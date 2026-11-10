# Todolijst

Deze lijst is oorspronkelijk uitgewerkt op basis van broncodeonderzoek, zonder
draaiende applicatie of trainer. Uitgevoerde wijzigingen en controles worden
hieronder per onderdeel bijgehouden.

## Actuele status

- Punt 9: trainerhellingweergave en praktijkcontrole van verstuurde commando's afgerond.
- Punt 7: normale werking bij 0% routegevoel bevestigd; afzonderlijke foutafhandeling blijft open.
- Groter versnellingsbereik (-8% tot +8%): geimplementeerd en automatisch getest; rijgevoel nog controleren.
- Punt 1: ontvangstcode hersteld en automatisch getest; gebruiker meldt dat cadans werkt. Stoppen met trappen en opnieuw verbinden nog apart controleren.
- Punt 3: tien seconden vertraagd verbergen op beide schermen geimplementeerd en automatisch getest; fysieke ventilatorcontrole staat nog open.
- Punt 5: alleen de Windows-pc wakker houden tijdens rit en pauze geimplementeerd; praktijkcontrole en eventuele klassieke screensaver blijven open. Telefoon valt buiten scope.
- Punt 12: verouderde pauze-/hervatmelding na beeindigen hersteld en met vier extra Edge-tests gecontroleerd; praktijkcontrole staat nog open.
- Punt 4: lokale NPC's staan vóór en achter de speler, in een middenstand tussen de oude en de te drukke verdeling. Serververkeer is ongewijzigd. Praktijkcontrole staat nog open.
- Punt 6: voorgrondgebouwen zijn 45% groter; achtergrond is ongewijzigd. Praktijkcontrole in venster en fullscreen staat nog open.
- Verificatie: alle 148 tests slagen, inclusief Edge.
- Punt 11: menuknop linksonder toegevoegd; Stop-knop verwijderd. Pauze zet de scène stil zonder eindkaart. Praktijkcontrole staat nog open.
- Punt 2: kruisje op de telefoonafstandsbediening verborgen bij aanraakbediening, ook liggend. Een smal desktopvenster behoudt de link.

## 1. Ontbrekende RPM

- [x] De FTMS- en Cycling Power-ontvangst controleren en de gevonden parserproblemen herstellen.
- [x] Cadans tijdens fietsen werkt volgens de terugmelding van de gebruiker na de wijziging.
- [ ] Nog apart controleren dat RPM bij stoppen nul wordt en na opnieuw verbinden goed terugkomt.

### Oorspronkelijke bevindingen

De RPM-weergave en de koppeling met trainergegevens bestaan al. Er zijn wel
concrete tekortkomingen in de ontvangstcode:

- De FTMS-parser houdt niet correct rekening met pakketten waarin het snelheidsveld ontbreekt. Daardoor kunnen andere velden verkeerd worden gelezen.
- Een pakket zonder cadans zet RPM direct op nul, ook als een eerder pakket wel cadans bevatte.
- De alternatieve ontvangst via Cycling Power leest alleen vermogen, geen cadans.

### Uitgevoerd

De FTMS-parser leest het snelheidsveld alleen als dit volgens bit 0 aanwezig is.
De lengte van alle aangekondigde velden wordt gecontroleerd; afgebroken
pakketten worden genegeerd in plaats van als fictieve nulwaarden verwerkt.
Een ontbrekende cadans of snelheid behoudt de vorige meting. Expliciete nul
wordt direct overgenomen. Ontbrekend vermogen overschrijft de vorige waarde
niet en telt niet als een extra nulmeting in gemiddelde of historie.

Cadans vervalt bij vijf seconden zonder nieuwe geldige cadans naar nul, ook
als er nog vermogensberichten binnenkomen. Deze vervaltijd wordt gecontroleerd
bij ontvangst, in de BLE-loop en bij het uitlezen van snapshots en `/api`.
Nieuwe verbindingen en normale teardown wissen de metingen en de crankbasis.

Cycling Power berekent nu RPM uit verschillen in crankomwentelingen en
eventtijd (1/1024 seconde), met correcte optionele offsets en 16-bits wrap.
De eerste meting vormt alleen een basis. Duplicaten, onbruikbare tijdverschillen
en onwaarschijnlijke waarden boven 300 RPM vernieuwen de cadans niet. Na een
lange onderbreking wordt opnieuw een basis opgebouwd.

