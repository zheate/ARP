from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

from combined_test.runtime_logging import (
    LOG_DIRECTORY_ENV,
    configure_runtime_logging,
    default_runtime_log_directory,
    get_runtime_logger,
    runtime_log_path,
    shutdown_runtime_logging,
)


class RuntimeLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        shutdown_runtime_logging()

    def tearDown(self) -> None:
        shutdown_runtime_logging()

    def test_configure_writes_utf8_events_and_process_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = configure_runtime_logging(directory, process_role="test-backend")
            self.assertIsNotNone(path)
            assert path is not None
            self.assertEqual(runtime_log_path(), path)

            get_runtime_logger("test").info("I2C 通信异常：测试消息")
            for handler in get_runtime_logger().handlers:
                handler.flush()

            text = path.read_text(encoding="utf-8")
            self.assertIn("运行日志已启动", text)
            self.assertIn("role=test-backend", text)
            self.assertIn("I2C 通信异常：测试消息", text)
            self.assertIn("thread=MainThread", text)
            shutdown_runtime_logging()

    def test_repeated_configuration_reuses_the_same_process_file(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_path = configure_runtime_logging(first)
            second_path = configure_runtime_logging(second)

            self.assertEqual(second_path, first_path)
            self.assertEqual(len(list(Path(first).glob("*.log"))), 1)
            self.assertEqual(len(list(Path(second).glob("*.log"))), 0)
            shutdown_runtime_logging()

    def test_environment_override_selects_log_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            selected = default_runtime_log_directory({LOG_DIRECTORY_ENV: directory})

            self.assertEqual(selected, Path(directory))

    def test_clean_old_logs_removes_expired_files(self) -> None:
        import os
        import time
        with tempfile.TemporaryDirectory() as directory:
            dir_path = Path(directory)
            old_log = dir_path / "backend_20200101_000000_pid100.log"
            old_log.write_text("old", encoding="utf-8")
            # Set mtime to 35 days ago
            past_time = time.time() - 35 * 86400
            os.utime(old_log, (past_time, past_time))

            recent_log = dir_path / "backend_20260816_000000_pid200.log"
            recent_log.write_text("recent", encoding="utf-8")

            path = configure_runtime_logging(directory)
            self.assertIsNotNone(path)
            self.assertFalse(old_log.exists())
            self.assertTrue(recent_log.exists())
            shutdown_runtime_logging()

    def test_unhandled_exception_hook_records_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_hook = sys.excepthook
            forwarded: list[str] = []

            def forwarding_hook(
                _exc_type: type[BaseException],
                exc_value: BaseException,
                _traceback: object,
            ) -> None:
                forwarded.append(str(exc_value))

            sys.excepthook = forwarding_hook
            try:
                path = configure_runtime_logging(directory)
                self.assertIsNotNone(path)
                assert path is not None
                try:
                    raise RuntimeError("未处理异常测试")
                except RuntimeError:
                    exc_type, exc_value, traceback = sys.exc_info()
                    assert exc_type is not None and exc_value is not None
                    sys.excepthook(exc_type, exc_value, traceback)
                for handler in get_runtime_logger().handlers:
                    handler.flush()

                text = path.read_text(encoding="utf-8")
                self.assertIn("主线程出现未处理异常", text)
                self.assertIn("RuntimeError: 未处理异常测试", text)
                self.assertEqual(forwarded, ["未处理异常测试"])
            finally:
                shutdown_runtime_logging()
                sys.excepthook = previous_hook


if __name__ == "__main__":
    logging.raiseExceptions = True
    unittest.main()
