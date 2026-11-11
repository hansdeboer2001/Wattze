# Wattze

Wattze is een lokale wielerapp voor een smart trainer. De app draait op je pc,
verbindt via Bluetooth LE met je trainer (en optioneel een hartslagband en Zwift
Click) en toont alles in de browser op `http://localhost:8080`.

Alles blijft lokaal: geen account, geen cloud en geen abonnement. Alleen als je
zelf de Strava-koppeling gebruikt, gaan ritten naar Strava.

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
  (o.a. voor Strava of Garmin), plus het screenshot van elke rit.
- **Strava upload** (`/uploadstrava`) — ritten direct naar Strava sturen, of na
  een rit meteen met **Opslaan + Strava**. Zie [Strava-koppeling](#strava-koppeling).
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
| `/ritten` | Opgenomen ritten, TCX- en screenshot-download |
| `/uploadstrava` | Verbinden met Strava en ritten uploaden |
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

## Strava-koppeling

Wattze kan ritten direct naar je eigen Strava-account uploaden. De koppeling
gebruikt de Strava-app "Wattze", die door maximaal 10 rijders gebruikt kan
worden.

### Instellen

1. **Vraag het `.env`-bestand op bij Hans.** Daarin staan de client-ID en het
   client secret van de Wattze-app op Strava. Je krijgt het privé
   toegestuurd.
2. **Zet `.env` in de projectmap** (naast `wattze_game.py`). Gebruik je de
   gebouwde versie, zet hem dan naast `Wattze.exe`. Het bestand ziet er zo uit:

   ```
   STRAVA_CLIENT_ID=...
   STRAVA_CLIENT_SECRET=...
   ```

3. **Start Wattze (opnieuw).** De `.env` wordt alleen bij het opstarten gelezen.
4. **Open `http://localhost:8080/uploadstrava`** en klik op **Verbinden met
   Strava**. Log in met je *eigen* Strava-account en geef toestemming.

Daarna zie je na elke rit (in Ride en bij Blokjes) de knop **Opslaan + Strava**.
Oudere ritten upload je via `/uploadstrava`.

### Wat er naar Strava gaat

- **Titel:** `Wattze - <naam van de rit>`, of `Wattze - Rit` als de rit geen
  naam heeft.
- **Beschrijving:** wordt automatisch ingevuld met route, vermogen
  (gemiddeld, NP en max), hoogtemeters, hartslag, cadans en je KOM-tijden. Bij
  Blokjes staan de naam van de training en het gemeten vermogen erin.
- De rit wordt als **trainerrit** gemarkeerd.

Na de upload verschijnt **Open op Strava**, een link naar de nieuwe activiteit.

### Screenshot toevoegen

Na elke rit bewaart Wattze een screenshot: in Ride het spelscherm met je
ritgegevens, bij Blokjes de trainingsgrafiek. De Strava-API laat geen foto's
uploaden (dat mogen alleen partners zoals Zwift), dus het screenshot voeg je
zelf toe:

1. Klik na de upload op **Screenshot downloaden**. Dat kan ook later, via de
   link **Screenshot** bij Mijn ritten of op `/uploadstrava`.
2. Klik op **Open op Strava** en sleep het screenshot in de activiteit.

### Belangrijk

- **Deel `.env` niet en zet hem nooit in git.** Hij staat in `.gitignore`.
  Iedereen met het secret kan zich voordoen als de Wattze-app.
- **Open Wattze via `localhost`**, niet via een IP-adres. Strava stuurt je na
  het inloggen alleen terug naar `localhost`.
- Je Strava-login wordt per pc bewaard in `WattzeRitten/config.json`. Elke
  rijder uploadt dus naar zijn of haar eigen account.
- Wil je stoppen, trek dan de toegang in via Strava → Instellingen → Mijn apps.
  Dan komt er een plek vrij voor een ander.
- Zonder `.env` werkt Wattze gewoon, alleen zonder Strava.

## Online KOM-Klassementen

Open **Instellingen > Online KOM's**, of ga naar `/klassementen`. Maak een
rijdersprofiel aan en bewaar de geheime herstelsleutel in je
wachtwoordmanager. Daarmee herstel je hetzelfde profiel op een andere pc.
Er is geen e-mailadres of wachtwoord nodig. De sleutel blijft op de beheerpagina
beschikbaar totdat je bevestigt dat je hem hebt bewaard. Deel nooit de herstelsleutel.

- **Lokaal:** je bestaande KOM's op deze pc, ook zonder account of internet.
- **Algemeen:** openbare beste tijd per rijder; bekijken kan zonder profiel.
- **Vrienden:** jouw beste tijd en die van geaccepteerde vrienden die delen aanzetten.

Je vindt de drie tabs in de KOM-ranglijst in Ride. Stuur je **vriendencode** naar
een fietsmaatje. Die voert hem in en verstuurt een verzoek; jij accepteert dat
onder Ontvangen verzoeken. Beide rijders kunnen een vriendschap verwijderen.
De vriendencode is geen login en geeft op zichzelf geen toegang tot resultaten.

Algemeen en Vrienden hebben aparte toestemming, beide standaard uit. Alleen
nieuwe volledige KOM-pogingen worden ingestuurd, direct na de segmentfinish.
Het achteraf negeren van een rit verwijdert een al ingestuurde KOM niet.
Simulatie (ook eerder in dezelfde rit of tijdens een pauze), ERG-trainingen en
segmenten waarin je pauzeert worden niet ingestuurd. Bestaande/import-KOM's
worden niet geupload.

Alleen vaste routes met een op app en server overeenkomend hoogteprofiel en
expliciete segmentgrenzen worden ondersteund. Eigen en willekeurige routes
tellen niet mee. Gewijzigde route-/segmentversies krijgen een apart klassement.
Er wordt gecontroleerd op ongeldige waarden en onmogelijke snelheden, maar
resultaten zijn **zelfgerapporteerd, geen geverifieerde wedstrijdresultaten**.

Bij tijdelijke netwerk-/serverproblemen bewaart de app maximaal 1000 pogingen
in een lokale wachtrij. Gebruik **Opnieuw versturen** op `/klassementen`;
maximaal 20 pogingen per klik. Dubbele verzending maakt geen dubbele resultaten.
Voorkeuren wijzigen wist de wachtrij, zodat pogingen niet onverwacht met nieuwe
deelvoorkeuren worden gepubliceerd. Delen uitzetten verbergt eerdere tijden
voor die categorie definitief; opnieuw aanzetten deelt alleen nieuwe pogingen.
Ook een wijziging op een andere pc maakt nog niet verstuurde pogingen met de
oude deelvoorkeuren ongeldig.

Profiel, geheime sleutel en wachtrij staan afzonderlijk in `WattzeRitten`,
niet in algemene instellingen of API-snapshots. `wattze.com` gebruikt
`online-kom.json`. Elke andere server, inclusief de huidige standaard, gebruikt
`online-kom-<serverhash>.json`.
Bewaar dit bestand prive. Online beheer en de nieuwe lokale API werken uitsluitend
via `http://localhost:<poort>` op deze pc, niet via het LAN-adres op je telefoon.

### App En Server Updaten

De online API is een optionele uitbreiding onder `/api/kom/v1`. De app wijst
standaard naar `https://test.wattze.com`.
De serverbron staat in het afzonderlijke project `../wattze-server`.

- Een nieuwe app met een oude/offline server behoudt lokale ritten en KOM's.
  De online tabs tonen een melding dat de server moet worden bijgewerkt.
- Een nieuwe server behoudt de bestaande multiplayer-endpoints voor oudere apps.
- Voor **de nieuwe online klassementen** moeten app en server beide bijgewerkt zijn.
  Deploy bij voorkeur eerst de server, daarna de app.

De server bewaart profielen, vriendschappen en resultaten in SQLite. Gebruik een
blijvende datamap/volume en maak backups; zie `../wattze-server/README.md` voor
deploy-, privacy- en protocolinformatie. Een app-update is op zichzelf dus geen
reden om voortaan altijd de server tegelijk te moeten updaten.

### Online KOM-server

Multiplayer en online KOM's gebruiken standaard `https://test.wattze.com`.
Daarvoor is geen omgevingsvariabele nodig. Het bestaande testprofiel blijft in
`WattzeRitten/online-kom-6fb348ec3de40ee6.json`.

Een andere server stel je alleen in als je daarvan af wilt wijken. De URL moet
een HTTPS-adres zonder pad, inloggegevens of query zijn.

```powershell
$env:WATTZE_KOM_HOST = "https://wattze.com"
.\start_wattze.cmd
```

Die server krijgt zijn eigen profielbestand. Het testprofiel en de lokale
ritten blijven ongewijzigd. Weglaten van de variabele keert terug naar
`test.wattze.com`.

De variabele geldt voor processen die vanuit dit PowerShell-venster worden gestart;
een gewone dubbelklik neemt deze tijdelijke instelling niet over. Voor een exe
is een nieuwe build met deze wijziging nodig; start die ook vanuit dit venster.

De app identificeert online aanvragen met `User-Agent: Wattze/1.0 (+https://wattze.com)`.
Dit lost de waargenomen Cloudflare-blokkade van de standaard Python-client
(HTTP 403, fout 1010) op. Gebruik bij die fout de bijgewerkte broncode of een
nieuwe build en herstart de app; alleen de browserpagina verversen is niet genoeg.

## Ritten En Gegevens

Opgeslagen gegevens staan in `WattzeRitten/` (automatisch aangemaakt):

- `config.json` — apparaatkeuze, instellingen en je Strava-login.
- `rides.json` — index van opgenomen ritten.
- `koms.json` — KOM-tijden.
- `*.tcx` — de opgenomen ritten.
- `*.png` — het screenshot bij een rit (zelfde naam als de `.tcx`).

Deze map wordt niet in git opgenomen.

## Voor ontwikkelaars

### Sim (alleen voor testen)

Open de game via `http://localhost:8080/game?sim=1`. Er verschijnt dan een
knop **Sim** naast Stop. Daarmee stel je een vast vermogen (0–2000 W) en een
vaste cadans (0–200 rpm) in, die de game gebruikt in plaats van de trainer.
Zo kun je testen zonder te fietsen.

- Via gewoon `/game` (en het dashboard) is de knop niet te zien.
- Het opgenomen `.tcx`-bestand bevat het **echte** vermogen en de echte cadans
  van de trainer. Snelheid en afstand komen wel uit de simulatie.
- Wordt zo'n rit naar Strava geüpload, dan staat in de beschrijving
  "(deels) gesimuleerd vermogen gebruikt".

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
