from __future__ import annotations

MARKET_TICKERS: dict[str, list[str]] = {
    "Finland Top": [
        "NOKIA.HE", "KNEBV.HE", "UPM.HE", "NESTE.HE", "FORTUM.HE", "WRT1V.HE", "KESKOB.HE", "METSO.HE", "STERV.HE", "VALMT.HE",
        "ELISA.HE", "KEMIRA.HE", "ORNBV.HE", "OUT1V.HE", "CGCBV.HE", "NDA-FI.HE", "SAMPO.HE", "TIETO.HE", "HUH1V.HE", "KCR.HE",
        "KOJAMO.HE", "TELIA1.HE", "PON1V.HE", "REVENIO.HE", "HARVIA.HE", "TOKMAN.HE", "TYRES.HE", "OMASP.HE", "PUUILO.HE", "MANDATUM.HE",
    ],
    "Sweden Top": [
        "VOLV-B.ST", "ATCO-A.ST", "ERIC-B.ST", "HM-B.ST", "SEB-A.ST", "ABB.ST", "SWED-A.ST", "SAND.ST", "ALFA.ST", "TELIA.ST",
        "INVE-B.ST", "HEXA-B.ST", "ASSA-B.ST", "SHB-A.ST", "SKF-B.ST", "ESSITY-B.ST", "NDA-SE.ST", "AZN.ST", "EQT.ST", "BOL.ST",
        "LATO-B.ST", "INDU-C.ST", "GETI-B.ST", "TEL2-B.ST", "VOLCAR-B.ST", "SAAB-B.ST", "SWMA.ST", "ATCO-B.ST", "EPI-A.ST", "SINCH.ST",
    ],
    "Germany Top": [
        "SAP.DE", "SIE.DE", "ALV.DE", "BAS.DE", "BMW.DE", "MBG.DE", "DTE.DE", "IFX.DE", "VOW3.DE", "BAYN.DE",
        "ADS.DE", "MUV2.DE", "RWE.DE", "DBK.DE", "DHL.DE", "BEI.DE", "HEN3.DE", "SHL.DE", "MTX.DE", "P911.DE",
        "QIA.DE", "SY1.DE", "HEI.DE", "CON.DE", "AIR.DE", "MRK.DE", "ENR.DE", "RHM.DE", "PAH3.DE", "VNA.DE",
    ],
    "USA Top": [
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "BRK-B", "JPM", "XOM",
        "LLY", "V", "MA", "AVGO", "WMT", "COST", "UNH", "JNJ", "PG", "HD",
        "ORCL", "BAC", "CRM", "NFLX", "AMD", "KO", "PEP", "CVX", "TMO", "MRK",
    ],
    "Finland Full": [
        "NOKIA.HE", "KNEBV.HE", "UPM.HE", "NESTE.HE", "FORTUM.HE", "WRT1V.HE", "KESKOB.HE", "METSO.HE", "STERV.HE", "VALMT.HE",
        "ELISA.HE", "KEMIRA.HE", "ORNBV.HE", "OUT1V.HE", "CGCBV.HE", "MNDI.HE", "NDA-FI.HE", "SAMPO.HE", "TIETO.HE", "HUH1V.HE",
        "KCR.HE", "KOJAMO.HE", "TELIA1.HE", "UPONOR.HE", "YIT.HE", "PON1V.HE", "BILOT.HE", "REVENIO.HE", "SCANFL.HE", "MEKKO.HE",
        "ALBAV.HE", "ANORA.HE", "ATRAV.HE", "DIGIA.HE", "DNA.HE", "EEZY.HE", "FIA1S.HE", "FISKARS.HE", "GLA1V.HE", "HARVIA.HE",
        "HONBS.HE", "ICP1V.HE", "INCAP.HE", "KSLAV.HE", "LASSILA.HE", "LETO1.HE", "MARAS.HE", "NOHO.HE", "OKDAV.HE", "QTCOM.HE",
        "RAP1V.HE", "RAUTE.HE", "REG1V.HE", "ROVIO.HE", "SAGCV.HE", "SAA1V.HE", "SIILI.HE", "SRV1V.HE", "SUY1V.HE", "TAALA.HE",
        "TKSAV.HE", "TOKMAN.HE", "TRH1V.HE", "TYRES.HE", "VAIAS.HE", "VERK.HE", "VIK1V.HE", "WUF1V.HE", "YHPO.HE", "SSH1V.HE",
        "OMASP.HE", "PURMO.HE", "PIHLIS.HE", "ADMIN.HE", "KOSKI.HE", "NANOFH.HE", "PUUILO.HE", "SPINN.HE", "RELAIS.HE", "MANDATUM.HE",
    ],
    "Sweden Full": [
        "VOLV-B.ST", "ATCO-A.ST", "ERIC-B.ST", "HM-B.ST", "SEB-A.ST", "ABB.ST", "SWED-A.ST", "SAND.ST", "ALFA.ST", "TELIA.ST",
        "INVE-B.ST", "HEXA-B.ST", "ASSA-B.ST", "SHB-A.ST", "SKF-B.ST", "ESSITY-B.ST", "NDA-SE.ST", "AZN.ST", "EQT.ST", "BOL.ST",
        "LATO-B.ST", "INDU-C.ST", "GETI-B.ST", "TEL2-B.ST", "VOLCAR-B.ST", "SAAB-B.ST", "SWMA.ST", "ATCO-B.ST", "EPI-A.ST", "SINCH.ST",
        "SBB-B.ST", "SCA-B.ST", "EVO.ST", "NIBE-B.ST", "LIFCO-B.ST", "MTRS.ST", "KINV-B.ST", "THULE.ST", "ALIF-B.ST", "LUNE.ST",
    ],
    "Germany Full": [
        "SAP.DE", "SIE.DE", "ALV.DE", "BAS.DE", "BMW.DE", "MBG.DE", "DTE.DE", "IFX.DE", "VOW3.DE", "BAYN.DE",
        "ADS.DE", "MUV2.DE", "RWE.DE", "DBK.DE", "DHL.DE", "BEI.DE", "HEN3.DE", "SHL.DE", "MTX.DE", "P911.DE",
        "QIA.DE", "SY1.DE", "HEI.DE", "CON.DE", "AIR.DE", "MRK.DE", "ENR.DE", "RHM.DE", "PAH3.DE", "VNA.DE",
        "FRE.DE", "FME.DE", "HNR1.DE", "LEG.DE", "SRT3.DE", "G1A.DE", "ZAL.DE", "BNR.DE", "EVK.DE", "1COV.DE",
    ],
    "USA Full": [
        "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "BRK-B", "JPM", "XOM",
        "LLY", "V", "MA", "AVGO", "WMT", "COST", "UNH", "JNJ", "PG", "HD",
        "ORCL", "BAC", "CRM", "NFLX", "AMD", "KO", "PEP", "CVX", "TMO", "MRK",
        "CSCO", "IBM", "GE", "CAT", "GS", "MS", "ABBV", "MCD", "LIN", "ACN",
    ],
}
