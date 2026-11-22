# Todolijst

## Klassementen: Persoonlijk En Online

Status: lokaal archief voor KOM-segmenten en uitgereden routes is geimplementeerd. Het online overzicht wacht op een serverendpoint.

Een extra Klassementen-tegel op het startscherm voor het persoonlijke KOM-archief
en de online klassementen, zonder eerst een route in Ride te openen.
Hiervoor zijn zowel appwijzigingen als een serverupdate nodig.

### Overzicht En Bediening

- [x] Een extra Klassementen-tegel en zelfstandige overzichtspagina toevoegen.
- [x] Tabs Mijn KOM's, Algemeen en Vrienden maken.
- [x] Zoeken en filteren op route, KOM-segment, rijder en periode toevoegen waar van toepassing.
- [x] Bij Mijn KOM's per segment de beste tijd, bijbehorend vermogen, aantal pogingen en laatste datum tonen.
- [x] Alle bewaarde eigen pogingen in een detailweergave tonen, sorteerbaar op datum en tijd.
- [x] Het huidige profielbeheer bereikbaar houden via Profiel en vrienden op een eigen subpagina.
- [x] De bestaande ranglijsten tijdens Ride behouden.

### Persoonlijk Archief

- [x] Alle toekomstige volledige lokale KOM-pogingen archiveren, ook buiten de huidige twintig snelste.
- [x] Bestaande lokale records meenemen en duidelijk maken dat eerder verwijderde pogingen niet automatisch terugkomen.
- [x] Nieuwe pogingen voorzien van een uniek ID, route-/segmentversie, namen, finishtijdstip, tijd, gemiddeld vermogen en rijder.
- [x] Simulatie-, ERG- en pauzestatus vastleggen en herkenbaar tonen; ontbrekende gegevens bij oude records niet verzinnen.
- [x] Oude en onbekende lokale routevarianten zichtbaar houden.
- [x] Mijn KOM's offline bruikbaar houden.

### Route-records

- [x] Elke uitgereden route lokaal archiveren met id, route, versie, naam, finishtijd, tijd, vermogen, rijder en sim/ERG/pauze.
- [x] Oude ritten alleen meenemen als de finishtijd betrouwbaar is. `rides.json` niet als route-record gebruiken.
- [x] Route-records naast de segmenten in Mijn KOM's tonen, met beste tijd, vermogen, aantal en detail.
- [x] De ghost van de beste rit blijven gebruiken.
- [x] Het lokale model klaarzetten voor een latere upload (`kind: route`, segment leeg).

### Online Klassementen

- [ ] Een server-API toevoegen voor een efficient overzicht van online klassementen met resultaten en de leider per segment.
- [ ] Per online KOM het detailklassement tonen met positie, naam, tijd, vermogen en datum.
- [ ] Het eigen resultaat markeren wanneer het online profiel bekend is.
- [ ] De beste geldige gedeelde poging per rijder tonen, met behoud van privacy- en vriendeninstellingen.
- [ ] Routeversies gescheiden houden, zodat gewijzigde segmenten niet met oude versies worden vergeleken.
- [ ] Duidelijke laad-, lege- en foutmeldingen toevoegen.

Mijn KOM's toont de eigen historie. Algemeen en Vrienden tonen online
klassementen, niet alle privepogingen van andere rijders. De bestaande online API
bewaart geaccepteerde uploads, maar biedt nog geen endpoint voor een volledige
persoonlijke online pogingenhistorie.

### Verificatie En Uitrol

- [x] Tests toevoegen voor archivering, migratie en routeversies.
- [ ] Tests toevoegen voor online overzicht en privacy.
- [ ] De interface testen op desktop en mobiel, inclusief offlinegebruik en netwerkfouten.
- [ ] Na verificatie app en server publiceren.
- [ ] De Debian-testserver volgens HANDLEIDING_ONLINE_KOM.md bijwerken en controleren.
