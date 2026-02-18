"""
Build script — creates a macOS .app bundle using PyInstaller.

Usage (from project root):
    python installer/mac/build.py

Before building, set the backend URL that will be baked into the installer:
    export INSTALLER_BACKEND_URL=https://your-backend.ngrok-free.dev
    python installer/mac/build.py

Output:
    dist/TelemetryTracker.app
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LAUNCHER = PROJECT_ROOT / "installer" / "mac" / "launcher.py"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
APP_NAME = "TelemetryTracker"


def main() -> None:
    backend_url = os.environ.get("INSTALLER_BACKEND_URL", "")
    if not backend_url:
        print("WARNING: INSTALLER_BACKEND_URL not set.")
        print("The installer will use the placeholder URL.")
        print("Set it with: export INSTALLER_BACKEND_URL=https://your-url.ngrok-free.dev")
        print()
    else:
        # Bake the backend URL into build_config.py so it's available at runtime
        config_file = PROJECT_ROOT / "installer" / "mac" / "build_config.py"
        config_file.write_text(
            '"""\nBuild-time configuration — values baked in by the build script.\n'
            'Do NOT edit manually; this file is overwritten by build.py.\n"""\n\n'
            f'BACKEND_URL = "{backend_url}"\n'
        )
        print(f"Baked backend URL: {backend_url}")

    # Collect data files: tracker platform modules + installer modules
    datas = [
        (str(PROJECT_ROOT / "tracker"), "tracker"),
        (str(PROJECT_ROOT / "installer"), "installer"),
    ]
    datas_args = []
    for src, dest in datas:
        datas_args.extend(["--add-data", f"{src}:{dest}"])

    # Hidden imports needed by the tracker
    hidden = [
        "tracker.platform",
        "tracker.platform.factory",
        "tracker.platform.base",
        "tracker.platform.macos",
        "installer.mac.setup_gui",
        "installer.mac.launchagent",
        "pynput",
        "pynput.keyboard",
        "pynput.mouse",
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
    ]
    hidden_args = []
    for h in hidden:
        hidden_args.extend(["--hidden-import", h])

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onedir",
        "--windowed",
        "--noconfirm",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        *datas_args,
        *hidden_args,
        str(LAUNCHER),
    ]

    print("Building with command:")
    print(" ".join(cmd))
    print()

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode == 0:
        app_path = DIST_DIR / f"{APP_NAME}.app"
        print(f"\nBuild successful: {app_path}")
        print(f"\nTo test: open {app_path}")
        print(f"To distribute: create a DMG with 'hdiutil'")
    else:
        print(f"\nBuild failed with exit code {result.returncode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
