from __future__ import annotations

import resource
import unittest
from unittest.mock import MagicMock, patch

from trading_system.manual_release_ingestion import (
    ManualOfficialReleaseProvider,
    _pdf_extract_worker,
)


class ManualReleaseResourceLimitTests(unittest.TestCase):
    def test_pdf_worker_preserves_tighter_inherited_address_space_soft_limit(self) -> None:
        send_conn = MagicMock()
        baseline = 1_000_000_000
        inherited_soft = baseline + 128 * 1024 * 1024
        inherited_hard = baseline + 512 * 1024 * 1024

        with (
            patch("trading_system.manual_release_ingestion._current_virtual_memory_bytes", return_value=baseline),
            patch("resource.getrlimit", side_effect=[
                (inherited_soft, inherited_hard),
                (resource.RLIM_INFINITY, resource.RLIM_INFINITY),
            ]),
            patch("resource.setrlimit") as setrlimit,
            patch.object(ManualOfficialReleaseProvider, "_extract_pdf_text_in_process", return_value="results"),
        ):
            _pdf_extract_worker(b"%PDF-1.7", send_conn, 10, 1000, 1024 * 1024 * 1024, 10)

        setrlimit.assert_any_call(resource.RLIMIT_AS, (inherited_soft, inherited_hard))
        send_conn.send.assert_called_once_with(("ok", "results"))

    def test_pdf_worker_preserves_tighter_inherited_cpu_limits(self) -> None:
        send_conn = MagicMock()
        baseline = 1_000_000_000
        inherited_cpu_soft = 4
        inherited_cpu_hard = 6

        with (
            patch("trading_system.manual_release_ingestion._current_virtual_memory_bytes", return_value=baseline),
            patch("resource.getrlimit", side_effect=[
                (resource.RLIM_INFINITY, resource.RLIM_INFINITY),
                (inherited_cpu_soft, inherited_cpu_hard),
            ]),
            patch("resource.setrlimit") as setrlimit,
            patch.object(ManualOfficialReleaseProvider, "_extract_pdf_text_in_process", return_value="results"),
        ):
            _pdf_extract_worker(b"%PDF-1.7", send_conn, 10, 1000, 1024 * 1024 * 1024, 10)

        setrlimit.assert_any_call(resource.RLIMIT_CPU, (inherited_cpu_soft, inherited_cpu_hard))
        send_conn.send.assert_called_once_with(("ok", "results"))

    def test_no_address_space_headroom_diagnostic_is_propagated(self) -> None:
        fake_context = MagicMock()
        recv_conn = MagicMock()
        send_conn = MagicMock()
        process = MagicMock()
        fake_context.Pipe.return_value = (recv_conn, send_conn)
        fake_context.Process.return_value = process
        recv_conn.poll.return_value = True
        recv_conn.recv.return_value = (
            "error",
            "RuntimeError: PDF worker inherited address-space limit leaves no allocation headroom",
        )

        with patch("trading_system.manual_release_ingestion.multiprocessing.get_context", return_value=fake_context):
            with self.assertRaisesRegex(RuntimeError, "inherited address-space limit leaves no allocation headroom"):
                ManualOfficialReleaseProvider._extract_pdf_text(b"%PDF-1.7")


if __name__ == "__main__":
    unittest.main()
