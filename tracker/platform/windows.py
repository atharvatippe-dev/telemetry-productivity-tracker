"""
Windows platform collector.

Uses:
  - win32gui / win32process / psutil for active window detection
  - win32gui.EnumWindows for visible window enumeration (distraction detection)
  - pynput for keystroke / mouse counting (no content logged)
  - ctypes GetLastInputInfo for idle time
"""

from __future__ import annotations

import ctypes
import logging
import threading

from tracker.platform.base import PlatformCollector

logger = logging.getLogger("tracker.windows")


class WindowsCollector(PlatformCollector):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keystroke_count = 0
        self._mouse_clicks = 0
        self._mouse_distance = 0.0
        self._last_mouse_pos: tuple[int, int] | None = None
        self._kb_listener = None
        self._mouse_listener = None

    # ── Active window ───────────────────────────────────────────
    def get_active_window(self) -> tuple[str, str]:
        """Get the foreground window's app name and title on Windows."""
        try:
            import win32gui
            import win32process
            import psutil

            hwnd = win32gui.GetForegroundWindow()
            window_title = win32gui.GetWindowText(hwnd) or ""
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                proc = psutil.Process(pid)
                app_name = proc.name().replace(".exe", "")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                app_name = "unknown"
            return app_name, window_title
        except ImportError:
            return self._get_active_window_ctypes()

    @staticmethod
    def _get_active_window_ctypes() -> tuple[str, str]:
        """Fallback using only ctypes."""
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return "unknown", buf.value or ""
        except Exception:
            return "unknown", ""

    # ── Visible windows (multi-monitor / split-screen / PiP) ───
    def get_visible_windows(self) -> list[tuple[str, str]]:
        """
        Enumerate ALL visible on-screen windows across every monitor
        using win32gui.EnumWindows. Filters out invisible and tiny windows.

        Returns a list of (app_name, window_title) pairs.
        """
        try:
            import win32gui
            import win32process
            import psutil
        except ImportError:
            logger.debug("win32gui/psutil not available — visible window enumeration disabled.")
            return []

        result: list[tuple[str, str]] = []

        def _enum_callback(hwnd, _):
            # Skip invisible windows
            if not win32gui.IsWindowVisible(hwnd):
                return
            # Skip minimized windows
            if win32gui.IsIconic(hwnd):
                return

            title = win32gui.GetWindowText(hwnd) or ""
            if not title:
                return

            # Skip tiny windows (toolbars, tray icons, tooltips)
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                width = right - left
                height = bottom - top
                if width < 100 or height < 100:
                    return
            except Exception:
                return

            # Get process name
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc = psutil.Process(pid)
                app_name = proc.name().replace(".exe", "")
            except Exception:
                app_name = "unknown"

            result.append((app_name, title))

        try:
            win32gui.EnumWindows(_enum_callback, None)
        except Exception as exc:
            logger.debug("EnumWindows failed: %s", exc)

        return result

    # ── Input listener ──────────────────────────────────────────
    def start_input_listener(self) -> None:
        try:
            from pynput import keyboard, mouse
        except ImportError:
            logger.error("pynput is required. Install with: pip install pynput")
            return

        def on_key_press(key):
            with self._lock:
                self._keystroke_count += 1

        def on_click(x, y, button, pressed):
            if pressed:
                with self._lock:
                    self._mouse_clicks += 1

        def on_move(x, y):
            with self._lock:
                if self._last_mouse_pos is not None:
                    dx = x - self._last_mouse_pos[0]
                    dy = y - self._last_mouse_pos[1]
                    self._mouse_distance += (dx ** 2 + dy ** 2) ** 0.5
                self._last_mouse_pos = (x, y)

        self._kb_listener = keyboard.Listener(on_press=on_key_press)
        self._mouse_listener = mouse.Listener(on_click=on_click, on_move=on_move)
        self._kb_listener.start()
        self._mouse_listener.start()
        logger.info("Windows input listeners started.")

    def stop_input_listener(self) -> None:
        if self._kb_listener:
            self._kb_listener.stop()
        if self._mouse_listener:
            self._mouse_listener.stop()
        logger.info("Windows input listeners stopped.")

    def get_and_reset_counts(self) -> dict:
        with self._lock:
            counts = {
                "keystroke_count": self._keystroke_count,
                "mouse_clicks": self._mouse_clicks,
                "mouse_distance": self._mouse_distance,
            }
            self._keystroke_count = 0
            self._mouse_clicks = 0
            self._mouse_distance = 0.0
            return counts

    # ── Idle time ───────────────────────────────────────────────
    def get_idle_seconds(self) -> float:
        """
        Use GetLastInputInfo to determine idle time on Windows.
        """
        try:
            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("dwTime", ctypes.c_uint),
                ]

            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):  # type: ignore[attr-defined]
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime  # type: ignore[attr-defined]
                return millis / 1000.0
        except Exception as exc:
            logger.warning("Idle detection failed: %s", exc)
        return 0.0
