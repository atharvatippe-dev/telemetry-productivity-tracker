# Uninstalling Zinnia Axion

## Windows

### Step 1 - Kill the running process
Open **Task Manager** (Ctrl+Shift+Esc), find `Zinnia_axion`, click **End Task**.

### Step 2 - Remove auto-start
Open **Command Prompt** (search "cmd") and run:
```
schtasks /Delete /TN Zinnia_axion /F
```
Also delete the Startup shortcut if it exists:
```
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Zinnia_axion.bat"
```

### Step 3 - Delete config and logs
```
rmdir /S /Q "%USERPROFILE%\.telemetry-tracker"
```

### Step 4 - Delete the installer files
Delete both `Zinnia_axion.exe` and `Zinnia_axion-Windows.zip` from wherever you saved them (Downloads, Desktop, etc.)

---

## macOS

### Step 1 - Stop Zinnia Axion
```bash
launchctl unload ~/Library/LaunchAgents/com.telemetry.tracker.plist
```

### Step 2 - Remove auto-start
```bash
rm ~/Library/LaunchAgents/com.telemetry.tracker.plist
```

### Step 3 - Delete config and logs
```bash
rm -rf ~/.telemetry-tracker
```

### Step 4 - Delete the app
Delete `Zinnia_axion.app` from wherever it was installed (Applications, Desktop, etc.)

---

After completing these steps, Zinnia Axion is fully removed with zero traces left on the system.
