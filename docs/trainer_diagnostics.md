# Tijdelijke trainerdiagnose

Doel: vaststellen waar schakelen bij 0% routegevoel eventueel vastloopt,
zonder de formule, BLE-commando's, retries of verzenddrempels te wijzigen.

## Proefrit

1. Herstart Wattze via `start_wattze.cmd` en verbind de trainer.
2. Stel routegevoel in op 0% en open daarna een nieuwe gamepagina via
   `http://localhost:8080/game?sim=1`. Een reeds geopende gamepagina kan nog
   de oude instelling gebruiken.
3. Start een gewone rit, geen ERG-training. Open het Sim-paneel via **Sim**.
   Laat de simulatie zelf uit; klik niet op **Aan**.
4. Klik **Start diagnoselog** en controleer dat **Log actief** verschijnt.
5. Fiets ongeveer 20 seconden met dezelfde versnelling. Schakel vervolgens
   enkele keren omhoog en omlaag, met circa 5 seconden tussen de acties.
6. Klik **Stop diagnoselog**. Start niet opnieuw voordat het log geanalyseerd
   is: een nieuwe start vervangt het vorige bestand.
7. Geef door dat de proefrit klaar is en of je een weerstandsverschil voelde.
   De technische gegevens hoeven niet overgeschreven te worden.

## Bestand

Het log staat lokaal in `WattzeRitten/trainer_diagnostics.jsonl`, ook bij een
gebouwde versie in de rittenmap van de app. Iedere regel is een JSON-object.
Opname is standaard uit, wordt niet hervat na een app-herstart en stopt
automatisch na uiterlijk 30 minuten of 5 MB. Er worden geen gebruikersnamen,
apparaatnamen, Bluetooth-adressen of tokens opgenomen. De logging uploadt niets.
Bij een schrijffout stopt de logging; de traineraansturing gaat gewoon door.
Schrijven gebeurt via een begrensde wachtrij in een achtergrondthread. Een volle
wachtrij stopt de logging in plaats van op schijftoegang te wachten. Laat Wattze
na **Stop diagnoselog** nog even open zodat de laatste regels kunnen worden
weggeschreven.

## Analyse

- `t` is Unix-tijd; `elapsed` is verstreken tijd sinds logstart in seconden.
- `gear_changed` toont de door de backend verwerkte versnelling.
- `grade_requested` toont de door de backend geaccepteerde `target_grade`.
  `source` bevat, bij ingeschakelde logging op de gamepagina, de routehelling,
  het werkelijk gebruikte routegevoel (0-100), de browserversnelling en de
  versnellingsbijdrage. Vergelijk dit met `route_feel_setting` van de backend.
- `ftms_write`, `ftms_sent`, `ftms_response`, `ftms_timeout` en
  `ftms_write_error` onderscheiden poging, geslaagde BLE-write en trainerrespons.
  Opcode 17 (`0x11`) is SIM; een bevestiging moet respons `[17, 1]` hebben.
  `sim_command.grade` bevat de daadwerkelijk gecodeerde helling in procenten.
  Begrenzing is -10 tot +16%, nauwkeurigheid 0,01 procentpunt.
- `control`, `connected` en `disconnected` geven modus- en verbindingscontext.
  `sample` bewaart maximaal eenmaal per seconde vermogen (W), cadans (RPM),
  trainersnelheid (km/h), modus en de laatste SIM-commandostatus.

Een gemelde trainerbevestiging bewijst niet dat de fysieke weerstand verandert.
Vermogen en cadans helpen bij de analyse, maar de rijder moet aangeven wat er
voelbaar gebeurt. De huidige aansturing accepteert sommige niet-passende FTMS-
responsen nog als succes; dit bestaande gedrag is niet aangepast. De nieuwe
diagnostiek noemt zulke responsen juist niet bevestigd.
