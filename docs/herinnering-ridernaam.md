# Herinnering: verplichte ridernaam

Nog niet gebouwd. Binnenkort doen.

Bij de eerste keer dat de app geen ridernaam heeft, een venster dat niet
wegklikbaar is tot er een naam is opgeslagen. Dat is de bestaande
gebruikersnaam in `WattzeRitten/config.json`, maximaal 24 tekens. De
telefoonafstandsbediening blijft vrij. Wie al een naam heeft, ziet niets.

## App-map

Geen vaste installatiemap. De map waarin `Wattze.exe` staat, is de app.

- Broncode: `Documents\Wattze\WattzeRitten`
- Uitgepakte zip: naast de exe, bijvoorbeeld `Bureaublad\Wattze\WattzeRitten`

In die map staan de ridernaam, ritten en KOM's. De zip bevat `WattzeRitten` niet.

## Update

Een update is een nieuwe exe in dezelfde map. `WattzeRitten` blijft staan.
Een bestaande naam wordt niet opnieuw gevraagd. Is de naam nog leeg, dan
verschijnt het venster één keer.

Een zip in een nieuwe lege map is een nieuwe app: geen naam, geen ritten.
De hele map verplaatsen, inclusief `WattzeRitten`, neemt alles mee.