### Controle en beperking

Twaalf nieuwe telemetrietests controleren optionele velden, afgebroken pakketten,
ontbrekend versus nul, vervaltijd, crankberekening, wraps en resets. Alle 143
tests slagen. De eerdere proefritten bevatten al cadans, maar zijn van voor
deze ontvangstwijziging. Na de wijziging heeft de gebruiker gemeld dat cadans
lijkt te werken. Dat bevestigt gebruik tijdens fietsen, maar is geen expliciete
controle van stoppen met trappen of opnieuw verbinden.

De bronselectie is ongewijzigd: FTMS heeft voorrang; Cycling Power wordt gebruikt
als FTMS ontbreekt. Een trainer die FTMS zonder cadans levert maar daarnaast
wel Cycling Power-crankgegevens heeft, vraagt nog een aparte gecombineerde
ontvangstoplossing als dat in de praktijk nodig blijkt.

Bron: `wattze/telemetry.py`, `wattze/ble_wattze.py`, `wattze/http_handler.py`,
`tests/test_telemetry.py`.

## 2. Sluitknop op mobiel

- [x] Het kruisje in het afstandsbedieningsscherm op mobiel verbergen.
- [ ] Op een echte telefoon, staand en liggend, controleren dat het kruisje weg is en de schakelknoppen bruikbaar blijven.

### Bevindingen

Het kruisje staat rechtsboven. Het is een link terug naar het dashboard, geen
browser-sluitknop.

### Uitgevoerd

Alleen deze link wordt verborgen bij `(hover: none) and (pointer: coarse)`.
Dat geldt voor telefoons in staande en liggende stand, onafhankelijk van de
schermbreedte. De schakelknoppen en ventilatorbediening blijven zichtbaar.
Een smal desktopvenster met muis behoudt de link. Tablets en sommige hybride
laptops vallen ook onder dit aanraakcriterium; dat is bewust, omdat daar dezelfde
afstandsbediening wordt gebruikt.

Bron: `pages/remote.html`, `tests/test_online_kom_ui.py`.

## 3. Ventilator na verbindingsverlies

- [x] De ventilatorweergave pas na 10 seconden aaneengesloten verbindingsverlies verbergen.
- [ ] Met de fysieke ventilator korte en lange uitval en herstel op beide schermen controleren.

### Oorspronkelijke bevindingen

De huidige schermen verschillen:

- In het afstandsbedieningsscherm verdwijnt de ventilator direct bij verbindingsverlies.
- In het ritscherm blijft het vak staan, maar wordt de waarde `-`.
- Er wordt nog geen tijdstip van verbindingsverlies bijgehouden.

### Uitgevoerd

De backend bewaart vanaf de eerste gedetecteerde fout een monotone tijd voor
het verbindingsverlies. Nieuwe fouten verschuiven dit tijdstip niet. Zowel
`/api` als de SSE-snapshots geven dezelfde berekende `fan.visible` door.
Het ritscherm en de telefoonbediening verbergen het ventilatorvak daardoor
pas bij minstens 10 seconden aaneengesloten verbindingsverlies. De schermen
verwerken dit bij de eerstvolgende statusupdate, niet noodzakelijk exact op
de tiende seconde. Herstel wist de timer en toont het vak bij de volgende
update weer, ook als het eerder al verborgen was.

Tijdens de wachttijd toont het ritscherm `-`; de telefoonbediening meldt
dat de verbinding wordt hersteld en markeert geen stand als actief. Een
uitgeschakelde of niet geconfigureerde ventilator blijft direct verborgen.
Een adreswijziging of uitschakeling wist de oude verlies-timer.

Bij een verbindingsfout wordt ook de verzendcache ongeldig gemaakt. De
regelaar biedt de gewenste stand daardoor opnieuw aan, zelfs als deze niet
gewijzigd is. Dit voorkomt dat alleen een herstelde ping voldoende lijkt
terwijl de ESP de fysieke ventilator intussen heeft uitgezet.

### Controle

