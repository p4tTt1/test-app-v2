import json, os, re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
SITE = "https://www.oevw.at"
SEARCH_URL = SITE + "/suche/filter"
PAYLOAD = {
    "unittypes": "apartment", "legalform": "rent", "zips": "",
    "rooms": [], "area_from": "50", "area_to": "", "price_to": "900",
    "available_immediately": "", "only_new": "",
}
WIEN_RE = re.compile(r"\b1[0-2]\d0\s+Wien\b")
STATE = BASE / "state_oevw.json"
TOPIC = os.environ["NTFY_TOPIC"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (private apartment watcher)",
    "Accept": "text/html,application/json,*/*",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": SITE + "/suche",
}


def notify(title, message, url, priority=4):
    r = requests.post("https://ntfy.sh", timeout=20, json={
        "topic": TOPIC, "title": title, "message": message,
        "click": url, "priority": priority,
    })
    print("ntfy:", r.status_code, title)


def find_html(data):
    if isinstance(data, str):
        return data if "thumblist" in data or "searchresults" in data else None
    if isinstance(data, dict):
        data = list(data.values())
    if isinstance(data, list):
        for v in data:
            found = find_html(v)
            if found:
                return found
    return None


def fetch_html():
    s = requests.Session()
    s.headers.update({
        "User-Agent": HEADERS["User-Agent"],
        "Accept-Language": "de-AT,de;q=0.9,en;q=0.8",
    })
    first = s.get(SITE + "/suche", timeout=30)
    first.raise_for_status()

    m = re.search(r'csrfToken\s*=\s*"([^"]+)"', first.text)
    if not m:
        raise RuntimeError("CSRF-Token auf /suche nicht gefunden")

    r = s.post(SEARCH_URL, json=PAYLOAD, timeout=30, headers={
        "Accept": "text/html,application/json,*/*",
        "X-CSRF-Token": m.group(1),
        "X-Requested-With": "XMLHttpRequest",
        "Origin": SITE,
        "Referer": SITE + "/suche",
    })
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        return r.text
    html = find_html(data)
    if html is None:
        raise RuntimeError("Antwort ist JSON, enthält aber keine Ergebnisliste")
    return html


def text_of(el):
    return el.get_text(" ", strip=True) if el else ""


def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("#searchresults") or soup
    heading = root.select_one("h1.thumbsection__heading")
    lis = root.select("li.thumblist__item")
    if heading is None and not lis:
        return "unknown", {}, None

    total = None
    if heading:
        m = re.search(r"\d+", heading.get_text())
        total = int(m.group()) if m else None

    items = {}
    for li in lis:
        a = li.select_one("a[href]")
        if not a:
            continue
        href = a["href"]
        m = re.search(r"/suche/(\d+)", href)
        key = m.group(1) if m else href
        items[key] = {
            "badge": text_of(li.select_one(".thumb__badge")),
            "info": text_of(li.select_one(".thumb__info")),
            "name": text_of(li.select_one(".thumb__heading")),
            "details": [text_of(x) for x in li.select(
                ".thumb__subheading__item, .thumb__text__item")],
            "url": SITE + href if href.startswith("/") else href,
        }
    if total and not items:
        return "unknown", {}, total
    return "ok", items, total


def signature(v):
    return [v["badge"]] + v["details"]


def describe(v):
    return " | ".join(x for x in signature(v) if x)


def main():
    status, found, total = parse(fetch_html())
    old = json.loads(STATE.read_text("utf-8")) if STATE.exists() else None

    if status == "unknown":
        print("Status: unknown")
        if old is None or old.get("status") != "unknown":
            notify("ÖVW: Seite unklar",
                   "Ergebnisliste nicht erkannt. Seite prüfen.",
                   SITE + "/suche")
        if old is not None:
            old["status"] = "unknown"
            STATE.write_text(json.dumps(old, ensure_ascii=False, indent=1), "utf-8")
        return

    if total is not None and total != len(found):
        print(f"WARNUNG: Überschrift sagt {total}, gefunden {len(found)}")

    eligible = {k: v for k, v in found.items() if WIEN_RE.search(v["info"])}
    old_items = old["items"] if old else {}

    if old is None:
        notify("ÖVW-Watcher aktiv",
               f"{len(eligible)} Wiener Wohnungen erfasst "
               f"({len(found)} Ergebnisse gesamt)", SITE + "/suche", 3)
    else:
        for key, v in eligible.items():
            prev = old_items.get(key)
            if prev is None:
                notify("ÖVW: Neue Wohnung in Wien",
                       f'{v["info"]}\n{v["name"]}\n{describe(v)}', v["url"])
            elif signature(prev) != signature(v):
                notify("ÖVW: Wohnung geändert",
                       f'{v["name"]}\nVorher: {describe(prev)}\n'
                       f'Jetzt: {describe(v)}', v["url"], 3)

    STATE.write_text(json.dumps({"status": "ok", "items": eligible},
                                ensure_ascii=False, indent=1), "utf-8")
    print(f"Status: ok | Wien: {len(eligible)} von {len(found)}")


if __name__ == "__main__":
    main()