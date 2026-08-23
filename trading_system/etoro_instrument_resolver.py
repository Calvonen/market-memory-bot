from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from trading_system.etoro_market_data import EtoroInstrumentCandidate


@dataclass(frozen=True)
class InstrumentResolutionRequest:
    instrument: str
    company_name: str = ""
    market: str = ""


@dataclass(frozen=True)
class ResolvedEtoroInstrument:
    instrument_id: int
    symbol: str
    display_name: str
    market: str = ""


class EtoroInstrumentSearch(Protocol):
    def search_instruments(self, term: str) -> tuple[EtoroInstrumentCandidate, ...]: ...


def _normalise_text(value: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", value.upper()).split())


def _normalise_symbol(value: str) -> str:
    return re.sub(r"\s+", "", value.upper())


def _base_symbol(value: str) -> str:
    return _normalise_symbol(value).split(".", 1)[0]


def _symbol_suffix(value: str) -> str | None:
    normalised = _normalise_symbol(value)
    if "." not in normalised:
        return None
    return normalised.split(".", 1)[1]


class EtoroInstrumentResolver:
    """Resolve tracked instruments to one eToro instrument without guessing.

    Resolution is deliberately conservative: an exact symbol wins, then a
    unique base-symbol match, then a unique exact company-name match. Any
    remaining ambiguity or lack of an exact match returns ``None``. A symbol
    or base-symbol match is only accepted if it does not contradict the
    known company name or exchange suffix for the request; a contradiction
    fails closed to ``None`` instead of falling back to a weaker heuristic.
    """

    def __init__(self, search: EtoroInstrumentSearch) -> None:
        self._search = search

    def resolve(self, request: InstrumentResolutionRequest) -> ResolvedEtoroInstrument | None:
        terms = []
        if request.instrument.strip():
            terms.append(request.instrument.strip())
        if request.company_name.strip() and request.company_name.strip() not in terms:
            terms.append(request.company_name.strip())
        if not terms:
            return None

        by_id: dict[int, EtoroInstrumentCandidate] = {}
        for term in terms:
            for candidate in self._search.search_instruments(term):
                by_id[candidate.instrument_id] = candidate

        candidates = tuple(by_id.values())
        if not candidates:
            return None

        requested_symbol = _normalise_symbol(request.instrument)
        if requested_symbol:
            exact_symbol = [
                candidate
                for candidate in candidates
                if candidate.symbol and _normalise_symbol(candidate.symbol) == requested_symbol
            ]
            resolved = self._unique(exact_symbol)
            if resolved is not None:
                if self._name_conflicts(exact_symbol[0], request):
                    return None
                return resolved

            requested_base = _base_symbol(request.instrument)
            base_matches = [
                candidate
                for candidate in candidates
                if candidate.symbol and _base_symbol(candidate.symbol) == requested_base
            ]
            resolved = self._unique(base_matches)
            if resolved is not None:
                candidate = base_matches[0]
                if self._name_conflicts(candidate, request) or self._suffix_conflicts(candidate, request):
                    return None
                return resolved

        requested_name = _normalise_text(request.company_name)
        if requested_name:
            name_matches = [
                candidate
                for candidate in candidates
                if candidate.display_name and _normalise_text(candidate.display_name) == requested_name
            ]
            return self._unique(name_matches)

        return None

    @staticmethod
    def _name_conflicts(candidate: EtoroInstrumentCandidate, request: InstrumentResolutionRequest) -> bool:
        requested_name = request.company_name.strip()
        if not requested_name:
            return False
        return _normalise_text(candidate.display_name) != _normalise_text(requested_name)

    @staticmethod
    def _suffix_conflicts(candidate: EtoroInstrumentCandidate, request: InstrumentResolutionRequest) -> bool:
        if not candidate.symbol:
            return False
        requested_suffix = _symbol_suffix(request.instrument)
        candidate_suffix = _symbol_suffix(candidate.symbol)
        if requested_suffix is None or candidate_suffix is None:
            return False
        return requested_suffix != candidate_suffix

    @staticmethod
    def _unique(candidates: list[EtoroInstrumentCandidate]) -> ResolvedEtoroInstrument | None:
        if len(candidates) != 1:
            return None
        candidate = candidates[0]
        return ResolvedEtoroInstrument(
            instrument_id=candidate.instrument_id,
            symbol=candidate.symbol or "",
            display_name=candidate.display_name,
            market=candidate.market or "",
        )