Dertien backendtests controleren onder meer 9,999 versus 10 seconden, herhaalde
fouten, herstel, cachegedrag, opnieuw versturen, uitschakelen en uitdraaien.
Een Edge-browsertest controleert de zichtbaarheid en herstelstatus op zowel
het ritscherm als de telefoonbediening. Alle 143 tests slagen. De ESP-firmware
en de veiligheidsuitschakeling zijn niet gewijzigd. Een echte netwerkonderbreking
met de fysieke ventilator is nog niet getest.

### Aandachtspunten

De ESP schakelt de ventilator uit veiligheid na ongeveer 8 seconden zonder ping
uit. Dat veiligheidsmechanisme moet los blijven staan van de zichtbaarheid op
het scherm. Ook controleren of de gewenste ventilatorstand na herstel opnieuw
wordt verstuurd: een herstelde verbinding betekent niet automatisch dat de
fysieke ventilator weer op de gewenste stand draait.

Bron: `wattze/fan.py`, `wattze/telemetry.py`, `wattze/http_handler.py`,
`pages/remote.html`, `pages/game.html`, `tests/test_fan.py`,
`tests/test_online_kom_ui.py`, `esphome/esp32-relays.yaml:133-143`.

## 4. Meer NPC's

- [x] De lokale dichtheid van NPC's verhogen, zowel vóór als achter de speler.
- [ ] In een echte rit controleren dat ontmoetingen regelmatiger zijn en niemand zichtbaar in beeld springt.

### Bevindingen

In de lokale modus zijn er 10 meerijders en 10 tegenliggers. Die staan relatief
ver uit elkaar; tegenliggers worden aanvankelijk over de hele route verdeeld.
Nieuwe NPC's verschijnen 220-1620 meter vooruit, terwijl maar een klein deel
van dat traject tegelijk in beeld past.

Multiplayer kan aantallen en posities van de server ontvangen. Alleen de lokale
aantallen aanpassen verandert dat verkeer niet.

### Vervolg

Lokaal staan er 6 meerijders vóór de speler, met ongeveer 110 meter
tussenruimte, en 3 meerijders achter de speler, net buiten beeld. Dat ligt
tussen de oude 10 verre meerijders en de eerdere 8 plus 6. Achterblijvers
blijven achter tot ze voorbij zijn en vooraan uit beeld rijden; daarna komen
ze opnieuw van achteren. Langzamere fietsers die meer dan 180 meter achter
raken, komen later weer net buiten beeld vóór de speler. Acht tegenliggers
staan ongeveer 120 meter uit elkaar in het komende stuk en komen na passeren
achteraan die rij terug. Servergestuurde en wereld-NPC's gebruiken deze
verdeling niet. De praktijkcontrole staat nog open.

### Aandachtspunten

Meer NPC's beinvloeden ook slipstream en daarmee het rijgedrag. Na aanpassing
controleren op lege perioden, tijd tot de eerste tegenligger, zichtbaar
verschijnen van NPC's en prestaties.

Bron: `pages/game.html:862-953`, `pages/game.html:3669-3708`.

## 5. Screensaver voorkomen

- [x] Alleen de Windows-pc tijdens een actieve of gepauzeerde rit tegen automatische schermuitschakeling en slaapstand beschermen.
- [ ] In de praktijk controleren dat het scherm tijdens rit en pauze aanblijft en de normale energie-instellingen na ritbeeindiging weer gelden.
- [ ] Indien een klassieke Windows-screensaver actief is: afzonderlijk vaststellen of hiervoor nog een aanvullende oplossing nodig is.

### Oorspronkelijke bevindingen

Er is momenteel geen expliciete voorziening om het scherm wakker te houden.
Fullscreen inschakelen is daarvoor op zichzelf geen betrouwbare oplossing.

### Uitgevoerd

De gebruiker heeft gekozen voor alleen de pc. Een aparte achtergrondthread
roept de Windows-API `SetThreadExecutionState` aan met `ES_CONTINUOUS`,
`ES_SYSTEM_REQUIRED` en `ES_DISPLAY_REQUIRED` zolang `rec.active` waar is.
Een gepauzeerde rit blijft actief en houdt de bescherming dus vast. Na het
beeindigen van de rit wordt met alleen `ES_CONTINUOUS` vrijgegeven. De thread
controleert de ritstatus iedere seconde en doet geen extra Windows-aanroepen
zolang de gewenste toestand ongewijzigd blijft.

