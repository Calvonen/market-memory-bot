from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Protocol

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


class EventAnalyzer(Protocol):
    def analyze(
        self,
        expectation: EventExpectation,
        document: ReleaseDocument,
    ) -> AIEventAnalysis: ...


def _build_prompt(expectation: EventExpectation, document: ReleaseDocument) -> str:
    expectation_json = json.dumps(asdict(expectation), default=str, ensure_ascii=False)
    release_text = document.raw_text[:100_000]
    return f"""Analyze this official company results release for an event-trading system.

Rules:
- Extract only facts supported by the supplied official release.
- Return metrics as objects with name, value, and unit. Use null for an unknown value or unit.
- Compare against the PRE-EVENT expectation snapshot; do not rewrite the expectation.
- FY26 results already pre-guided should not be treated as a fresh catalyst by themselves.
- Give separate fundamental and catalyst assessments.
- A single miss/beat must not decide a trade.
- Do not propose position size, order execution, leverage, or bypass risk controls.
- Evidence quotes must be short excerpts from the supplied release.
- If evidence is incomplete or contradictory, use MIXED/NEUTRAL and list uncertainties.

PRE-EVENT EXPECTATION:
{expectation_json}

OFFICIAL RELEASE TITLE: {document.source_title}
OFFICIAL RELEASE URL: {document.source_url}
OFFICIAL RELEASE TEXT:
{release_text}
"""


def _post_json(url: str, body: dict[str, Any], *, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"AI provider HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"AI provider request failed: {exc}") from exc


class GroqEventAnalyzer:
    """Default cloud analyzer using Groq JSON Schema structured output."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: int = 180,
    ) -> None:
        self.api_key = api_key or os.environ.get("GROQ_API_KEY") or ""
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is required for the Groq analyzer")
        self.model = model or os.environ.get("MARKETAI_GROQ_MODEL", "openai/gpt-oss-120b")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def analyze(
        self,
        expectation: EventExpectation,
        document: ReleaseDocument,
    ) -> AIEventAnalysis:
        schema = EventAnalysisPayload.model_json_schema()
        response = _post_json(
            f"{self.base_url}/chat/completions",
            {
                "model": self.model,
                "messages": [{"role": "user", "content": _build_prompt(expectation, document)}],
                "temperature": 0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "event_analysis",
                        "strict": True,
                        "schema": schema,
                    },
                },
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout_seconds,
        )
        choices = response.get("choices") or []
        raw = str(((choices[0].get("message") if choices else {}) or {}).get("content") or "")
        if not raw:
            raise RuntimeError("Groq returned an empty analysis")
        payload = EventAnalysisPayload.model_validate_json(raw)
        return AIEventAnalysis(payload=payload, provider="groq", model=self.model, raw_response=raw)


class OllamaEventAnalyzer:
    """Local JSON-schema fallback using Ollama's HTTP API."""

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        self.model = model or os.environ.get("MARKETAI_OLLAMA_MODEL", "gpt-oss:20b")
        self.base_url = (
            base_url or os.environ.get("MARKETAI_OLLAMA_URL", "http://localhost:11434")
        ).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def analyze(
        self,
        expectation: EventExpectation,
        document: ReleaseDocument,
    ) -> AIEventAnalysis:
        response = _post_json(
            f"{self.base_url}/api/chat",
            {
                "model": self.model,
                "messages": [{"role": "user", "content": _build_prompt(expectation, document)}],
                "stream": False,
                "format": EventAnalysisPayload.model_json_schema(),
                "options": {"temperature": 0},
            },
            headers={},
            timeout=self.timeout_seconds,
        )
        raw = str((response.get("message") or {}).get("content") or "")
        if not raw:
            raise RuntimeError("Ollama returned an empty analysis")
        payload = EventAnalysisPayload.model_validate_json(raw)
        return AIEventAnalysis(payload=payload, provider="ollama", model=self.model, raw_response=raw)


class OpenAIEventAnalyzer:
    """Optional paid-provider adapter. Imported only when explicitly enabled."""

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "OpenAI fallback requires the optional 'openai' Python package"
                ) from exc
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is required for the OpenAI analyzer")
            client = OpenAI(api_key=api_key)
        self.client = client
        self.model = model or os.environ.get("MARKETAI_OPENAI_MODEL", "gpt-5.6")

    def analyze(
        self,
        expectation: EventExpectation,
        document: ReleaseDocument,
    ) -> AIEventAnalysis:
        response = self.client.responses.create(
            model=self.model,
            input=_build_prompt(expectation, document),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "event_analysis",
                    "strict": True,
                    "schema": EventAnalysisPayload.model_json_schema(),
                }
            },
        )
        raw = response.output_text
        payload = EventAnalysisPayload.model_validate_json(raw)
        return AIEventAnalysis(payload=payload, provider="openai", model=self.model, raw_response=raw)


class FallbackEventAnalyzer:
    """Try providers in order; fail only after every configured analyzer fails."""

    def __init__(self, analyzers: list[EventAnalyzer]) -> None:
        if not analyzers:
            raise ValueError("At least one event analyzer is required")
        self.analyzers = analyzers

    def analyze(
        self,
        expectation: EventExpectation,
        document: ReleaseDocument,
    ) -> AIEventAnalysis:
        errors: list[str] = []
        for analyzer in self.analyzers:
            try:
                return analyzer.analyze(expectation, document)
            except Exception as exc:
                errors.append(f"{type(analyzer).__name__}: {exc}")
        raise RuntimeError("All event analyzers failed: " + " | ".join(errors))


def build_default_event_analyzer() -> EventAnalyzer:
    """Groq first, local Ollama second; OpenAI is opt-in only."""
    analyzers: list[EventAnalyzer] = []
    if os.environ.get("GROQ_API_KEY"):
        analyzers.append(GroqEventAnalyzer())

    ollama_enabled = os.environ.get("MARKETAI_OLLAMA_ENABLED", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    if ollama_enabled:
        analyzers.append(OllamaEventAnalyzer())

    if os.environ.get("MARKETAI_ENABLE_OPENAI_FALLBACK", "false").lower() in {
        "1",
        "true",
        "yes",
    } and os.environ.get("OPENAI_API_KEY"):
        analyzers.append(OpenAIEventAnalyzer())

    if not analyzers:
        raise RuntimeError(
            "No AI provider configured. Set GROQ_API_KEY or enable/configure Ollama."
        )
    return FallbackEventAnalyzer(analyzers)
