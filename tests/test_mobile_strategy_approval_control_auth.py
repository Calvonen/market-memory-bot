import unittest
from pathlib import Path


API_PATH = Path("mobile/src/services/api.ts")


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    end = source.index("\n}\n", start)
    return source[start:end]


class MobileStrategyApprovalControlAuthTests(unittest.TestCase):
    def test_strategy_approval_uses_control_post_helper_and_helper_injects_control_key(self) -> None:
        source = API_PATH.read_text(encoding="utf-8")

        approve_body = _function_body(source, "export function approveStrategyDraft(")
        self.assertIn("return apiControlPost<StrategyDraftApprovalResult>(", approve_body)
        self.assertNotIn("apiPost<StrategyDraftApprovalResult>", approve_body)
        self.assertNotIn("X-MarketAI-Key", approve_body)

        control_helper = _function_body(source, "export async function apiControlPost<")
        self.assertIn("'X-MarketAI-Control-Key': CONTROL_API_KEY", control_helper)


if __name__ == "__main__":
    unittest.main()
