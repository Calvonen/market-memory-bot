from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel, Field

from trading_system.models import EventExpectation
from trading_system.release_ingestion import ReleaseDocument


class EventAnalysisPayload(BaseModel):
    metrics: dict[str, float | str | None]
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


@dataclass(frozen=True)
class AIEventAnalysis:
    payload: EventAnalysisPayload
    model: str
    raw_response: str


class OpenAIEventAnalyzer:
    """Structured event analysis. It can score evidence but cannot size or execute trades."""

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self.client = client
        self.model = model or os.environ.get("MARKETAI_OPENAI_MODEL", "gpt-5.6")

    def analyze(
        self,
        expectation: EventExpectation,
        document: ReleaseDocument,
    ) -> AIEventAnalysis:
        expectation_json = json.dumps(asdict(expectation), default=str, ensure_ascii=False)
        release_text = document.raw_text[:100_000]
        prompt = f"""Analyze this official company results release for an event-trading system.

Rules:
- Extract only facts supported by the supplied official release.
- Compare against the PRE-EVENT expectation snapshot; do not rewrite the expectation.
- FY26 results already pre-guided should not be treated as a fresh catalyst by themselves.
- Give separate fundamental and catalyst assessments.
- A single miss/beat must not decide a trade.
- Do not propose position size, order execution, leverage, or bypass risk controls.
- Evidence quotes must be short excerpts from the supplied release.

PRE-EVENT EXPECTATION:
{expectation_json}

OFFICIAL RELEASE TITLE: {document.source_title}
OFFICIAL RELEASE URL: {document.source_url}
OFFICIAL RELEASE TEXT:
{release_text}
"""
        schema = EventAnalysisPayload.model_json_schema()
        response = self.client.responses.create(
            model=self.model,
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "event_analysis",
                    "strict": True,
                    "schema": schema,
                }
            },
        )
        raw = response.output_text
        payload = EventAnalysisPayload.model_validate_json(raw)
        return AIEventAnalysis(payload=payload, model=self.model, raw_response=raw)
