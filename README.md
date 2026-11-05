# Wattze

Wattze is een lokale wielerapp voor een smart trainer. De app draait op je pc,
verbindt via Bluetooth LE met je trainer (en optioneel een hartslagband en Zwift
Click) en toont alles in de browser op `http://localhost:8080`.

Alles blijft lokaal: geen account, geen cloud en geen abonnement.

## Wat kun je ermee

- **Dashboard** (`/`) — live vermogen, cadans, snelheid en hartslag, met grafieken
  en snelkoppelingen naar de andere schermen.
- **Ride** (`/game`) — een rit door een gegenereerd landschap. De app stuurt de
  helling naar de trainer, toont KOM's, een ghost van je beste rit en virtuele
  versnellingen. Er zijn vaste routes en je kunt eigen routes toevoegen.
- **Blokjes** (`/blokjes`) — intervaltrainingen op vermogen (ERG). Kies een
  training of bouw er zelf een en start hem in Ride.
- **Live** (`/live`) — groot, rustig live-scherm voor naast de fiets.
- **Mijn ritten** (`/ritten`) — opgenomen ritten bekijken en downloaden als TCX
  (o.a. voor Strava of Garmin).
- **Apparaten** (`/start`) — trainer, hartslagmeter en Zwift Click kiezen, plus
  een QR-code voor de telefoonafstandsbediening.
- **Afstandsbediening** (`/afstandsbediening`) — versnellingen en ventilator
  bedienen vanaf je telefoon (zelfde Wi-Fi).
- **Instellingen** (`/instellingen`) — naam, gewicht, tekstgrootte, aantal
  versnellingen en de ventilator.
- **KOM-klassement** — tijden per klim worden lokaal bewaard.
- **Multiplayer** — optioneel samen rijden via een server (standaard uit).

## Vereisten

- Windows 10/11 (Bluetooth LE via WinRT).
- Python 3.11 of nieuwer (3.12 aanbevolen).
- Een smart trainer die FTMS ondersteunt (bijv. Wahoo KICKR, Tacx NEO).
- Optioneel: een BLE-hartslagband en/of Zwift Click.
- Optioneel: een ESP32 met ESPHome voor de ventilator.

Installeer de Python-pakketten:

```powershell
python -m pip install -r requirements.txt
```

## Starten

Dubbelklik op `start_wattze.cmd`, of gebruik PowerShell:

```powershell
.\start_wattze.ps1          # herstart de server en opent de browser
.\start_wattze.ps1 stop     # stopt de server
```

De server luistert standaard op poort 8080. Een andere poort kies je met de
omgevingsvariabele `KICKR_PORT`:

```powershell
$env:KICKR_PORT = 8090
.\start_wattze.ps1
```

Handmatig starten (handig bij foutmeldingen):

```powershell
python wattze_game.py
```

Open daarna `http://localhost:8080/start` om je apparaten te kiezen.

## Schermen

| Pad | Functie |
| --- | --- |
| `/` | Dashboard met live waarden en grafieken |
| `/live` | Groot live-scherm |
| `/game` | Ride: rit met hellingbesturing, routes en KOM's |
| `/blokjes` | Intervaltrainingen (ERG) maken en starten |
| `/ritten` | Opgenomen ritten en TCX-download |
| `/start` | Apparaten kiezen en telefoon-QR |
| `/afstandsbediening` (of `/remote`) | Telefoonafstandsbediening |
| `/instellingen` | Instellingen |
| `/api`, `/events` | JSON- en SSE-interface voor de pagina's |

## Trainers, sensoren en versnellingen

- **Trainer** — FTMS Indoor Bike Data, FTMS Control Point en/of Cycling Power.
  Wahoo KICKR wordt daarnaast via het Wahoo-protocol aangestuurd.
- **Hartslag** — elke BLE-hartslagmeter met de standaard Heart Rate Service.
- **Zwift Click** — virtuele versnellingen; ook te bedienen met de knoppen in
  Ride en de telefoonafstandsbediening.
- **Ventilator** — via een ESP32 met ESPHome (`esphome/esp32-relays.yaml`). De
  app schakelt de ventilator op basis van je vermogen; drempels en uitdraaitijd
  stel je in bij Instellingen.

## Routes

Routes staan in `routes/*.json`. Elke route bevat een hoogteprofiel (`ele`),
een stapgrootte in meters (`step`), een naam en eventueel KOM-segmenten
(`segs`).

Een GPX-rit omzetten naar een route:

```powershell
python gpx_to_route.py route_GPX\rit.gpx routes\mijn-rit.json --step 25
```

Zonder argumenten opent het script een bestandsvenster en schrijft het resultaat
naar `routes/<naam>.json`. Bestaande routes behouden hun id, naam en KOM's;
alleen het hoogteprofiel wordt bijgewerkt.

## Ritten en gegevens

Opgeslagen gegevens staan in `WattzeRitten/` (automatisch aangemaakt):

- `config.json` — apparaatkeuze en instellingen.
- `rides.json` — index van opgenomen ritten.
- `koms.json` — KOM-tijden.
- `*.tcx` — de opgenomen ritten.

Deze map wordt niet in git opgenomen.

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Windows-app bouwen

```powershell
.\build_wattze.ps1
```

Dit maakt met PyInstaller (via `wattze.spec`) een zelfstandige map
`dist\Wattze\Wattze.exe` en een zip `dist\Wattze.zip` die je kunt delen.

## Projectstructuur

```
wattze_game.py          startpunt: start de server en achtergrondtaken
wattze/                 BLE, HTTP, opslag, ritten en telemetrie
pages/                  HTML/CSS/JS-schermen
routes/                 routebestanden met hoogteprofielen
route_GPX/              bronbestanden voor gpx_to_route.py (niet in git)
tests/                  unittest-tests
esphome/                ESPHome-configuratie voor de ventilator
scenery-preview/        lokale previewpagina's (niet in git)
start_wattze.*          start- en stopscripts
build_wattze.*          PyInstaller-build
gpx_to_route.py         GPX naar route-JSON
```
