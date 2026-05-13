# Market Memory Bot - määrittely

## Tavoite

Rakennetaan osakemarkkinoiden analysointityökalu,
joka vertaa nykyisiä 15 pörssipäivää
historiallisiin 15 pörssipäivään ennen merkittävää huippua tai pohjaa.

Järjestelmän pitää:

* hakea historiallinen markkinadata
* tunnistaa käännekohdat
* laskea tekniset indikaattorit
* verrata nykyistä rakennetta historiallisiin rakenteisiin
* visualisoida trendit päällekkäin
* tehdä samankaltaisuushälytyksiä

---

## Historiallinen vertailulogiikka

NYKYHETKI:
viimeiset 15 pörssipäivää

HISTORIA:
15 pörssipäivää ennen historiallista huippua tai pohjaa

ÄLÄ käytä historiallista dataa käännekohdan jälkeen vertailussa.

---

## Käytettävät indikaattorit

* RSI 14
* MACD histogrammi
* ATR %
* Volyymisuhde
* Etäisyys EMA20:stä
* Etäisyys EMA50:stä

---

## Visualisointi

Piirrä päällekkäin:

* nykyinen trendi
* parhaat historialliset osumat

Normalisoitu kurssi alkaa arvosta 100.

Käytä eri värejä:

* nykyinen rakenne
* historialliset pohjat
* historialliset huiput

Käytä Plotly-kirjastoa.

---

## Hälytykset

Tee hälytys kun:
samankaltaisuus >= 0.75

Hälytystyypit:

* REBOUND WATCH
* SHORT WATCH

---

## Toimialakohtaiset asetukset

Esimerkkitoimialat:

* teknologia
* paperiteollisuus
* pankit
* teollisuus

Jokaiselle toimialalle:

* dipin rajat
* RSI-rajat
* volatiliteettiasetukset
