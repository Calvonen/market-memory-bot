import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/deploy-seesam-hub.yml")
RELEASE_WORKER = Path("trading_system/release_worker.py")


class HaysReleaseDeployHealthGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.worker = RELEASE_WORKER.read_text(encoding="utf-8")

    def _deploy_step(self) -> str:
        start = self.workflow.index("Deploy backend to seesam-hub (locked)")
        end = self.workflow.index("\n  publish-ota:", start)
        return self.workflow[start:end]

    def test_worker_can_exit_cleanly_after_terminal_paper_state(self) -> None:
        self.assertIn("terminal_status = _terminal_paper_status", self.worker)
        self.assertIn("exiting without re-running the pipeline", self.worker)
        terminal_block = self.worker.index("terminal_status = _terminal_paper_status")
        loop_start = self.worker.index("while True:", terminal_block)
        self.assertIn("return", self.worker[terminal_block:loop_start])

    def test_deploy_does_not_treat_clean_inactive_worker_as_failure(self) -> None:
        body = self._deploy_step()
        self.assertIn(
            "if ! /usr/bin/systemctl is-active --quiet marketai-hays-release.service; then",
            body,
        )
        self.assertIn(
            "/usr/bin/systemctl show marketai-hays-release.service --property=Result --value",
            body,
        )
        self.assertIn('if [ "$HAYS_RESULT" != "success" ]; then', body)
        self.assertIn("exited cleanly after terminal event state", body)

    def test_actual_hays_worker_failure_still_fails_closed(self) -> None:
        body = self._deploy_step()
        result_check = body.index('if [ "$HAYS_RESULT" != "success" ]; then')
        state_write = body.index('printf \'%s\\n\' "$DEPLOY_SHA"')
        failure_block = body[result_check:state_write]
        self.assertIn("exit 1", failure_block)
        self.assertIn(
            "systemctl status marketai-hays-release.service --no-pager -l || true",
            failure_block,
        )

    def test_deployed_sha_is_recorded_only_after_release_worker_gate(self) -> None:
        body = self._deploy_step()
        gate_index = body.index("HAYS_RESULT=")
        state_write_index = body.index('printf \'%s\\n\' "$DEPLOY_SHA"')
        self.assertLess(gate_index, state_write_index)


if __name__ == "__main__":
    unittest.main()