Activeren en vrijgeven gebeuren op dezelfde thread, zoals Windows vereist.
Bij normaal stoppen van de server wordt de thread gestopt en de bescherming
vrijgegeven. Bij procesbeeindiging ruimt Windows de threadgebonden aanvraag op.
Een mislukte aanroep wordt gelogd en bij de volgende controle opnieuw geprobeerd,
zonder de rit of trainerregeling te stoppen.

Deze oplossing werkt onafhankelijk van browser, fullscreen en lokaal HTTP.
De telefoon is niet aangepast. Er worden geen energie-instellingen of
screensaverinstellingen gewijzigd; handmatig vergrendelen of slapen blijft mogelijk.

### Controle en beperking

Elf tests met een gemockte Windows-API controleren ritstart, pauze, hervatten,
ritstop, thread-eigenaarschap, vrijgeven, herhaalde fouten en niet-Windows gedrag.
Alle 143 tests slagen. Echte automatische schermuitschakeling en slaapstand zijn
niet afgewacht; de praktijkcontrole staat nog open.

`SetThreadExecutionState` voorkomt automatische schermuitschakeling en slaapstand,
maar blokkeert een klassieke Windows-screensaver niet gegarandeerd. Dat deel van
de oorspronkelijke melding blijft apart open als zo'n screensaver wordt gebruikt.
Als een browser-stopbericht niet bij de backend aankomt, kan de rit actief
blijven en blijft daarmee ook de bescherming actief tot de rit of Wattze stopt.

Bron: `wattze/keep_awake.py`, `wattze_game.py`, `wattze/rides.py`,
`tests/test_keep_awake.py`.

## 6. Voorgrondhuisjes vergroten

- [x] Voorgrondgebouwen ongeveer 45% groter maken ten opzichte van de fietser, zonder de achtergrond mee te vergroten.
- [ ] In een normaal venster en fullscreen controleren dat dorp, rijwoningen, los huis en boerderij groter ogen en de achtergrond gelijk blijft.

### Bevindingen

De voorgrondhuizen zijn al afzonderlijk geschaald, maar sommige zijn inclusief
dak maar weinig hoger dan de fietser. Dat ondersteunt de gemelde schaalafwijking.

### Vervolg

Dorp, rijwoningen, los huis, boerderij, schuur, silo, molen en kapel zijn 45%
groter. De onderlinge afstand schaalt mee en gebouwen worden tot 420 pixels
buiten beeld nog getekend, zodat ze niet aan de rand verschijnen. Achtergrond,
lantaarns en scenezoom zijn ongewijzigd. De praktijkcontrole staat nog open.

### Aandachtspunten

Gebouwen worden na de fietsers getekend en kunnen deze dus bedekken. Algemene
scenezoom is geen oplossing, want die vergroot de fietser evenveel. Dorp,
rijwoningen, los huis en boerderij vergelijken in normaal venster en fullscreen.

Bron: `pages/game.html:1655-1700`, `pages/game.html:1888-1893`.

## 7. Schakelen bij 0% routegevoel

- [x] Schakelen bij 0% routegevoel onderzoeken: werking tijdens een normale SIM-rit met de echte trainer bevestigd; geen formulewijziging nodig.
- [ ] De afzonderlijk gevonden foutafhandeling bij mislukte hellingsaanvragen en niet-passende FTMS-responsen onderzoeken.

### Bevindingen

De huidige formule hoort dit al te ondersteunen:

```text
trainerhelling = routehelling * routegevoel + versnellingsbijdrage
```

Bij 0% routegevoel blijft de versnellingsbijdrage bestaan. De formule zelf is
niet de aangetoonde oorzaak.

Wel gevonden:

- In ERG-modus wordt vermogen aangestuurd, niet de hellingsbijdrage van de versnelling.
- Een mislukte hellingsaanvraag wordt niet betrouwbaar opnieuw aangeboden.
- Een actieve gamepagina moet de gewijzigde versnelling omzetten naar een nieuw trainercommando.

### Vervolg

Met de trainerhelling zichtbaar controleren of schakelen het commando verandert,
of dat commando wordt bevestigd en of de trainer erop reageert. SIM/ERG-modus,
actieve/gepauzeerde rit, verbindingsstatus en mislukte verzoeken meenemen.
Niet zonder aangetoonde noodzaak de routegevoel-formule veranderen.

### Diagnosevoorziening

