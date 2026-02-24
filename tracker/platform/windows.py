"""
Windows platform collector.

Uses Win32 APIs exclusively so input detection works in VDI/RDP sessions
where pynput hooks are silently blocked by the remote desktop protocol.

APIs used:
  - win32gui / win32process / psutil  for active window detection
  - win32gui.EnumWindows              for visible window enumeration
  - GetLastInputInfo (user32)         for OS-level idle time
  - GetCursorPos (user32)             for mouse movement tracking
  - GetAsyncKeyState (user32)         for polling keyboard state (native Windows)

VDI fallback:
  In Citrix/RDP environments, GetAsyncKeyState often returns zero because
  input is injected via the remote protocol layer.  When this is detected,
  the collector falls back to idle-time-delta estimation: if GetLastInputInfo
  idle drops to near-zero between polls, the user pressed a key or clicked.
  This provides reliable interaction counting in all VDI environments.
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

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04

_VK_POLL_START = 0x08
_VK_POLL_END = 0xFE
_KEY_PRESSED_BIT = 0x8000

# Idle threshold (seconds): if idle drops below this between polls,
# count it as an input event in the VDI fallback estimator.
_IDLE_INPUT_THRESHOLD = 2.0

# Number of initial polls to evaluate whether GetAsyncKeyState works.
# If zero keypresses after this many polls with low idle, switch to fallback.
_CALIBRATION_POLLS = 150  # ~5 seconds at 30 Hz


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


def _get_idle_ms() -> int:
    """Return milliseconds since last user input (OS-level)."""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if _user32.GetLastInputInfo(ctypes.byref(lii)):
        return _kernel32.GetTickCount() - lii.dwTime
    return 0


class WindowsCollector(PlatformCollector):
    """
    Windows collector with automatic VDI fallback.

    Primary mode (native Windows):
      - GetAsyncKeyState polled at ~30 Hz for key-press transitions
      - GetAsyncKeyState on VK_LBUTTON/RBUTTON/MBUTTON for clicks
      - GetCursorPos for mouse movement

    VDI fallback mode (auto-detected):
      - GetLastInputInfo idle-time-delta for interaction estimation
      - GetCursorPos for mouse movement (works in VDI)
      - Activated when GetAsyncKeyState returns nothing after calibration

    Both modes use GetLastInputInfo for idle time reporting.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keystroke_count = 0
        self._mouse_clicks = 0
        self._mouse_distance = 0.0
        self._last_mouse_pos: tuple[int, int] | None = None

        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # GetAsyncKeyState tracking
        self._prev_key_states: dict[int, bool] = {}
        self._prev_lbutton = False
        self._prev_rbutton = False
        self._prev_mbutton = False

        # VDI fallback: idle-delta estimator
        self._vdi_mode = False
        self._calibration_count = 0
        self._calibration_async_hits = 0
        self._calibration_low_idle_seen = False
        self._prev_idle_ms = 0

    # ── Active window ────────────────────────────────────────────

    def get_active_window(self) -> tuple[str, str]:
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

            if app_name.lower() == "applicationframehost":
                app_name = self._resolve_uwp_app(hwnd, win32gui, win32process, psutil) or app_name

            return app_name, window_title
        except ImportError:
            return self._get_active_window_ctypes()

    @staticmethod
    def _resolve_uwp_app(hwnd, win32gui, win32process, psutil) -> str | None:
        """Find the real UWP app behind ApplicationFrameHost."""
        host_pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        real_app = None

        def _child_callback(child_hwnd, _):
            nonlocal real_app
            try:
                _, child_pid = win32process.GetWindowThreadProcessId(child_hwnd)
                if child_pid != host_pid and child_pid > 0:
                    proc = psutil.Process(child_pid)
                    name = proc.name().replace(".exe", "")
                    if name.lower() != "applicationframehost":
                        real_app = name
            except Exception:
                pass

        try:
            win32gui.EnumChildWindows(hwnd, _child_callback, None)
        except Exception:
            pass

        return real_app

    @staticmethod
    def _get_active_window_ctypes() -> tuple[str, str]:
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

            if app_name.lower() == "applicationframehost":
                app_name = WindowsCollector._resolve_uwp_app(hwnd, win32gui, win32process, psutil) or app_name

            result.append((app_name, title))

        try:
            win32gui.EnumWindows(_enum_callback, None)
        except Exception as exc:
            logger.debug("EnumWindows failed: %s", exc)

        return result

    # ── Input polling ────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """
        Background thread polling at ~30 Hz.

        During the first _CALIBRATION_POLLS cycles, runs both
        GetAsyncKeyState and idle-delta detection. If GetAsyncKeyState
        produces zero hits while the user is clearly active (low idle),
        switches permanently to VDI fallback mode.
        """
        poll_interval = 1.0 / 30

        while not self._stop_event.is_set():
            try:
                if not self._vdi_mode:
                    self._poll_async_keystate()
                    self._poll_async_mouse_buttons()

                self._poll_mouse_position()
                self._poll_idle_delta()

                if not self._vdi_mode:
                    self._calibrate()

            except Exception as exc:
                logger.debug("Poll error: %s", exc)

            self._stop_event.wait(poll_interval)

    def _calibrate(self) -> None:
        """
        After enough polls, check if GetAsyncKeyState detected anything.
        If not but the user was active (idle resets seen), switch to VDI mode.
        """
        self._calibration_count += 1
        if self._calibration_count < _CALIBRATION_POLLS:
            return

        if self._calibration_async_hits == 0 and self._calibration_low_idle_seen:
            self._vdi_mode = True
            logger.info(
                "VDI mode activated: GetAsyncKeyState returned 0 hits "
                "while user was active. Switching to idle-delta estimation."
            )
        elif self._calibration_async_hits > 0:
            logger.info(
                "Native input detection confirmed (%d hits during calibration).",
                self._calibration_async_hits,
            )

    def _poll_async_keystate(self) -> None:
        """Detect key presses via GetAsyncKeyState (works on native Windows)."""
        new_presses = 0
        for vk in range(_VK_POLL_START, _VK_POLL_END + 1):
            if vk in (VK_LBUTTON, VK_RBUTTON, VK_MBUTTON):
                continue

            state = _user32.GetAsyncKeyState(vk)
            is_pressed = bool(state & _KEY_PRESSED_BIT)
            was_pressed = self._prev_key_states.get(vk, False)

            if is_pressed and not was_pressed:
                new_presses += 1

            self._prev_key_states[vk] = is_pressed

        if new_presses > 0:
            self._calibration_async_hits += new_presses
            with self._lock:
                self._keystroke_count += new_presses

    def _poll_async_mouse_buttons(self) -> None:
        """Detect mouse clicks via GetAsyncKeyState on button VKs."""
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
            self._calibration_async_hits += new_clicks
            with self._lock:
                self._mouse_clicks += new_clicks

    def _poll_mouse_position(self) -> None:
        """Track mouse movement via GetCursorPos (works in VDI)."""
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

    def _poll_idle_delta(self) -> None:
        """
        VDI fallback: estimate input events from idle time changes.

        GetLastInputInfo is updated by the OS whenever any input arrives,
        even in Citrix/RDP sessions. If idle drops from a high value to
        near-zero, the user just pressed a key or clicked.

        Each idle reset is counted as one interaction event.
        This is conservative (undercounts) but reliable.
        """
        idle_ms = _get_idle_ms()

        # Track that we've seen the user be active (for calibration)
        if idle_ms < _IDLE_INPUT_THRESHOLD * 1000:
            self._calibration_low_idle_seen = True

        if self._vdi_mode:
            # If idle dropped significantly, user provided input
            if self._prev_idle_ms > _IDLE_INPUT_THRESHOLD * 1000 and idle_ms < _IDLE_INPUT_THRESHOLD * 1000:
                with self._lock:
                    self._keystroke_count += 1

            # If idle is very low and cursor moved, count as a click
            # (can't distinguish keys from clicks, so split heuristically)
            if idle_ms < 200:  # <200ms idle = just interacted
                pt = POINT()
                if _user32.GetCursorPos(ctypes.byref(pt)):
                    if self._last_mouse_pos is not None:
                        dx = pt.x - self._last_mouse_pos[0]
                        dy = pt.y - self._last_mouse_pos[1]
                        if (dx * dx + dy * dy) ** 0.5 < 5:
                            # Cursor didn't move but idle is near-zero:
                            # likely a keystroke or click in place
                            with self._lock:
                                self._keystroke_count += 1

        self._prev_idle_ms = idle_ms

    # ── PlatformCollector interface ──────────────────────────────

    def start_input_listener(self) -> None:
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name="win32-input-poll",
            daemon=True,
        )
        self._poll_thread.start()
        logger.info("Win32 input polling started (VDI-compatible).")

    def stop_input_listener(self) -> None:
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        if self._vdi_mode:
            logger.info("Win32 input polling stopped (was in VDI fallback mode).")
        else:
            logger.info("Win32 input polling stopped (native mode).")

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

    # ── Idle time ────────────────────────────────────────────────

    def get_idle_seconds(self) -> float:
        try:
            return _get_idle_ms() / 1000.0
        except Exception as exc:
            logger.warning("Idle detection failed: %s", exc)
        return 0.0
