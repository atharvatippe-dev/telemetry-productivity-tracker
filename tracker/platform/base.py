"""
Abstract base class for platform-specific telemetry collectors.

Each platform module (macOS, Windows, Linux) implements this interface.
"""

from abc import ABC, abstractmethod


class PlatformCollector(ABC):
    """
    Contract that every OS-specific collector must fulfill.

    Responsibilities:
      • get_active_window()  — return (app_name, window_title)
      • start_input_listener() / stop_input_listener()
        — manage a background listener that counts keystrokes and mouse events
      • get_and_reset_counts() — return accumulated counts since last call
      • get_idle_seconds()     — seconds since last user interaction
    """

    @abstractmethod
    def get_active_window(self) -> tuple[str, str]:
        """Return (application_name, window_title) of the foreground window."""
        ...

    @abstractmethod
    def start_input_listener(self) -> None:
        """Begin counting keystrokes and mouse events in the background."""
        ...

    @abstractmethod
    def stop_input_listener(self) -> None:
        """Tear down the background listener."""
        ...

    @abstractmethod
    def get_and_reset_counts(self) -> dict:
        """
        Return accumulated input counts since the last call and reset.

        Expected keys:
            keystroke_count  : int
            mouse_clicks     : int
            mouse_distance   : float  (pixels, approximate)
        """
        ...

    @abstractmethod
    def get_idle_seconds(self) -> float:
        """Return seconds since the last keyboard/mouse interaction."""
        ...

    def get_visible_windows(self) -> list[tuple[str, str]]:
        """
        Return a list of (app_name, window_title) for ALL visible on-screen
        windows — across all monitors, split-view panes, and PiP overlays.

        Used for multi-monitor / split-screen / PiP distraction detection.

        Default implementation returns an empty list (platform doesn't support
        window enumeration).  macOS overrides this using CGWindowListCopyWindowInfo.
        """
        return []