Een tijdelijke, expliciet in te schakelen diagnoselog is toegevoegd aan het
Sim-paneel van `/game?sim=1`. Deze registreert schakelacties, door de game
gebruikte rekenwaarden, ontvangen hellingsaanvragen, FTMS-verzendingen en
responsen, verbindingswisselingen en eenmaal per seconde telemetrie.
Bestand: `WattzeRitten/trainer_diagnostics.jsonl`. Standaard uit; maximaal
30 minuten of 5 MB. Een nieuwe start vervangt het vorige log.
Zie `docs/trainer_diagnostics.md` voor de werkwijze.
Schrijven gebeurt in een achtergrondthread met begrensde wachtrij; een volle
wachtrij of schrijffout stopt alleen het log. Alle 143 geautomatiseerde tests
slagen, inclusief start/stop in Edge en blokkering/foutgevallen bij het schrijven.

### Praktijkuitkomst

Twee proefritten bij 0% routegevoel zijn geanalyseerd. In de eerste proefrit
(versnelling 1-9 van 16) bleef het commando grotendeels negatief (-5,50% tot
+0,37%). De rijder voelde toen nauwelijks verschil. Een minimale fysieke
trainerweerstand bij negatieve helling is een plausibele verklaring, maar is
niet afzonderlijk gemeten.

De tweede opname duurde circa 123 seconden en bevatte 15 nieuwe SIM-commando's,
allemaal met de passende succesrespons `[17, 1]`, zonder geregistreerde
timeouts, schrijffouten of verbindingsverlies. Zowel de backend als de
gamepagina gebruikte 0% routegevoel. Tijdens opschakelen liep de verstuurde
helling op tot +5,50% (versnelling 16); bij terugschakelen daalde deze weer tot
-0,37% (versnelling 8). De rijder bevestigde duidelijke weerstandsverandering.
Het vermogen steeg tijdens opschakelen van ongeveer 80 W naar ruim 200 W;
de cadans bleef niet exact gelijk, dus dit is ondersteunende observatie,
geen gekalibreerde weerstandsmeting.

Conclusie: de normale SIM-aansturing en virtuele versnelling werken ook bij
0% routegevoel. Er is geen aangetoonde aanleiding om de formule te wijzigen.
De afzonderlijke foutpaden hierboven zijn hiermee niet praktijkgetest.

### Groter versnellingsbereik

Na de proefritten is de versnellingsbijdrage op verzoek vergroot van -5,50%
tot +5,50% naar -8% tot +8%. Het ingestelde aantal versnellingen blijft gelijk.
Bij 16 versnellingen is de stap nu circa 1,07 procentpunt; bij een routehelling
van -5% en 100% routegevoel vraagt versnelling 16 nu +3% aan in plaats van
+0,50%. De bestaande HTTP- en BLE-begrenzing blijft ongewijzigd.
Het bereik geldt zowel bij het laden als bij een live wijziging van het aantal
versnellingen. De browsertest controleert 0% en 100% routegevoel bij 12, 16 en
24 versnellingen. Het rijgevoel met het grotere bereik moet nog worden getest.

Bron: `pages/game.html:794-831`, `pages/game.html:3730-3740`.

## 8. Samenvatting in screenshot

- [ ] De gewenste ritsamenvatting volledig en consistent in het screenshot opnemen.

### Bevindingen

Screenshots bevatten al een beperkte tekstsamenvatting, maar niet de volledige
eindkaart. Het screenshot wordt gemaakt voordat de eindkaart is opgebouwd.
Daardoor kunnen tijd en afstand iets afwijken.

Losse HTML-overlays en afzonderlijke grafiekcanvassen worden niet automatisch
meegenomen bij het kopieren van het hoofdcanvas.

### Vervolg

Dezelfde eindgegevens gebruiken voor screenshot en eindkaart en de gewenste
statistieken als leesbare kaart in het screenshot tekenen. Eventuele grafieken
pas toevoegen nadat deze zijn opgebouwd.

### Open vragen

- Alleen statistieken opnemen, of ook grafieken en KOM-resultaten?
- Moet dit ook voor Blokjes gelden? Dat heeft een aparte screenshotfunctie.

Bron: `pages/game.html:3344-3457`, `pages/blokjes.html:482-544`.

## 9. Verstuurde trainerhelling tonen

