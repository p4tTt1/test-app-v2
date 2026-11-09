import json, os, sys, tempfile, time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import certifi
import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
LIST_URL = "https://www.nhg.at/angebot/?city=Wien&area=50" # param rent=900 doesn't reliably work
SIGNUP_URL = "https://www.nhg.at/anmelden/?topId={tid}"
STATE = BASE / "state.json"
TOPIC = os.environ["NTFY_TOPIC"]
HEADERS = {"User-Agent": "Mozilla/5.0 (private apartment watcher)"}
BLOCKED = {"reserviert", "vergeben"}

# Projekte, die ignoriert werden sollen (ID als Text, Kommentar zur Erinnerung)
BLOCKED_IDS = {
    "765",
    "311",
    "338",
    "255",
    "67",
    "330",
    "328",
    "816"
}

# Textteile in Name oder Adresse (ohne Beachtung der Groß-/Kleinschreibung)
BLOCKED_TEXT = []


def is_blocked(pid, p):
    if pid in BLOCKED_IDS:
        return True
    hay = f'{p["name"]} {p["addr"]}'.lower()
    return any(t.lower() in hay for t in BLOCKED_TEXT)


def build_bundle():
    ca = Path(certifi.where()).read_text()
    ca += "\n" + (BASE / "nhg-intermediate.pem").read_text()
    tmp = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
    tmp.write(ca)
    tmp.close()
    return tmp.name


CA_BUNDLE = build_bundle()


def notify(title, message, url, priority=4):
    r = requests.post("https://ntfy.sh", timeout=20, json={
        "topic": TOPIC, "title": title, "message": message,
        "click": url, "priority": priority,
    })
    print("ntfy:", r.status_code, title)


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30, verify=CA_BUNDLE)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def list_projects():
    projects = {}
    for tile in get(LIST_URL).select(".tile-project"):
        a = tile.select_one("a.tile__link")
        if not a:
            continue
        pid = parse_qs(urlparse(a["href"]).query).get("id", [None])[0]
        name = tile.select_one("h5")
        addr = tile.select_one(".tile__head p")
        projects[pid] = {
            "name": name.get_text(strip=True) if name else "?",
            "addr": addr.get_text(strip=True) if addr else "",
            "url": a["href"],
        }
    return projects


def parse_tops(soup):
    offer = soup.select_one(".section-offer")
    if offer is None:
        raise RuntimeError("Bereich .section-offer nicht gefunden")
    tops = {}
    for d in offer.select(".dropdown"):
        head = d.select_one("header.dropdown__head")
        if head is None or not head.get("data-top-id"):
            continue
        strong = head.select_one(".dropdown__title strong")
        extra = [s.get_text(" ", strip=True)
                 for s in head.select(".dropdown__title > span")]
        badge = head.select_one(".dropdown__badge")
        badge_text = badge.get_text(strip=True) if badge else ""
        tops[head["data-top-id"]] = {
            "info": strong.get_text(" ", strip=True) if strong else "",
            "where": " ".join(extra),
            "badge": badge_text,
            "available": badge_text.lower() not in BLOCKED,
        }
    return tops


def check_signup(tid):
    """'open' = Formular da, 'full' = Limit erreicht, 'unknown' = weder noch."""
    soup = get(SIGNUP_URL.format(tid=tid))
    if "Anmeldelimit erreicht" in soup.get_text(" ", strip=True):
        return "full"
    if soup.select_one("#anmelden_form"):
        return "open"
    return "unknown"


def main():
    projects = list_projects()
    if not projects:
        sys.exit("Keine Projekte gefunden - Seite geändert oder blockiert?")

    first_run = not STATE.exists()
    old = {} if first_run else json.loads(STATE.read_text("utf-8"))
    new = {}

    for pid, p in projects.items():
        if is_blocked(pid, p):
            continue
        time.sleep(1)
        try:
            tops = parse_tops(get(p["url"]))
        except Exception as e:
            print(f"Fehler bei {p['name']}: {e}")
            new[pid] = old.get(pid) or {**p, "tops": {}}
            continue

        old_tops = old.get(pid, {}).get("tops", {})
        for tid, t in tops.items():
            prev = old_tops.get(tid, {}).get("signup")
            t["signup"] = None
            if not t["available"]:
                continue
            time.sleep(1)
            try:
                status = check_signup(tid)
            except Exception as e:
                print(f"Fehler bei Top {tid}: {e}")
                status = prev  # alten Stand behalten
            t["signup"] = status

            if status in ("open", "unknown") and status != prev:
                title = ("Wohnung frei - Anmeldung möglich" if status == "open"
                         else "Wohnung verfügbar (Anmeldestatus unklar)")
                notify(title,
                       f'{t["info"]}\n{t["where"]}',
                       SIGNUP_URL.format(tid=tid))

        p["tops"] = tops
        new[pid] = p

    if first_run:
        n = sum(len(p["tops"]) for p in new.values())
        notify("NHG-Watcher aktiv",
               f"{len(new)} Projekte, {n} Wohnungen erfasst", LIST_URL, 3)

    STATE.write_text(json.dumps(new, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
