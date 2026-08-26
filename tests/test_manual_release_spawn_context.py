from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from trading_system.manual_release_ingestion import ManualOfficialReleaseProvider


class ManualOfficialReleaseSpawnContextTests(unittest.TestCase):
    def test_pdf_extraction_uses_fresh_spawn_context(self) -> None:
        fake_context = MagicMock()
        recv_conn = MagicMock()
        send_conn = MagicMock()
        process = MagicMock()
        fake_context.Pipe.return_value = (recv_conn, send_conn)
        fake_context.Process.return_value = process
        recv_conn.poll.return_value = False

        with patch(
            "trading_system.manual_release_ingestion.multiprocessing.get_context",
            return_value=fake_context,
        ) as get_context:
            with self.assertRaisesRegex(RuntimeError, "exceeded resource limit"):
                ManualOfficialReleaseProvider._extract_pdf_text(b"%PDF-1.7")

        get_context.assert_called_once_with("spawn")
        process.terminate.assert_called_once()
        process.join.assert_called()


if __name__ == "__main__":
    unittest.main()
