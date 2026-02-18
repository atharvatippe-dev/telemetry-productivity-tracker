"""
Launcher - entry point for the bundled Telemetry Tracker .exe (Windows).

Flow:
  1. Check if %USERPROFILE%\\.telemetry-tracker\\config.env exists
  2. If not → show setup GUI (first launch)
  3. Load config.env into environment
  4. Install Task Scheduler auto-start entry (idempotent)
  5. Start the tracker agent
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tracker.launcher")

# When bundled with PyInstaller, files are in a temp dir.
# _MEIPASS is set by PyInstaller; fall back to script dir for dev.
if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(sys._MEIPASS)
else:
    BUNDLE_DIR = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(BUNDLE_DIR))

CONFIG_DIR = Path.home() / ".telemetry-tracker"
CONFIG_FILE = CONFIG_DIR / "config.env"


def _load_config_env() -> None:
    """Read config.env and inject into os.environ."""
    for line in CONFIG_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _install_autostart() -> None:
    """Install a Windows Task Scheduler entry so the tracker starts on logon."""
    try:
        from installer.windows.autostart import install_autostart
        install_autostart()
    except Exception as exc:
        logger.warning("Could not install auto-start: %s", exc)


def main() -> None:
    # Step 1: First-launch setup if needed
    if not CONFIG_FILE.exists() or CONFIG_FILE.stat().st_size == 0:
        logger.info("No config found - launching setup GUI.")
        from installer.windows.setup_gui import show_setup

        setup_done = False

        def on_complete():
            nonlocal setup_done
            setup_done = True

        show_setup(on_complete=on_complete)

        if not setup_done:
            logger.info("Setup cancelled by user.")
            sys.exit(0)

    # Step 2: Load config
    _load_config_env()
    logger.info("Config loaded. User ID: %s", os.environ.get("USER_ID", "unknown"))

    # Step 3: Install auto-start (idempotent)
    _install_autostart()

    # Step 4: Start the tracker
    from tracker.agent import main as tracker_main
    tracker_main()


if __name__ == "__main__":
    main()
