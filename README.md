# NHG-Wohnungswatcher

Kleines Python-Tool, das die Wiener Angebote von [nhg.at](https://www.nhg.at/angebot/?city=Wien) regelmäßig prüft und per Push-Nachricht (ntfy) meldet, sobald eine Wohnung **verfügbar** ist **und** die Anmeldung dafür tatsächlich **möglich** ist.

---

## Inhalt

1. [Das Problem](#das-problem)
2. [Wie es funktioniert](#wie-es-funktioniert)
3. [Logik im Detail](#logik-im-detail)
4. [Dateien im Projekt](#dateien-im-projekt)
5. [Einrichtung lokal](#einrichtung-lokal)
6. [Testen](#testen)
7. [Betrieb auf GitHub Actions](#betrieb-auf-github-actions)
8. [Wartung](#wartung)
9. [Fehlersuche](#fehlersuche)
10. [Grenzen und Hinweise](#grenzen-und-hinweise)

---

## Das Problem

Freie Wohnungen sind bei NHG oft nach kurzer Zeit nicht mehr nutzbar: Entweder sind sie "reserviert", oder die Anmeldung scheitert mit "Anmeldelimit erreicht". Die E-Mails der eingebauten Suchagenten kommen zu spät. Dieses Tool prüft selbst in kurzen Abständen und meldet nur echte Chancen, also Wohnungen, bei denen das Anmeldeformular tatsächlich erreichbar ist.

---

## Wie es funktioniert

Jeder Lauf von `watch.py` besteht aus vier Schritten:

```
1. Projektliste holen      https://www.nhg.at/angebot/?city=Wien
        │
        ▼
2. Pro Projekt die Detailseite holen und alle Wohnungen ("Tops") auslesen
        │                    (ID, Daten, Status-Badge)
        ▼
3. Pro Wohnung mit Status "verfügbar": Anmeldeseite prüfen
        │                    https://www.nhg.at/anmelden/?topId=<ID>
        ▼
4. Mit dem letzten Stand (state.json) vergleichen → ntfy-Nachricht bei Änderung
```

Der Zustand wird in `state.json` gespeichert. Dadurch gibt es Nachrichten nur bei **Änderungen**, nicht bei jedem Lauf.

Das Tool liest ausschließlich Seiten (HTTP GET). Es sendet **nie** ein Formular ab und meldet dich nirgends an. Die Anmeldung machst du selbst, über den Link in der Nachricht.

---

## Logik im Detail

### Schritt 1: Projekte

Die Listenseite enthält pro Projekt eine Kachel (`.tile-project`). Daraus werden Projekt-ID (aus dem Link-Parameter `id`), Name, Adresse und Detail-URL gelesen. Findet das Skript **keine** Projekte, bricht es mit Fehler ab (Seite geändert oder blockiert), statt stillschweigend nichts zu tun.

### Schritt 2: Wohnungen pro Projekt

Auf der Detailseite liegt jede Wohnung in einem `.dropdown` innerhalb von `.section-offer`. Gelesen werden:

| Feld        | Quelle im HTML                                  |
|-------------|-------------------------------------------------|
| ID          | `data-top-id` am `header.dropdown__head`        |
| Kurzinfo    | `.dropdown__title strong` (Zimmer, m², Kosten)  |
| Lage        | weitere `span` im `.dropdown__title`            |
| Status      | `.dropdown__badge` (z. B. "reserviert")         |

Es gibt drei Fälle, die das Skript unterscheidet:

| Fall | Seite zeigt                         | Ergebnis                         |
|------|-------------------------------------|----------------------------------|
| 1    | `div.no-results`, keine Wohnungen   | nichts zu tun                    |
| 2    | Badge "reserviert" oder "vergeben"  | `available = false`, ignoriert   |
| 3    | Badge "verfügbar" (oder anderer Text) | `available = true`, weiter zu Schritt 3 |

Als "nicht verfügbar" gilt, was in der Konstante `BLOCKED` steht (`reserviert`, `vergeben`). Jeder andere Badge-Text, auch ein fehlender, wird vorsichtshalber als **verfügbar** gewertet. Lieber ein Fehlalarm als eine verpasste Wohnung.

Die Ausführlichkeit der Detail-Infos (Anmelden-Button, Exposé) wird auf der Website erst beim Aufklappen nachgeladen. Das Skript braucht sie nicht, weil die ID genügt, um die Anmeldeseite direkt aufzurufen.

### Schritt 3: Anmeldeseite prüfen

Für jede verfügbare Wohnung wird `https://www.nhg.at/anmelden/?topId=<ID>` abgerufen, ohne Login, genau wie in einem anonymen Browserfenster. Das Ergebnis ist einer von drei Zuständen:

| Zustand   | Erkennungsmerkmal                          | Bedeutung                         |
|-----------|--------------------------------------------|-----------------------------------|
| `full`    | Text "Anmeldelimit erreicht" auf der Seite | keine Vormerkungen mehr möglich   |
| `open`    | Formular `#anmelden_form` vorhanden        | Anmeldung möglich                 |
| `unknown` | weder noch                                 | Seite hat sich vermutlich geändert|

Das Skript ruft die Anmeldeseite nur für Wohnungen mit Status "verfügbar" ab, nicht für alle Wohnungen und nicht für reservierte. Das hält die Last auf nhg.at minimal. Zwischen den Anfragen wartet es jeweils eine Sekunde.

### Schritt 4: Wann kommt eine Nachricht?

Verglichen wird der **neue** Anmeldezustand einer Wohnung mit dem **gespeicherten** aus dem letzten Lauf:

| Gespeichert (alt)       | Jetzt (neu) | Nachricht                                  |
|-------------------------|-------------|--------------------------------------------|
| neu / nicht verfügbar   | `open`      | "Wohnung frei - Anmeldung möglich"         |
| `full`                  | `open`      | "Wohnung frei - Anmeldung möglich" (Platz wurde wieder frei) |
| neu / nicht verfügbar   | `unknown`   | "Wohnung verfügbar (Anmeldestatus unklar)" |
| `open`                  | `open`      | keine (schon gemeldet)                     |
| beliebig                | `full`      | keine                                      |

Das heißt: Jede Wohnung meldet sich **einmal pro Wechsel** auf `open`. Wird sie zwischendurch wieder voll und später erneut frei, kommt eine neue Nachricht.

Beim allerersten Lauf (noch keine `state.json`) kommt zusätzlich "NHG-Watcher aktiv" mit der Zahl der erfassten Projekte und Wohnungen. Danach nicht mehr.

### Fehlerverhalten

* Schlägt der Abruf **eines** Projekts fehl, wird der alte Stand dieses Projekts behalten und der Lauf geht mit den übrigen weiter.
* Schlägt der Abruf der **Anmeldeseite** fehl, wird der alte Anmeldezustand beibehalten. Dadurch entsteht kein falscher Alarm durch einen kurzen Netzwerkfehler.
* Schlägt der Abruf der **Projektliste** fehl, bricht der Lauf mit Fehler ab. Auf GitHub bekommst du dann automatisch eine Fehler-Mail.

---

## Dateien im Projekt

| Datei                      | Zweck                                                            |
|----------------------------|------------------------------------------------------------------|
| `watch.py`                 | das eigentliche Skript                                           |
| `nhg-intermediate.pem`     | Zwischenzertifikat von nhg.at (siehe unten)                      |
| `get_intermediate.py`      | Hilfsskript, um das Zwischenzertifikat neu zu holen              |
| `requirements.txt`         | Python-Abhängigkeiten                                            |
| `state.json`               | zuletzt bekannter Zustand (wird vom Skript erzeugt)              |
| `.github/workflows/watch.yml` | GitHub-Actions-Workflow für den Dauerbetrieb                  |
| `.gitignore`               | schließt `.venv/` und `__pycache__/` aus                         |

### Warum ein eigenes Zwischenzertifikat?

nhg.at liefert sein Zwischenzertifikat nicht mit (SSL Labs: "chain incomplete"). Browser laden es selbst nach, Python (`requests`) nicht, und meldet `CERTIFICATE_VERIFY_FAILED`. Deshalb liegt `nhg-intermediate.pem` im Projekt. `watch.py` hängt es beim Start an die Standard-Zertifikate (`certifi`) an und nutzt dieses Bündel für alle Abrufe. Die Zertifikatsprüfung bleibt dabei vollständig aktiv.

`requirements.txt`:

```
requests
beautifulsoup4
```

---

## Einrichtung lokal

Voraussetzung: Python 3.10 oder neuer.

```powershell
cd apartment-watcher
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### Push-Nachrichten mit ntfy

1. App **ntfy** installieren (Android: Play Store/F-Droid, iOS: App Store).
2. In der App auf **+** tippen und ein Topic mit schwer erratbarem Namen abonnieren, z. B. `nhg-<irgendwas>-x7k29q`. Jeder, der den Namen kennt, kann mitlesen, deshalb kein einfaches Wort verwenden.
3. Topic als Umgebungsvariable setzen (gilt nur im aktuellen Terminal-Fenster):

```powershell
$env:NTFY_TOPIC="dein-topic-name"        # macOS/Linux: export NTFY_TOPIC=dein-topic-name
```

Prüfen, ob sie gesetzt ist: `$env:NTFY_TOPIC`

### Start

```powershell
python watch.py
```

Erwartet: `state.json` entsteht, auf dem Handy erscheint "NHG-Watcher aktiv". Jeder weitere Lauf ist still, solange sich nichts ändert.

---

## Testen

Da nicht immer eine Wohnung verfügbar ist, gibt es mehrere Wege, die Logik zu prüfen.

### A. Push-Nachricht isoliert

```powershell
Invoke-RestMethod -Method Post -Uri "https://ntfy.sh/$env:NTFY_TOPIC" -Body "Test"
```

Kommt "Test" am Handy an, sind App und Topic richtig eingerichtet.

### B. Anmeldeseite einer bestimmten Wohnung prüfen

```powershell
python -c "import watch; print(watch.check_signup('25499'))"
```

Ergebnis: `full`, `open` oder `unknown`. Die ID (`topId`) steht im HTML einer Wohnung als `data-top-id`.

### C. Wohnungsliste eines Projekts ansehen

```powershell
python -c "import json, watch; print(json.dumps(watch.parse_tops(watch.get('https://www.nhg.at/Projekte/Details/?id=372')), indent=1, ensure_ascii=False))"
```

### D. Vollständigen Alarm testen (auch ohne freie Wiener Wohnung)

1. In `watch.py` vorübergehend `LIST_URL = "https://www.nhg.at/angebot/"` setzen (alle Bundesländer statt nur Wien).
2. `state.json` löschen und `python watch.py` starten. Alle Wohnungen mit Status `open` lösen jetzt eine Nachricht aus.
3. **Danach `LIST_URL` wieder auf `...?city=Wien` zurücksetzen** und `state.json` erneut löschen.

### E. Übergang `full` → `open` testen

In `state.json` bei einer Wohnung, die aktuell `open` ist, den Wert `"signup": "open"` auf `"full"` ändern und erneut starten. Es muss eine Nachricht kommen.

### F. "Kein Alarm bei unveränderter Lage"

Zweimal hintereinander starten. Der zweite Lauf darf keine Nachricht senden.

---

## Betrieb auf GitHub Actions

Läuft kostenlos in der Cloud, ohne eigenen Server.

### Einrichtung

1. **Öffentliches** GitHub-Repository anlegen. In öffentlichen Repos sind Actions-Minuten kostenlos, bei privaten nicht ausreichend für ein 5-Minuten-Intervall.
2. Folgende Dateien hochladen: `watch.py`, `nhg-intermediate.pem`, `get_intermediate.py`, `requirements.txt`, `.gitignore`, `.github/workflows/watch.yml`.
3. Die **lokale `state.json` nicht hochladen** (vorher löschen). Der Workflow legt sie selbst an.
4. Unter *Settings → Secrets and variables → Actions → New repository secret* das Secret `NTFY_TOPIC` mit deinem Topic-Namen anlegen. Das Topic steht so nie im öffentlichen Code.
5. Im Reiter *Actions* den Workflow **nhg-watch** einmal manuell starten (*Run workflow*). Es sollte "NHG-Watcher aktiv" aufs Handy kommen.

### Workflow `.github/workflows/watch.yml`

```yaml
name: nhg-watch
on:
  schedule:
    - cron: "*/5 * * * *"
  workflow_dispatch:
permissions:
  contents: write
concurrency: nhg
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: python watch.py
        env:
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
      - run: |
          git config user.name "bot"
          git config user.email "bot@users.noreply.github.com"
          git add state.json
          git diff --cached --quiet || (git commit -m "state" && git push)
```

Der letzte Schritt speichert `state.json` im Repository, damit der Zustand zwischen den Läufen erhalten bleibt (GitHub-Läufe haben sonst keinen Speicher). `concurrency` verhindert, dass zwei Läufe gleichzeitig laufen.

### Wichtig zu wissen

* **Verzögerung:** GitHubs Zeitplan ist nicht minutengenau. Üblich sind 5 bis 15 Minuten, bei hoher Last mehr. Für Reaktionszeiten im Minutenbereich braucht es einen eigenen Rechner (z. B. Raspberry Pi) mit einem Intervall von einer Minute.
* **Automatische Abschaltung:** GitHub kann geplante Workflows in Repos ohne Aktivität deaktivieren. Dann im Reiter *Actions* wieder aktivieren.
* **IP-Sperre:** Falls nhg.at Anfragen von GitHub-Servern blockiert, zeigt der erste manuelle Lauf einen Fehler (z. B. 403). Dann bleibt nur ein anderer Host (eigener Rechner, kleiner VPS).
* **Fehler-Mails:** Schlägt ein Lauf fehl, schickt GitHub automatisch eine E-Mail an dich.

---

## Wartung

| Situation | Maßnahme |
|-----------|----------|
| `CERTIFICATE_VERIFY_FAILED` taucht plötzlich wieder auf | nhg.at hat vermutlich den Zertifikatsaussteller gewechselt: `pip install cryptography`, `python get_intermediate.py`, neue `nhg-intermediate.pem` ins Repo |
| "Keine Projekte gefunden" | Website-Struktur geändert, Selektoren in `list_projects()` anpassen |
| Dauerhaft Nachrichten "Status unklar" | Anmeldeseite hat sich geändert, Erkennung in `check_signup()` anpassen |
| Zusätzliche Statuswörter | `BLOCKED` in `watch.py` ergänzen (z. B. weitere Wörter für "nicht verfügbar") |
| Neue Stadt/Region | `LIST_URL` ändern, z. B. `?city=Wien` durch eine andere Stadt ersetzen |
| Abfrageintervall ändern | `cron`-Zeile im Workflow (`*/5` = alle 5 Minuten) |
| Alles zurücksetzen | `state.json` löschen (der nächste Lauf meldet wieder alle aktuell offenen Wohnungen) |

---

## Fehlersuche

| Symptom | Mögliche Ursache |
|---------|------------------|
| Keine Nachricht beim Start | `state.json` existiert schon (die Startmeldung kommt nur beim ersten Lauf); Topic-Name in App und Variable stimmt nicht überein; Benachrichtigungen für die App sind deaktiviert |
| `KeyError: 'NTFY_TOPIC'` | Umgebungsvariable im aktuellen Terminal nicht gesetzt |
| `FileNotFoundError ... nhg-intermediate.pem` | Datei liegt nicht im selben Ordner wie `watch.py` |
| `SSLError` | siehe Zertifikat-Abschnitt oben |
| HTTP 403/429 | nhg.at blockiert oder begrenzt die Anfragen; Intervall erhöhen oder anderen Host verwenden |
| Änderungen am Skript werden nicht übernommen | Datei vor dem Start nicht gespeichert |

---

## Grenzen und Hinweise

* **Keine automatische Anmeldung.** Das Tool meldet dich bewusst nicht selbst an. Das wäre fehleranfällig, kann gegen die Nutzungsbedingungen verstoßen und lässt dich nicht prüfen, was du eigentlich buchst.
* **Verfügbar heißt nicht bekommen.** Das Tool erhöht die Chance, rechtzeitig zu sein. Ob die Anmeldung zum Zug kommt, entscheidet NHG.
* **Anmeldeseite wird anonym geprüft.** Eingeloggt kann die Seite für dich anders aussehen. Der Link in der Nachricht führt zur Anmeldeseite, dort loggst du dich gewohnt ein.
* **Fremde Website.** Die Auswertung beruht auf dem derzeitigen HTML von nhg.at. Ändert NHG das Layout, muss das Skript angepasst werden. Das Abrufintervall ist bewusst moderat gewählt, bitte nicht deutlich kürzer setzen, als nötig.
* **Rechtliches.** Prüfe die Nutzungsbedingungen und die `robots.txt` von nhg.at, bevor du das Tool dauerhaft betreibst. Die Verantwortung für den Einsatz liegt bei dir.