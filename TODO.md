# Todolijst

## Klassementen: Persoonlijk En Online

Status: gepland, nog niet geimplementeerd.

Een extra Klassementen-tegel op het startscherm voor het persoonlijke KOM-archief
en de online klassementen, zonder eerst een route in Ride te openen.
Hiervoor zijn zowel appwijzigingen als een serverupdate nodig.

### Overzicht En Bediening

- [ ] Een extra Klassementen-tegel en zelfstandige overzichtspagina toevoegen.
- [ ] Tabs Mijn KOM's, Algemeen en Vrienden maken.
- [ ] Zoeken en filteren op route, KOM-segment, rijder en periode toevoegen waar van toepassing.
- [ ] Bij Mijn KOM's per segment de beste tijd, bijbehorend vermogen, aantal pogingen en laatste datum tonen.
- [ ] Alle bewaarde eigen pogingen in een detailweergave tonen, sorteerbaar op datum en tijd.
- [ ] Het huidige profielbeheer bereikbaar houden via Profiel en vrienden op een eigen subpagina.
- [ ] De bestaande ranglijsten tijdens Ride behouden.

### Persoonlijk Archief

- [ ] Alle toekomstige volledige lokale KOM-pogingen archiveren, ook buiten de huidige twintig snelste.
- [ ] Bestaande lokale records meenemen en duidelijk maken dat eerder verwijderde pogingen niet automatisch terugkomen.
- [ ] Nieuwe pogingen voorzien van een uniek ID, route-/segmentversie, namen, finishtijdstip, tijd, gemiddeld vermogen en rijder.
- [ ] Simulatie-, ERG- en pauzestatus vastleggen en herkenbaar tonen; ontbrekende gegevens bij oude records niet verzinnen.
- [ ] Oude en onbekende lokale routevarianten zichtbaar houden.
- [ ] Mijn KOM's offline bruikbaar houden.

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

- [ ] Tests toevoegen voor archivering, migratie, online overzicht, privacy en routeversies.
- [ ] De interface testen op desktop en mobiel, inclusief offlinegebruik en netwerkfouten.
- [ ] Na verificatie app en server publiceren.
- [ ] De Debian-testserver volgens HANDLEIDING_ONLINE_KOM.md bijwerken en controleren.
