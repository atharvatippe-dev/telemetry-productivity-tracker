"""
Windows auto-start installer using Task Scheduler.

Creates a scheduled task that runs the tracker .exe at user logon.
Uses schtasks.exe - no extra dependencies required.
Idempotent - safe to call multiple times.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("tracker.autostart")

TASK_NAME = "TelemetryTracker"
LOG_DIR = Path.home() / ".telemetry-tracker" / "logs"


def _get_executable() -> str:
    """Return the path to the bundled .exe or the Python script."""
    if getattr(sys, "frozen", False):
        return os.path.realpath(sys.executable)
    return sys.executable


def _get_command() -> str:
    """Return the full command string for the scheduled task."""
    exe = _get_executable()
    if getattr(sys, "frozen", False):
        return f'"{exe}"'
    launcher_path = Path(__file__).resolve().parent / "launcher.py"
    return f'"{exe}" "{launcher_path}"'


def install_autostart() -> None:
    """Create a Task Scheduler entry to run the tracker at user logon."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Remove existing task first (ignore errors if it doesn't exist)
    subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
    )

    command = _get_command()

    result = subprocess.run(
        [
            "schtasks", "/Create",
            "/TN", TASK_NAME,
            "/TR", command,
            "/SC", "ONLOGON",
            "/RL", "LIMITED",
            "/F",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        logger.info("Task Scheduler entry created - tracker will auto-start on logon.")
    else:
        logger.warning(
            "schtasks /Create returned %d: %s", result.returncode, result.stderr
        )
        # Fallback: Startup folder shortcut
        _install_startup_shortcut()


def _install_startup_shortcut() -> None:
    """
    Fallback: place a .bat launcher in the user's Startup folder.
    Works even without admin privileges (per-user Startup folder).
    """
    startup_dir = Path(os.environ.get(
        "APPDATA", Path.home() / "AppData" / "Roaming"
    )) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

    if not startup_dir.exists():
        logger.warning("Startup folder not found: %s", startup_dir)
        return

    bat_path = startup_dir / "TelemetryTracker.bat"
    command = _get_command()

    bat_path.write_text(
        f"@echo off\r\nstart /B \"\" {command}\r\n",
        encoding="utf-8",
    )
    logger.info("Startup shortcut written to %s", bat_path)


def uninstall_autostart() -> None:
    """Remove the Task Scheduler entry and Startup shortcut."""
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        logger.info("Task Scheduler entry removed.")
    else:
        logger.info("No Task Scheduler entry found to remove.")

    # Also remove Startup shortcut if present
    startup_dir = Path(os.environ.get(
        "APPDATA", Path.home() / "AppData" / "Roaming"
    )) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    bat_path = startup_dir / "TelemetryTracker.bat"
    if bat_path.exists():
        bat_path.unlink()
        logger.info("Startup shortcut removed.")


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "uninstall":
        uninstall_autostart()
    else:
        install_autostart()
