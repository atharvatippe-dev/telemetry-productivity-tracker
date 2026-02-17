"""
Linux platform collector.

Uses:
  • xdotool / wmctrl for active window detection
  • pynput for keystroke / mouse counting (no content logged)
  • xprintidle for idle time
"""

from __future__ import annotations

import logging
import subprocess
import threading

from tracker.platform.base import PlatformCollector

logger = logging.getLogger("tracker.linux")


class LinuxCollector(PlatformCollector):
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
        """
        Get the active window on Linux using xdotool.
        Falls back to wmctrl if xdotool is unavailable.
        """
        window_title = ""
        app_name = "unknown"

        # Try xdotool first
        try:
            wid = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()

            if wid:
                name_result = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowpid"],
                    capture_output=True, text=True, timeout=2,
                )
                pid = name_result.stdout.strip()

                title_result = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowname"],
                    capture_output=True, text=True, timeout=2,
                )
                window_title = title_result.stdout.strip()

                # Get process name from pid
                if pid:
                    try:
                        import psutil
                        proc = psutil.Process(int(pid))
                        app_name = proc.name()
                    except Exception:
                        # Try reading /proc/<pid>/comm
                        try:
                            comm_result = subprocess.run(
                                ["cat", f"/proc/{pid}/comm"],
                                capture_output=True, text=True, timeout=1,
                            )
                            app_name = comm_result.stdout.strip() or "unknown"
                        except Exception:
                            pass

            return app_name, window_title

        except FileNotFoundError:
            logger.debug("xdotool not found, trying wmctrl.")

        # Fallback: wmctrl
        try:
            result = subprocess.run(
                ["wmctrl", "-l", "-p"],
                capture_output=True, text=True, timeout=2,
            )
            # Parse wmctrl output — active window is typically the last focused
            # This is a rough heuristic
            for line in result.stdout.strip().split("\n"):
                parts = line.split(None, 4)
                if len(parts) >= 5:
                    window_title = parts[4]
                    app_name = "unknown"
            return app_name, window_title
        except FileNotFoundError:
            logger.warning("Neither xdotool nor wmctrl found. Install one for window detection.")
            return "unknown", ""

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
        logger.info("Linux input listeners started.")

    def stop_input_listener(self) -> None:
        if self._kb_listener:
            self._kb_listener.stop()
        if self._mouse_listener:
            self._mouse_listener.stop()
        logger.info("Linux input listeners stopped.")

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
        Use xprintidle to get idle time on Linux (milliseconds).
        Falls back to 0.0 if unavailable.
        """
        try:
            result = subprocess.run(
                ["xprintidle"],
                capture_output=True, text=True, timeout=2,
            )
            millis = int(result.stdout.strip())
            return millis / 1000.0
        except (FileNotFoundError, ValueError):
            logger.debug("xprintidle not available — idle detection disabled.")
            return 0.0
