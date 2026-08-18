from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import requests
from pydantic import BaseModel, ConfigDict, Field

from trading_system.models import EventExpectation
from trading_system.release_ingestion import ReleaseDocument


class ExtractedMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: float | str | None
    unit: str | None


class EventAnalysisPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[ExtractedMetric]
    guidance_summary: str
    management_summary: str
    catalyst_direction: str = Field(pattern="^(BULLISH|BEARISH|MIXED|NEUTRAL)$")
    catalyst_score_0_25: int = Field(ge=0, le=25)
    fundamental_direction: str = Field(pattern="^(BULLISH|BEARISH|MIXED|NEUTRAL)$")
    fundamental_score_0_35: int = Field(ge=0, le=35)
    key_positive_surprises: list[str]
    key_negative_surprises: list[str]
    uncertainties: list[str]
    invalidation_flags: list[str]
    evidence_quotes: list[str] = Field(max_length=8)

    def metric_values(self) -> dict[str, float | str | None]:
        return {metric.name: metric.value for metric in self.metrics}


@dataclass(frozen=True)
class AIEventAnalysis:
    payload: EventAnalysisPayload
    provider: str
    model: str
    raw_response: str


def analysis_from_record(record: dict[str, Any]) -> AIEventAnalysis:
    """Rebuild a persisted `event_ai_analyses` row into the shape analyze() returns.

    Used on worker restart to reuse an already-persisted analysis instead of
    calling an AI provider again for the same document + expectation version.
    """
    return AIEventAnalysis(
        payload=EventAnalysisPayload.model_validate(record["analysis"]),
        provider=str(record["provider"]),
        model=str(record["model"]),
        raw_response=str(record.get("raw_response") or ""),
    )


class EventAnalyzer(Protocol):
    def analyze(
        self,
        expectation: EventExpectation,
        document: ReleaseDocument,
    ) -> AIEventAnalysis: ...


def _allowed_metric_names(expectation: EventExpectation) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*expectation.consensus.keys(), *expectation.important_kpis)))


def _event_analysis_schema(expectation: EventExpectation) -> dict[str, Any]:
    schema = EventAnalysisPayload.model_json_schema()
    metric_def = (schema.get("$defs") or {}).get("ExtractedMetric") or {}
    name_schema = ((metric_def.get("properties") or {}).get("name") or {})
    allowed = list(_allowed_metric_names(expectation))
    if allowed:
        name_schema["enum"] = allowed
    return schema


def _parse_payload(raw: str, expectation: EventExpectation) -> EventAnalysisPayload:
    payload = EventAnalysisPayload.model_validate_json(raw)
    allowed = set(_allowed_metric_names(expectation))
    unknown = sorted({metric.name for metric in payload.metrics if metric.name not in allowed})
    if unknown:
        raise RuntimeError("AI returned non-canonical metric names: " + ", ".join(unknown))
    return payload


def _release_char_budget() -> int:
    configured = int(os.environ.get("MARKETAI_AI_RELEASE_CHAR_BUDGET", "12000"))
    return max(4_000, min(configured, 50_000))


def _keyword_tokens(expectation: EventExpectation) -> set[str]:
    tokens = {
        "guidance", "outlook", "operating profit", "net fees", "earnings", "eps",
        "cash", "debt", "cost", "saving", "restructuring", "exceptional", "germany",
        "uk", "ireland", "permanent", "temp", "contracting", "margin", "strategy",
        "current trading",
    }
    for key in (*expectation.consensus.keys(), *expectation.important_kpis):
        normalized = re.sub(r"[_\-]+", " ", str(key).lower())
        tokens.update(word for word in normalized.split() if len(word) >= 4)
    return tokens


