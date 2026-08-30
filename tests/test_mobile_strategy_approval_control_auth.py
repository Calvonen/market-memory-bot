from pathlib import Path


API_PATH = Path("mobile/src/services/api.ts")


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    end = source.index("\n}\n", start)
    return source[start:end]


def test_strategy_approval_uses_control_post_helper_and_helper_injects_control_key() -> None:
    source = API_PATH.read_text(encoding="utf-8")

    approve_body = _function_body(source, "export function approveStrategyDraft(")
    assert "return apiControlPost<StrategyDraftApprovalResult>(" in approve_body
    assert "apiPost<StrategyDraftApprovalResult>" not in approve_body
    assert "X-MarketAI-Key" not in approve_body

    control_helper = _function_body(source, "export async function apiControlPost<")
    assert "'X-MarketAI-Control-Key': CONTROL_API_KEY" in control_helper
