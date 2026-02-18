"""
macOS LaunchAgent installer.

Creates ~/Library/LaunchAgents/com.telemetry.tracker.plist so the tracker
auto-starts when the user logs in. Idempotent — safe to call multiple times.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("tracker.launchagent")

PLIST_NAME = "com.telemetry.tracker"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = LAUNCH_AGENTS_DIR / f"{PLIST_NAME}.plist"
LOG_DIR = Path.home() / ".telemetry-tracker" / "logs"


def _get_executable() -> str:
    """Return the path to the bundled app or the Python script."""
    if getattr(sys, "frozen", False):
        return os.path.realpath(sys.executable)
    return sys.executable


def _get_args() -> list[str]:
    """Return the command-line arguments for the LaunchAgent."""
    exe = _get_executable()
    if getattr(sys, "frozen", False):
        return [exe]
    launcher_path = Path(__file__).resolve().parent / "launcher.py"
    return [exe, str(launcher_path)]


def _build_plist(args: list[str]) -> str:
    program_args = "\n".join(f"        <string>{a}</string>" for a in args)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
{program_args}
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>{LOG_DIR / "tracker.log"}</string>

    <key>StandardErrorPath</key>
    <string>{LOG_DIR / "tracker.err.log"}</string>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
"""


def install_launchagent() -> None:
    """Write the plist and load it. Idempotent."""
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    args = _get_args()
    plist_content = _build_plist(args)

    # Unload existing if present (ignore errors)
    if PLIST_PATH.exists():
        subprocess.run(
            ["launchctl", "unload", str(PLIST_PATH)],
            capture_output=True,
        )

    PLIST_PATH.write_text(plist_content)
    logger.info("LaunchAgent plist written to %s", PLIST_PATH)

    result = subprocess.run(
        ["launchctl", "load", str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        logger.info("LaunchAgent loaded — tracker will auto-start on login.")
    else:
        logger.warning("launchctl load returned %d: %s", result.returncode, result.stderr)


def uninstall_launchagent() -> None:
    """Unload and remove the plist."""
    if PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
        PLIST_PATH.unlink()
        logger.info("LaunchAgent uninstalled.")
    else:
        logger.info("No LaunchAgent found to uninstall.")


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "uninstall":
        uninstall_launchagent()
    else:
        install_launchagent()