def _select_release_text(expectation: EventExpectation, raw_text: str) -> str:
    budget = _release_char_budget()
    if len(raw_text) <= budget:
        return raw_text

    normalized = raw_text.replace("\r\n", "\n")
    paragraphs = [part.strip() for part in re.split(r"\n{1,}", normalized) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]

    head_budget = min(4_000, budget // 3)
    head = normalized[:head_budget].strip()
    keywords = _keyword_tokens(expectation)
    scored: list[tuple[int, int, str]] = []
    for index, paragraph in enumerate(paragraphs):
        lowered = paragraph.lower()
        score = sum(2 if " " in keyword else 1 for keyword in keywords if keyword in lowered)
        if any(marker in lowered for marker in ("chief executive", "ceo", "current trading", "outlook")):
            score += 3
        if score:
            scored.append((score, -index, paragraph))

    selected = [head]
    used = len(head)
    seen = {head}
    for _, _, paragraph in sorted(scored, reverse=True):
        if paragraph in seen:
            continue
        remaining = budget - used - 2
        if remaining <= 200:
            break
        chunk = paragraph[:remaining]
        selected.append(chunk)
        seen.add(paragraph)
        used += len(chunk) + 2
    return "\n\n".join(selected)[:budget]


def _build_prompt(expectation: EventExpectation, document: ReleaseDocument) -> str:
    expectation_json = json.dumps(asdict(expectation), default=str, ensure_ascii=False)
    allowed_names = json.dumps(_allowed_metric_names(expectation), ensure_ascii=False)
    release_text = _select_release_text(expectation, document.raw_text)
    return f"""Analyze this official company results release for an event-trading system.

Rules:
- Extract only facts supported by the supplied official release excerpt.
- Metrics must use ONLY the exact canonical names in ALLOWED METRIC NAMES below. Do not invent, translate, prettify or rename metric names.
- Omit a metric if none of the allowed canonical names fit it; describe other observations in the summaries instead.
- Return metrics as objects with name, value, and unit. Use null for an unknown value or unit.
- Compare against the PRE-EVENT expectation snapshot; do not rewrite the expectation.
- FY26 results already pre-guided should not be treated as a fresh catalyst by themselves.
- Give separate fundamental and catalyst assessments.
- A single miss/beat must not decide a trade.
- Do not propose position size, order execution, leverage, or bypass risk controls.
- Evidence quotes must be short excerpts from the supplied release.
- If evidence is incomplete or contradictory, use MIXED/NEUTRAL and list uncertainties.

ALLOWED METRIC NAMES:
{allowed_names}

PRE-EVENT EXPECTATION:
{expectation_json}

OFFICIAL RELEASE TITLE: {document.source_title}
OFFICIAL RELEASE URL: {document.source_url}
OFFICIAL RELEASE EXCERPT:
{release_text}
"""


def _post_json(url: str, body: dict[str, Any], *, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    try:
        response = requests.post(
            url,
            json=body,
            headers={"Accept": "application/json", "User-Agent": "MarketAI/0.1", **headers},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"AI provider request failed: {exc}") from exc
    if not response.ok:
        raise RuntimeError(f"AI provider HTTP {response.status_code}: {response.text[:2000]}")
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("AI provider returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("AI provider returned an unexpected response shape")
    return data


class GroqEventAnalyzer:
    def __init__(self, *, api_key: str | None = None, model: str | None = None, base_url: str = "https://api.groq.com/openai/v1", timeout_seconds: int = 180) -> None:
        self.api_key = api_key or os.environ.get("GROQ_API_KEY") or ""
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is required for the Groq analyzer")
        self.model = model or os.environ.get("MARKETAI_GROQ_MODEL", "openai/gpt-oss-120b")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def analyze(self, expectation: EventExpectation, document: ReleaseDocument) -> AIEventAnalysis:
        response = _post_json(
            f"{self.base_url}/chat/completions",
            {
                "model": self.model,
                "messages": [{"role": "user", "content": _build_prompt(expectation, document)}],
                "temperature": 0,
                "response_format": {"type": "json_schema", "json_schema": {"name": "event_analysis", "strict": True, "schema": _event_analysis_schema(expectation)}},
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout_seconds,
        )
        choices = response.get("choices") or []
        raw = str(((choices[0].get("message") if choices else {}) or {}).get("content") or "")
        if not raw:
            raise RuntimeError("Groq returned an empty analysis")
        return AIEventAnalysis(payload=_parse_payload(raw, expectation), provider="groq", model=self.model, raw_response=raw)


class OllamaEventAnalyzer:
    def __init__(self, *, model: str | None = None, base_url: str | None = None, timeout_seconds: int = 180) -> None:
        self.model = model or os.environ.get("MARKETAI_OLLAMA_MODEL", "gpt-oss:20b")
        self.base_url = (base_url or os.environ.get("MARKETAI_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def analyze(self, expectation: EventExpectation, document: ReleaseDocument) -> AIEventAnalysis:
        response = _post_json(
            f"{self.base_url}/api/chat",
            {"model": self.model, "messages": [{"role": "user", "content": _build_prompt(expectation, document)}], "stream": False, "format": _event_analysis_schema(expectation), "options": {"temperature": 0}},
            headers={},
            timeout=self.timeout_seconds,
        )
        raw = str((response.get("message") or {}).get("content") or "")
        if not raw:
            raise RuntimeError("Ollama returned an empty analysis")
        return AIEventAnalysis(payload=_parse_payload(raw, expectation), provider="ollama", model=self.model, raw_response=raw)


class OpenAIEventAnalyzer:
    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError("OpenAI fallback requires the optional 'openai' Python package") from exc
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is required for the OpenAI analyzer")
            client = OpenAI(api_key=api_key)
        self.client = client
        self.model = model or os.environ.get("MARKETAI_OPENAI_MODEL", "gpt-5.6")

    def analyze(self, expectation: EventExpectation, document: ReleaseDocument) -> AIEventAnalysis:
        response = self.client.responses.create(
            model=self.model,
            input=_build_prompt(expectation, document),
            text={"format": {"type": "json_schema", "name": "event_analysis", "strict": True, "schema": _event_analysis_schema(expectation)}},
        )
        raw = response.output_text
        return AIEventAnalysis(payload=_parse_payload(raw, expectation), provider="openai", model=self.model, raw_response=raw)


class FallbackEventAnalyzer:
    def __init__(self, analyzers: list[EventAnalyzer]) -> None:
        if not analyzers:
            raise ValueError("At least one event analyzer is required")
        self.analyzers = analyzers

    def analyze(self, expectation: EventExpectation, document: ReleaseDocument) -> AIEventAnalysis:
        errors: list[str] = []
        for analyzer in self.analyzers:
            try:
                return analyzer.analyze(expectation, document)
            except Exception as exc:
                errors.append(f"{type(analyzer).__name__}: {exc}")
        raise RuntimeError("All event analyzers failed: " + " | ".join(errors))


def build_default_event_analyzer() -> EventAnalyzer:
    analyzers: list[EventAnalyzer] = []
    if os.environ.get("GROQ_API_KEY"):
        analyzers.append(GroqEventAnalyzer())
    if os.environ.get("MARKETAI_OLLAMA_ENABLED", "true").lower() not in {"0", "false", "no"}:
        analyzers.append(OllamaEventAnalyzer())
    if os.environ.get("MARKETAI_ENABLE_OPENAI_FALLBACK", "false").lower() in {"1", "true", "yes"} and os.environ.get("OPENAI_API_KEY"):
        analyzers.append(OpenAIEventAnalyzer())
    if not analyzers:
        raise RuntimeError("No AI provider configured. Set GROQ_API_KEY or enable/configure Ollama.")
    return FallbackEventAnalyzer(analyzers)