- [x] In de ontwikkelweergave het daadwerkelijk naar de trainer verstuurde hellingspercentage tonen, zonder de aansturing te veranderen.
- [x] De verstuurde waarden en trainerbevestigingen tijdens echte ritten via het diagnoselog controleren.

### Bevindingen

Nu staat de routehelling in beeld. De trainer krijgt een aangepaste waarde door
routegevoel, versnelling en begrenzing.

De backend bewaart niet altijd de werkelijk verstuurde waarde. Een doel van
`18%` wordt in het BLE-pakket begrensd tot `16%`, maar kan als `18%`
geregistreerd blijven.

### Vervolg

De daadwerkelijk gecodeerde waarde registreren en tonen, bij voorkeur met de
bevestigingsstatus van de trainer. Bij ERG of ontbrekende gegevens geen
misleidende `0%` tonen. De zichtbare waarde moet het verstuurde of bevestigde
SIM-commando beschrijven, niet een gemeten fysieke helling of weerstand.

### Uitgevoerd

De uitlezing staat in het Sim-paneel van `/game?sim=1`. Simulatie inschakelen is
niet nodig. De bestaande API-polling werkt de waarde elke twee seconden bij.
De waarde wordt na een geslaagde BLE-write uit het gecodeerde pakket gelezen,
inclusief begrenzing en afronding. De status onderscheidt wachten op bevestiging,
bevestigd, afgewezen, timeout, ongeldige/niet-passende respons en schrijffout.
Alleen een succesvolle respons voor SIM-opcode `0x11` geldt hier als bevestigd.

Bij ERG, een verbroken verbinding of ontbrekende gegevens wordt geen fictieve
`0%` getoond. De diagnostiek wordt bij een nieuwe SIM-sessie, moduswisseling en
verbindingsverlies gewist. Bestaande regelwaarden, commando's en verzendvoorwaarden
zijn niet aangepast. Het betreft het laatst verstuurde commando, geen gemeten
fysieke helling of weerstand.

### Controle

Elf geautomatiseerde backendtests dekken pakketbegrenzing, afronding, echte nul,
bevestiging, foutgevallen en resets bij ERG/disconnect/reconnect. De browsertest
controleert het paneel zonder ingeschakelde simulatie, statussen en desktop- en
mobiele breedte in Edge. De geautomatiseerde tests slagen. De daadwerkelijke
commando's en passende trainerbevestigingen zijn daarnaast met echte proefritten
gecontroleerd; zie de praktijkuitkomst bij punt 7.

Bij de tests is ook bestaand gedrag bevestigd: de aansturing zelf controleert
bij een succesvolle FTMS-respons nog niet de bijbehorende opcode. Dit is niet
gewijzigd; de nieuwe diagnostiek noemt zo'n niet-passende respons niet bevestigd.

Bron: `wattze/ble_wattze.py:155-158`, `wattze/ble_wattze.py:294-306`,
`pages/game.html:3250-3274`.

## 10. Rechteroverlays in fullscreen

- [ ] De rechteroverlays in fullscreen groter en beter leesbaar maken.

### Bevindingen

Het routeprofiel en klimprofiel krijgen nu al een beperkte fullscreenvergroting,
maar blijven standaard slechts 110 pixels hoog.

### Vervolg

Deze overlays specifiek in fullscreen vergroten en hun onderlinge posities
aanpassen. Niet de algemene vergroting van alle overlays verhogen. Normale
fullscreen en ERG-fullscreen hebben verschillende posities en moeten beide op
overlap worden gecontroleerd, ook op lage schermen.

### Open vragen

- Gaat het inderdaad om de route- en klimprofielen?
- Moet vooral de hoogte, de tekst of de gehele weergave groter worden?

Bron: `pages/game.html:39-67`, `pages/game.html:98`.

## 11. Menuknop in het spel

- [x] Een menuknop linksonder toevoegen om tijdens het spelen routegevoel aan te passen, te pauzeren en de rit te beëindigen.
- [ ] In een echte rit controleren dat pauze de scène stilzet, hervatten verdergaat en beëindigen de samenvatting opent.

### Uitgevoerd

De Stop-knop rechtsboven is verwijderd. Tijdens een rit staat linksonder een
menuknop, ook in volledig scherm. Het menu sluit met het kruisje rechtsboven;
sluiten hervat een gepauzeerde rit niet. Het menu bevat routegevoel, pauzeren
of hervatten, en rit beëindigen.

