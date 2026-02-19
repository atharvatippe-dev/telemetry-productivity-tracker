"""
Windows platform collector.

Uses Win32 APIs exclusively so input detection works in VDI/RDP sessions
where pynput hooks are silently blocked by the remote desktop protocol.

APIs used:
  - win32gui / win32process / psutil  for active window detection
  - win32gui.EnumWindows              for visible window enumeration
  - GetLastInputInfo (user32)         for OS-level idle time
  - GetCursorPos (user32)             for mouse movement tracking
  - GetAsyncKeyState (user32)         for polling keyboard state
  - GetKeyState (user32)              for mouse button state
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import threading
import time

from tracker.platform.base import PlatformCollector

logger = logging.getLogger("tracker.windows")

# ── Win32 constants ──────────────────────────────────────────────────

# Virtual-key codes for mouse buttons
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04

# Range of VK codes to poll for keyboard activity (covers all useful keys)
# 0x08 = Backspace through 0xFE; skip 0x00-0x07 (mouse/undefined)
_VK_POLL_START = 0x08
_VK_POLL_END = 0xFE

# Bit mask: if bit 15 is set, the key is currently pressed
_KEY_PRESSED_BIT = 0x8000


# ── ctypes structures ────────────────────────────────────────────────

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


# ── Cached Win32 function references ─────────────────────────────────

_user32 = ctypes.windll.user32  # type: ignore[attr-defined]
_kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]


class WindowsCollector(PlatformCollector):
    """
    Windows collector that uses polling-based Win32 APIs instead of
    pynput hooks.  Works reliably in VDI, RDP, and Citrix sessions.

    Input detection strategy:
      - A background thread polls GetAsyncKeyState at ~30 Hz to detect
        key-press transitions and mouse button clicks.
      - GetCursorPos is sampled each poll to compute mouse distance.
      - GetLastInputInfo provides OS-level idle time.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keystroke_count = 0
        self._mouse_clicks = 0
        self._mouse_distance = 0.0
        self._last_mouse_pos: tuple[int, int] | None = None

        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Track previous key states to detect press transitions
        self._prev_key_states: dict[int, bool] = {}
        self._prev_lbutton = False
        self._prev_rbutton = False
        self._prev_mbutton = False

    # ── Active window ────────────────────────────────────────────

    def get_active_window(self) -> tuple[str, str]:
        """Get the foreground window's app name and title."""
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
        """Fallback using only ctypes if pywin32/psutil unavailable."""
        try:
            hwnd = _user32.GetForegroundWindow()
            length = _user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buf, length + 1)
            return "unknown", buf.value or ""
        except Exception:
            return "unknown", ""

    # ── Visible windows (multi-monitor / split-screen / PiP) ─────

    def get_visible_windows(self) -> list[tuple[str, str]]:
        """
        Enumerate ALL visible on-screen windows across every monitor.
        Filters out invisible, minimized, and tiny (<100x100) windows.
        """
        try:
            import win32gui
            import win32process
            import psutil
        except ImportError:
            return []

        result: list[tuple[str, str]] = []

        def _enum_callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            if win32gui.IsIconic(hwnd):
                return

            title = win32gui.GetWindowText(hwnd) or ""
            if not title:
                return

            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                if (right - left) < 100 or (bottom - top) < 100:
                    return
            except Exception:
                return

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

    # ── Input polling (replaces pynput) ──────────────────────────

    def _poll_loop(self) -> None:
        """
        Background thread: polls Win32 APIs at ~30 Hz to detect input.

        Uses GetAsyncKeyState to detect key-press transitions (bit 15
        going from 0 to 1) and GetCursorPos for mouse movement.
        This works in VDI/RDP because these APIs query the OS input
        state directly rather than relying on low-level hooks.
        """
        poll_interval = 1.0 / 30  # ~30 Hz

        while not self._stop_event.is_set():
            try:
                self._poll_keyboard()
                self._poll_mouse_buttons()
                self._poll_mouse_position()
            except Exception as exc:
                logger.debug("Poll error: %s", exc)

            self._stop_event.wait(poll_interval)

    def _poll_keyboard(self) -> None:
        """Detect new key presses by polling GetAsyncKeyState."""
        new_presses = 0
        for vk in range(_VK_POLL_START, _VK_POLL_END + 1):
            # Skip mouse button VK codes (handled separately)
            if vk in (VK_LBUTTON, VK_RBUTTON, VK_MBUTTON):
                continue

            state = _user32.GetAsyncKeyState(vk)
            is_pressed = bool(state & _KEY_PRESSED_BIT)
            was_pressed = self._prev_key_states.get(vk, False)

            if is_pressed and not was_pressed:
                new_presses += 1

            self._prev_key_states[vk] = is_pressed

        if new_presses > 0:
            with self._lock:
                self._keystroke_count += new_presses

    def _poll_mouse_buttons(self) -> None:
        """Detect mouse clicks by polling GetAsyncKeyState on button VKs."""
        new_clicks = 0

        for vk, attr in [
            (VK_LBUTTON, "_prev_lbutton"),
            (VK_RBUTTON, "_prev_rbutton"),
            (VK_MBUTTON, "_prev_mbutton"),
        ]:
            state = _user32.GetAsyncKeyState(vk)
            is_pressed = bool(state & _KEY_PRESSED_BIT)
            was_pressed = getattr(self, attr)

            if is_pressed and not was_pressed:
                new_clicks += 1

            setattr(self, attr, is_pressed)

        if new_clicks > 0:
            with self._lock:
                self._mouse_clicks += new_clicks

    def _poll_mouse_position(self) -> None:
        """Track mouse movement via GetCursorPos."""
        pt = POINT()
        if _user32.GetCursorPos(ctypes.byref(pt)):
            x, y = pt.x, pt.y
            with self._lock:
                if self._last_mouse_pos is not None:
                    dx = x - self._last_mouse_pos[0]
                    dy = y - self._last_mouse_pos[1]
                    dist = (dx * dx + dy * dy) ** 0.5
                    if dist > 0:
                        self._mouse_distance += dist
                self._last_mouse_pos = (x, y)

    # ── PlatformCollector interface ──────────────────────────────

    def start_input_listener(self) -> None:
        """Start the background polling thread."""
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="win32-input-poll",
            daemon=True,
        )
        self._poll_thread.start()
        logger.info("Win32 input polling started (VDI-compatible).")

    def stop_input_listener(self) -> None:
        """Signal the polling thread to stop and wait for it."""
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        logger.info("Win32 input polling stopped.")

    def get_and_reset_counts(self) -> dict:
        """Return accumulated counts since last call and reset."""
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

    # ── Idle time ────────────────────────────────────────────────

    def get_idle_seconds(self) -> float:
        """
        OS-level idle time via GetLastInputInfo.
        Works in VDI/RDP because it tracks the session's input state.
        """
        try:
            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if _user32.GetLastInputInfo(ctypes.byref(lii)):
                millis = _kernel32.GetTickCount() - lii.dwTime
                return millis / 1000.0
        except Exception as exc:
            logger.warning("Idle detection failed: %s", exc)
        return 0.0
