"""
Build script - creates a Windows .exe using PyInstaller.

Usage (from project root):
    set INSTALLER_BACKEND_URL=https://your-backend.ngrok-free.dev
    python installer/windows/build.py

Output:
    dist/ZinniaAxion.exe
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LAUNCHER = PROJECT_ROOT / "installer" / "windows" / "launcher.py"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
APP_NAME = "ZinniaAxion"


def main() -> None:
    backend_url = os.environ.get("INSTALLER_BACKEND_URL", "")
    if not backend_url:
        print("WARNING: INSTALLER_BACKEND_URL not set.")
        print("The installer will use the placeholder URL.")
        print("Set it with: set INSTALLER_BACKEND_URL=https://your-url.ngrok-free.dev")
        print()
    else:
        config_file = PROJECT_ROOT / "installer" / "windows" / "build_config.py"
        config_file.write_text(
            '"""\nBuild-time configuration - values baked in by the build script.\n'
            'Do NOT edit manually; this file is overwritten by build.py.\n"""\n\n'
            f'BACKEND_URL = "{backend_url}"\n',
            encoding="utf-8",
        )
        print(f"Baked backend URL: {backend_url}")

    # Data files: tracker modules + installer modules
    datas = [
        (str(PROJECT_ROOT / "tracker"), "tracker"),
        (str(PROJECT_ROOT / "installer"), "installer"),
    ]
    datas_args = []
    for src, dest in datas:
        datas_args.extend(["--add-data", f"{src};{dest}"])

    hidden = [
        "tracker.platform",
        "tracker.platform.factory",
        "tracker.platform.base",
        "tracker.platform.windows",
        "installer.windows.setup_gui",
        "installer.windows.autostart",
        "installer.windows.build_config",
        "psutil",
        "win32gui",
        "win32process",
        "win32api",
        "win32con",
        "ctypes",
        "ctypes.wintypes",
    ]
    hidden_args = []
    for h in hidden:
        hidden_args.extend(["--hidden-import", h])

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onefile",
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
        exe_path = DIST_DIR / f"{APP_NAME}.exe"
        print(f"\nBuild successful: {exe_path}")
        print(f"Size: {exe_path.stat().st_size / (1024*1024):.1f} MB")
    else:
        print(f"\nBuild failed with exit code {result.returncode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
