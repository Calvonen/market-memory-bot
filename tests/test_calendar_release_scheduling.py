from pathlib import Path
import unittest


WORKFLOW = Path(".github/workflows/deploy-seesam-hub.yml")
SERVICE = Path("deploy/systemd/marketai-calendar-release.service")
TIMER = Path("deploy/systemd/marketai-calendar-release.timer")


class CalendarReleaseSchedulingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.service = SERVICE.read_text(encoding="utf-8")
        cls.timer = TIMER.read_text(encoding="utf-8")

    def _deploy_body(self) -> str:
        start = self.workflow.index("Deploy backend to seesam-hub (locked)")
        end = self.workflow.index("\n  publish-ota:", start)
        return self.workflow[start:end]

    def test_service_is_one_shot_ingestion_only_worker(self) -> None:
        self.assertIn("Type=oneshot", self.service)
        self.assertIn(
            "ExecStart=/home/marko/marketai-repo/.venv/bin/python -m trading_system.calendar_release_worker",
            self.service,
        )
        for forbidden in ("post_release_paper", "Strategy", "RiskEngine", "PaperBroker"):
            self.assertNotIn(forbidden, self.service)

    def test_timer_polls_every_two_minutes_and_survives_downtime(self) -> None:
        self.assertIn("OnUnitInactiveSec=120s", self.timer)
        self.assertIn("Persistent=true", self.timer)
        self.assertIn("Unit=marketai-calendar-release.service", self.timer)
        self.assertIn("WantedBy=timers.target", self.timer)

    def test_deploy_installs_enables_and_immediately_runs_release_worker(self) -> None:
        body = self._deploy_body()
        self.assertIn(
            "install -m 0644 deploy/systemd/marketai-calendar-release.service /etc/systemd/system/marketai-calendar-release.service",
            body,
        )
        self.assertIn(
            "install -m 0644 deploy/systemd/marketai-calendar-release.timer /etc/systemd/system/marketai-calendar-release.timer",
            body,
        )
        self.assertIn("systemctl enable --now marketai-calendar-release.timer", body)
        self.assertIn("systemctl start marketai-calendar-release.service", body)

    def test_release_worker_wiring_is_after_schema_gate(self) -> None:
        body = self._deploy_body()
        gate = body.index("scripts/verify_supabase_schema.py")
        install = body.index("deploy/systemd/marketai-calendar-release.service")
        enable = body.index("systemctl enable --now marketai-calendar-release.timer")
        run_now = body.index("systemctl start marketai-calendar-release.service")
        self.assertLess(gate, install)
        self.assertLess(install, enable)
        self.assertLess(enable, run_now)

    def test_timer_health_is_fail_closed_before_success_is_recorded(self) -> None:
        body = self._deploy_body()
        health = body.index("systemctl is-active --quiet marketai-calendar-release.timer")
        success_record = body.index("last-deployed-backend.sha")
        self.assertLess(health, success_record)
        segment = body[body.index("marketai-calendar-release.timer", health - 200):success_record]
        self.assertNotIn("|| true", segment)
        self.assertNotIn("set +e", segment)


if __name__ == "__main__":
    unittest.main()
