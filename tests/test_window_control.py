"""Regression tests for stale window handles and safe target reacquisition."""
import unittest
from unittest.mock import patch

import window_control


class WindowRecoveryTests(unittest.TestCase):
    def test_original_window_is_preserved(self):
        with patch.object(window_control, "window_pid", return_value=1234):
            with patch.object(window_control, "window_title", return_value="Minecraft"):
                hwnd, reason = window_control.restore_same_process_window(10, 1234)
        self.assertEqual((hwnd, reason), (10, ""))

    def test_reacquire_only_unique_window_in_same_process(self):
        with patch.object(window_control, "window_pid", side_effect=lambda hwnd: {10: 0, 20: 1234, 30: 5678}.get(hwnd, 0)):
            with patch.object(window_control, "list_windows", return_value=[
                (20, "Minecraft"), (30, "Browser")]):
                hwnd, reason = window_control.restore_same_process_window(10, 1234)
        self.assertEqual(hwnd, 20)
        self.assertTrue(reason)

    def test_never_select_unrelated_process(self):
        with patch.object(window_control, "window_pid", side_effect=lambda hwnd: {10: 0, 30: 5678}.get(hwnd, 0)):
            with patch.object(window_control, "list_windows", return_value=[(30, "Minecraft")]):
                hwnd, _ = window_control.restore_same_process_window(10, 1234)
        self.assertIsNone(hwnd)

    def test_ambiguous_windows_require_manual_selection(self):
        with patch.object(window_control, "window_pid", side_effect=lambda hwnd: {10: 0, 20: 1234, 21: 1234}.get(hwnd, 0)):
            with patch.object(window_control, "list_windows", return_value=[
                (20, "Minecraft"), (21, "Minecraft chat")]):
                hwnd, _ = window_control.restore_same_process_window(10, 1234)
        self.assertIsNone(hwnd)


if __name__ == "__main__":
    unittest.main()
