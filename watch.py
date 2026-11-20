import json, os, sys, tempfile, time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import certifi
import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
LIST_URL = "https://www.nhg.at/angebot/?city=Wien"
SIGNUP_URL = "https://www.nhg.at/anmelden/?topId={tid}"
FOCUS_ID = os.environ.get("FOCUS_ID", "")
STATE = BASE / ("state_focus.json" if FOCUS_ID else "state.json")
TOPIC = os.environ["NTFY_TOPIC"]
HEADERS = {"User-Agent": "Mozilla/5.0 (private apartment watcher)"}

# Nur diese Projekte prüfen (leer = alle)
ALLOWED_IDS = set() #{"404"}
# Projekte, die ignoriert werden sollen (ID als Text, Kommentar zur Erinnerung)
BLOCKED_IDS = {
    "57",
    "67",
    "128",
    "148",
    "255",
    "280",
    "311",
    "328",
    "330",
    "333",
    "338",
    "401",
    "511",
    "610",
    "662",
    "757",
    "765",
    "816",
}
# Textteile in Name oder Adresse, die ein Projekt sperren
BLOCKED_TEXT = []

STATS = {"req": 0, "net": 0.0, "sleep": 0.0, "slow": []}


def pause(sec=0.2):
    STATS["sleep"] += sec
    time.sleep(sec)


def build_bundle():
    ca = Path(certifi.where()).read_text()
    ca += "\n" + (BASE / "nhg-intermediate.pem").read_text()
    tmp = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
    tmp.write(ca)
    tmp.close()
    return tmp.name


CA_BUNDLE = build_bundle()
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.verify = CA_BUNDLE


def notify(title, message, url, priority=4):
    r = requests.post("https://ntfy.sh", timeout=20, json={
        "topic": TOPIC, "title": title, "message": message,
        "click": url, "priority": priority,
    })
    print("ntfy:", r.status_code, title)


def get(url):
    t0 = time.perf_counter()
    r = SESSION.get(url, timeout=30)
    dt = time.perf_counter() - t0
    STATS["req"] += 1
    STATS["net"] += dt
    STATS["slow"].append((dt, url))
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
        tops[head["data-top-id"]] = {
            "info": strong.get_text(" ", strip=True) if strong else "",
            "where": " ".join(extra),
        }
    return tops


def check_signup(tid):
    """'open' = Formular, 'full' = Limit erreicht,
    'gone' = Einheit nicht mehr verfügbar, 'unknown' = nichts davon."""
    soup = get(SIGNUP_URL.format(tid=tid))
    text = soup.get_text(" ", strip=True)
    if "Anmeldelimit erreicht" in text:
        return "full"
    if soup.select_one("#anmelden_form"):
        return "open"
    if "Diese Einheit ist nicht mehr verfügbar" in text:
        return "gone"
    return "unknown"


def is_blocked(pid, p):
    if ALLOWED_IDS and pid not in ALLOWED_IDS:
        return True
    if pid in BLOCKED_IDS:
        return True
    hay = f'{p["name"]} {p["addr"]}'.lower()
    return any(t.lower() in hay for t in BLOCKED_TEXT)


def any_recognized(state):
    """True, wenn mindestens eine Anmeldeseite eindeutig erkannt wurde."""
    return any(t.get("signup") in ("open", "full", "gone")
               for p in state.values() for t in p.get("tops", {}).values())


def main():
    t_start = time.perf_counter()
    if FOCUS_ID:
        projects = {FOCUS_ID: {
            "name": "Fokus-Projekt", "addr": "",
            "url": f"https://www.nhg.at/Projekte/Details/?city=Wien&id={FOCUS_ID}",
        }}
    else:
        projects = list_projects()
        if not projects:
            sys.exit("Keine Projekte gefunden - Seite geändert oder blockiert?")

    first_run = not STATE.exists()
    old = {} if first_run else json.loads(STATE.read_text("utf-8"))
    new = {}
    checked = 0

    for pid, p in projects.items():
        if is_blocked(pid, p):
            continue
        pause()
        try:
            tops = parse_tops(get(p["url"]))
        except Exception as e:
            print(f"Fehler bei {p['name']}: {e}")
            new[pid] = old.get(pid) or {**p, "tops": {}}
            continue

        old_tops = old.get(pid, {}).get("tops", {})
        for tid, t in tops.items():
            prev = old_tops.get(tid, {}).get("signup")
            pause()
            try:
                status = check_signup(tid)
                checked += 1
            except Exception as e:
                print(f"Fehler bei Top {tid}: {e}")
                status = prev  # alten Stand behalten
            t["signup"] = status

            if status == "unknown":
                print(f"Top {tid}: Anmeldeseite unklar")
            if status == "open" and prev != "open":
                notify("Wohnung frei - Anmeldung möglich",
                       f'{t["info"]}\n{t["where"]}',
                       SIGNUP_URL.format(tid=tid))

        p["tops"] = tops
        new[pid] = p

    if checked and not any_recognized(new) and (first_run or any_recognized(old)):
        notify("NHG: Anmeldeseiten unklar",
               "Keine Anmeldeseite zeigt Formular oder Limit-Hinweis. Seite prüfen.",
               LIST_URL)

    if first_run:
        n = sum(len(p["tops"]) for p in new.values())
        notify("NHG-Fokus aktiv" if FOCUS_ID else "NHG-Watcher aktiv",
               f"{len(new)} Projekte, {n} Wohnungen erfasst", LIST_URL, 3)

    STATE.write_text(json.dumps(new, ensure_ascii=False, indent=1), "utf-8")
    print(f"Anmeldeseiten geprüft: {checked}")

    total = time.perf_counter() - t_start
    rest = total - STATS["net"] - STATS["sleep"]
    print(f"Laufzeit: {total:.1f}s | Anfragen: {STATS['req']} | "
          f"Netzwerk: {STATS['net']:.1f}s | Pausen: {STATS['sleep']:.1f}s | "
          f"Rest (Parsen etc.): {rest:.1f}s")
    for dt, url in sorted(STATS["slow"], reverse=True)[:5]:
        print(f"  {dt:.2f}s  {url}")


if __name__ == "__main__":
    main()