Routegevoel werkt direct tijdens het rijden en wordt opgeslagen voor een
volgende rit. Pauzeren toont geen eindkaart. Tijd, afstand, fietser, NPC's,
animatie en trainingsvoortgang blijven stilstaan; de pauzeduur telt niet mee.
De traineraansturing en opname worden tijdens pauze onderbroken en bij hervatten
weer gestart. Beëindigen gebruikt de bestaande eindafhandeling.

De vermogensgrafiek is 60% breed en horizontaal gecentreerd. De menuknop
linksonder is vergroot en overlapt de grafiek niet. De wattagekaart linksboven
is niet gewijzigd.

Een lokale pauze zet andere multiplayerspelers niet stil. Na hervatten loopt
de eigen scène verder vanaf de gepauzeerde positie.

## 12. Spel hervatten na beeindigen

- [x] De verouderde pauze-/hervatmelding na beeindigen herstellen en automatisch testen.
- [ ] Tijdens een echte rit via het menu beëindigen controleren; hervatten mag daarna niet meer worden aangeboden.

### Bevindingen en herstel

Er is geen opgeslagen lokale hervatvlag gevonden en de exacte tekst 'Spel
hervatten' komt niet voor in de huidige pagina. Wel gevonden: de knop
'Beeindigen' op de pauzesamenvatting verborg de hervatknoppen, maar liet
'Rit gepauzeerd' en 'Je kunt de rit hervatten.' staan. Alleen de opname werd
gestopt; de bestaande eindafhandeling voor titel, melding en samenvatting
werd overgeslagen.

De knop gebruikt nu `finishRide(false)`, net als de gewone ritbeeindiging.
Daarmee verdwijnen de pauzestatus en hervatmogelijkheid en verschijnt de
definitieve samenvatting. De laatste pauzeduur wordt eerst uit de verstreken
rittijd gehaald, zodat wachten in het pauzescherm niet wordt opgeteld bij de
eindtijd of het screenshot. De opname wordt maar eenmaal gestopt.

### Controle en afbakening

Vier nieuwe Edge-tests controleren pauzeren en daarna beeindigen, uitsluiting
van tien minuten pauzetijd, een zeer korte rit zonder opgeslagen opname,
echt hervatten en de gewone finish. Na opslaan of nogmaals klikken op de
verborgen knoppen keert de hervatstatus niet terug. Alle 143 tests slagen.

Dit herstel betreft de normale pauze-/eindknop. Browser-terugnavigatie via
back/forward-cache, meerdere gelijktijdige gamepagina's en een niet-afgeleverd
stopbericht blijven afzonderlijke levenscyclusrisico's; die zijn niet met
deze wijziging opgelost.

Bron: `pages/game.html` (`finishRide`, `btnEndRide`),
`tests/test_online_kom_ui.py`.

## Aanbevolen volgorde

1. Resterende praktijkcontroles uitvoeren: RPM bij stoppen en opnieuw verbinden (punt 1), groter versnellingsbereik en ventilatoruitval/herstel (punt 3). Cadans tijdens fietsen is door de gebruiker bevestigd; softwaretests zijn afgerond.
2. Ventilatorvertraging (punt 3) is geimplementeerd; alleen de fysieke praktijkcontrole staat nog open.
3. Pc-schermwakkerhouding (punt 5) is geimplementeerd; werking tijdens rit/pauze en vrijgave na beeindigen nog in de praktijk controleren. Telefoon is uitgesloten; een klassieke screensaver blijft zo nodig een afzonderlijk aandachtspunt.
4. Menuknop (punt 11) is toegevoegd. Nog controleren dat pauze de scène stilzet en beëindigen via het menu de samenvatting opent.
5. Fullscreenoverlays en screenshotsamenvatting uitvoeren (punten 10 en 8). Voorgrondgebouwen (punt 6) nog in venster en fullscreen controleren. NPC-dichtheid (punt 4) nog in een rit controleren. Het mobiele kruisje (punt 2) nog op een echte telefoon controleren.
6. De resterende foutafhandeling van trainercommando's uit punt 7 gericht onderzoeken; normale SIM-werking bij 0% is al bevestigd.
