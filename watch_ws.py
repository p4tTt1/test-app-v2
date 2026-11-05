import json, os, sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
URL = ("https://www.wiensued.at/wohnen/?dev=&city=Wien&search="
       "&space-from=&space-to=&room-from=&room-to=&state[]=sofort")
STATE = BASE / "state_ws.json"
TOPIC = os.environ["NTFY_TOPIC"]
HEADERS = {"User-Agent": "Mozilla/5.0 (private apartment watcher)"}
NO_RESULTS = "Keine passenden Objekte gefunden"
HIT_KEYWORD = "sofort"


def notify(title, message, url, priority=4):
    r = requests.post("https://ntfy.sh", timeout=20, json={
        "topic": TOPIC, "title": title, "message": message,
        "click": url, "priority": priority,
    })
    print("ntfy:", r.status_code, title)


def get_page():
    r = requests.get(URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def label(box, name):
    for line in box.select(".labtxtline"):
        lab, txt = line.select_one(".lab"), line.select_one(".txt")
        if lab and txt and lab.get_text(strip=True).lower() == name.lower():
            return txt.get_text(" ", strip=True)
    return ""


def parse(soup):
    hint = NO_RESULTS in soup.get_text(" ", strip=True)
    hits = {}
    for box in soup.select(".object-box"):
        if HIT_KEYWORD not in label(box, "Status").lower():
            continue
        a = box.select_one("a[href]")
        h4 = box.select_one(".address h4")
        p = box.select_one(".address p")
        addr = " ".join(x.get_text(strip=True) for x in (h4, p) if x)
        info = ", ".join(f"{n}: {label(box, n)}"
                         for n in ("Status", "Fläche in m²", "Zimmer",
                                   "Wohneinheiten") if label(box, n))
        url = a["href"] if a else ""
        hits[url or addr or str(len(hits))] = {
            "addr": addr or "?", "info": info, "url": url or URL}
    status = "hits" if hits else ("none" if hint else "unknown")
    return status, hits


def main():
    status, hits = parse(get_page())
    old = json.loads(STATE.read_text("utf-8")) if STATE.exists() else None
    old_hits = set(old["hits"]) if old else set()

    for key, h in hits.items():
        if key not in old_hits:
            notify("WS: Sofort verfügbar",
                   f'{h["addr"]}\n{h["info"]}', h["url"])

    if status == "unknown" and (old is None or old["status"] != "unknown"):
        notify("WS: Seite unklar",
               "Weder Hinweis noch Treffer erkannt. Seite prüfen.", URL)

    if old is None:
        notify("WS-Watcher aktiv", f"Status: {status}", URL, 3)

    STATE.write_text(json.dumps({"status": status, "hits": list(hits)},
                                ensure_ascii=False, indent=1), "utf-8")
    print("Status:", status, "| Treffer:", len(hits))


if __name__ == "__main__":
    main()