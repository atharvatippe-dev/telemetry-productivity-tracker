"""
First-launch setup GUI for Zinnia Axion (Windows).

Displays a small Tkinter window asking for the employee's User ID.
The backend URL is pre-configured by the admin at build time.
Writes config to %USERPROFILE%\\.telemetry-tracker\\config.env
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

CONFIG_DIR = Path.home() / ".telemetry-tracker"
CONFIG_FILE = CONFIG_DIR / "config.env"

try:
    from installer.windows.build_config import BACKEND_URL as DEFAULT_BACKEND_URL
except ImportError:
    DEFAULT_BACKEND_URL = os.environ.get(
        "INSTALLER_BACKEND_URL", "https://your-backend-url.ngrok-free.dev"
    )


def config_exists() -> bool:
    return CONFIG_FILE.exists() and CONFIG_FILE.stat().st_size > 0


def read_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if not CONFIG_FILE.exists():
        return cfg
    for line in CONFIG_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def write_config(user_id: str, backend_url: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Zinnia Axion Configuration (Windows)",
        f"USER_ID={user_id}",
        f"BACKEND_URL={backend_url}",
        "",
        "# Advanced settings (defaults are fine for most users)",
        "POLL_INTERVAL_SEC=1",
        "BATCH_INTERVAL_SEC=10",
        f"BUFFER_FILE={CONFIG_DIR / 'buffer.json'}",
        "WINDOW_TITLE_MODE=full",
        "WAKE_THRESHOLD_SEC=30",
        "GHOST_APPS=explorer,ShellExperienceHost,SearchHost,LockApp,"
        "TextInputHost,StartMenuExperienceHost,RuntimeBroker,"
        "SystemSettings,ApplicationFrameHost,LogonUI,csrss,dwm",
        "NON_PRODUCTIVE_APPS=youtube,netflix,reddit,twitter,x.com,"
        "instagram,facebook,tiktok,twitch,discord,spotify,steam,epic games",
        "MEETING_APPS=zoom,microsoft teams,google meet,webex,facetime,"
        "slack huddle,discord call,skype,around,tuple,gather",
        "BROWSER_APPS=google chrome,firefox,microsoft edge,msedge,"
        "brave browser,chromium,opera",
    ]
    CONFIG_FILE.write_text("\n".join(lines) + "\n")


def show_setup(on_complete: callable = None) -> None:
    """Show the setup window. Calls on_complete() after config is saved."""

    root = tk.Tk()
    root.title("Zinnia Axion - Setup")
    root.geometry("440x220")
    root.resizable(False, False)

    # Center on screen
    root.update_idletasks()
    x = (root.winfo_screenwidth() - 440) // 2
    y = (root.winfo_screenheight() - 220) // 2
    root.geometry(f"440x220+{x}+{y}")

    tk.Label(
        root, text="Zinnia Axion Setup", font=("Segoe UI", 14, "bold")
    ).pack(pady=(20, 5))

    tk.Label(
        root,
        text="Enter your name or employee ID to get started.",
        font=("Segoe UI", 10),
    ).pack(pady=(0, 12))

    frame = tk.Frame(root)
    frame.pack(pady=5)
    tk.Label(frame, text="User ID:", width=10, anchor="e", font=("Segoe UI", 10)).grid(
        row=0, column=0, padx=5
    )
    user_entry = tk.Entry(frame, width=30, font=("Segoe UI", 10))
    user_entry.grid(row=0, column=1, padx=5)
    user_entry.focus_set()

    def on_save():
        uid = user_entry.get().strip()
        if not uid:
            messagebox.showwarning("Missing User ID", "Please enter your User ID.")
            return
        write_config(uid, DEFAULT_BACKEND_URL)
        messagebox.showinfo(
            "Setup Complete",
            f"Configuration saved.\nUser ID: {uid}\n\nThe tracker will now start.",
        )
        root.destroy()
        if on_complete:
            on_complete()

    tk.Button(
        root, text="Save & Start", command=on_save, width=16, font=("Segoe UI", 10)
    ).pack(pady=18)

    root.bind("<Return>", lambda e: on_save())
    root.mainloop()


if __name__ == "__main__":
    show_setup(on_complete=lambda: print("Config saved."))
