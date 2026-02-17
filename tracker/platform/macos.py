"""
macOS platform collector.

Uses:
  • AppKit (pyobjc) for active window detection
  • pynput for keystroke / mouse counting (no content logged)
  • Quartz for idle time
"""

from __future__ import annotations

import logging
import subprocess
import threading

from tracker.platform.base import PlatformCollector

logger = logging.getLogger("tracker.macos")


class MacOSCollector(PlatformCollector):
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
        Get the frontmost application name and window title on macOS.

        Uses the Accessibility API (AXUIElement) which works reliably for
        virtually all apps including Electron apps (Cursor, VS Code, Chrome)
        and native apps (Safari, Finder, Terminal).

        Falls back to AppleScript if the AX API is unavailable.
        """
        app_name = "unknown"
        window_title = ""

        try:
            from AppKit import NSWorkspace
            active_app = NSWorkspace.sharedWorkspace().activeApplication()
            if active_app:
                app_name = active_app.get("NSApplicationName", "unknown")
                pid = active_app.get("NSApplicationProcessIdentifier")

                # Use Accessibility API for window title — works for all apps
                if pid:
                    window_title = self._get_title_via_ax(pid)
        except ImportError:
            app_name = self._get_app_via_applescript()

        # Fallback to AppleScript if AX API returned nothing
        if not window_title and app_name not in ("unknown", "loginwindow"):
            window_title = self._get_title_via_applescript(app_name)

        return app_name, window_title

    @staticmethod
    def _get_title_via_ax(pid: int) -> str:
        """
        Get window title using macOS Accessibility API (AXUIElement).
        This works for virtually all apps — Electron, native, Java, etc.
        Requires Accessibility permission for the terminal/parent app.
        """
        try:
            from ApplicationServices import (
                AXUIElementCreateApplication,
                AXUIElementCopyAttributeValue,
            )
            app_ref = AXUIElementCreateApplication(pid)

            # Try AXFocusedWindow first (most reliable)
            err, focused = AXUIElementCopyAttributeValue(
                app_ref, "AXFocusedWindow", None
            )
            if err == 0 and focused:
                err2, title = AXUIElementCopyAttributeValue(
                    focused, "AXTitle", None
                )
                if err2 == 0 and title:
                    return str(title)

            # Fallback: first item in AXWindows list
            err3, windows = AXUIElementCopyAttributeValue(
                app_ref, "AXWindows", None
            )
            if err3 == 0 and windows and len(windows) > 0:
                err4, title = AXUIElementCopyAttributeValue(
                    windows[0], "AXTitle", None
                )
                if err4 == 0 and title:
                    return str(title)
        except Exception:
            pass
        return ""

    @staticmethod
    def _get_app_via_applescript() -> str:
        """Fallback: use AppleScript to get frontmost app name."""
        try:
            result = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of first '
                 'application process whose frontmost is true'],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip() or "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _get_title_via_applescript(app_name: str) -> str:
        """Last-resort fallback: use AppleScript to get window title."""
        try:
            script = f'tell application "{app_name}" to get name of front window'
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=2,
            )
            title = result.stdout.strip()
            if title:
                return title
        except Exception:
            pass

        try:
            script = (
                f'tell application "System Events"\n'
                f'  tell process "{app_name}"\n'
                f'    get name of front window\n'
                f'  end tell\n'
                f'end tell'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=2,
            )
            return result.stdout.strip() or ""
        except Exception:
            return ""

    # ── Visible windows (multi-monitor / split-screen / PiP) ───
    def get_visible_windows(self) -> list[tuple[str, str]]:
        """
        Enumerate ALL on-screen windows across every monitor, split-view
        pane, and Picture-in-Picture overlay using the Core Graphics API.

        Returns a list of (owner_app_name, window_title) pairs.
        Filters out tiny windows (< 100×100 px) to skip menu-bar items,
        status icons, tooltips, and notification badges.
        """
        try:
            import Quartz

            windows = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID,
            )
            if not windows:
                return []

            result: list[tuple[str, str]] = []
            for win in windows:
                owner = win.get(Quartz.kCGWindowOwnerName, "") or ""
                title = win.get(Quartz.kCGWindowName, "") or ""

                # Skip windows without an identifiable owner
                if not owner:
                    continue

                # Skip tiny windows (menu-bar extras, status items, badges)
                bounds = win.get(Quartz.kCGWindowBounds, {})
                width = bounds.get("Width", 0)
                height = bounds.get("Height", 0)
                if width < 100 or height < 100:
                    continue

                result.append((owner, title))

            return result

        except (ImportError, Exception) as exc:
            logger.debug("CGWindowListCopyWindowInfo unavailable: %s", exc)
            return []

    # ── Input listener ──────────────────────────────────────────
    def start_input_listener(self) -> None:
        try:
            from pynput import keyboard, mouse
        except ImportError:
            logger.error("pynput is required. Install with: pip install pynput")
            return

        def on_key_press(key):
            # Only count — NEVER log the actual key
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
        logger.info("macOS input listeners started.")

    def stop_input_listener(self) -> None:
        if self._kb_listener:
            self._kb_listener.stop()
        if self._mouse_listener:
            self._mouse_listener.stop()
        logger.info("macOS input listeners stopped.")

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
        Use Quartz Event Services (CGEventSourceSecondsSinceLastEventType)
        to get the system-wide idle duration.
        """
        try:
            import Quartz
            idle = Quartz.CGEventSourceSecondsSinceLastEventType(
                Quartz.kCGEventSourceStateCombinedSessionState,
                Quartz.kCGAnyInputEventType,
            )
            return float(idle)
        except ImportError:
            logger.warning("Quartz not available — idle detection disabled.")
            return 0.0
