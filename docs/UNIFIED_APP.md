# Unified MarketAI app

MarketAI Expo kokoaa mobiilin read-only-käyttäjäpolut viiteen välilehteen: **Tapahtumat**, **Market Memory**, **Scanneri**, **Tradet** ja **Asetukset**. Nykyinen tapahtuma- ja paper-trade-dashboard säilyy Tapahtumat-välilehdellä.

## Rajat

`streamlit_app.py` säilyy desktop- ja debug-käyttöliittymänä sekä laajojen asetusten, visualisointien ja trade-työkalujen kotina. Expo ei aja Pythonia: se kutsuu FastAPI:n `X-MarketAI-Key`-suojattuja read-only-reittejä. `/api/v1/market-memory/{ticker}` käyttää suoraan `market_memory`-ytimen data-, indikaattori-, pivot- ja similarity-moduuleja. `/api/v1/scanner` tarjoaa rajatun ensimmäisen taulukon. Admin-avain ja tuotantosekretit eivät kuulu mobiilibundleen.

## OTA-päivitys

Preview- ja production-buildit sidotaan EAS-kanaviin `eas.json`:issa. Julkaise native-runtimea muuttamaton JS/TS/TSX-päivitys komennolla `eas update --channel preview` ja varmennuksen jälkeen `eas update --channel production`. `runtimeVersion` seuraa app-versiota; native-riippuvuuden muutos vaatii uuden buildin ja version.

## PR #74

Seuraavaan PR:ään jää scannerin täysi feature parity, charttien viimeistely, avointen tradejen täydellinen hallinta, asetusten koko siirto ja Streamlit-template cleanup.
