# Zinnia Axion

A privacy-conscious, telemetry-driven productivity tracker that silently records how employees spend their computer time and surfaces the data through real-time dashboards. Deploys as a standalone executable on **macOS** and **Windows** — no Python installation needed on end-user machines.

**Key privacy guarantee:** Only interaction *counts* are recorded — keystroke content is **never** captured.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Productivity Model](#productivity-model)
3. [Features](#features)
4. [Project Structure](#project-structure)
5. [Quick Start (Developer)](#quick-start-developer)
6. [Deploying to Employees](#deploying-to-employees)
7. [Dashboards](#dashboards)
8. [API Reference](#api-reference)
9. [Configuration](#configuration)
10. [Security & Anti-Cheat](#security--anti-cheat)
11. [Privacy](#privacy)
12. [Uninstallation](#uninstallation)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     Employee Laptop (macOS / Windows)            │
│                                                                  │
│  ┌────────────────────────────────────┐                          │
│  │  Tracker Agent (tracker/agent.py)  │                          │
│  │                                    │                          │
│  │  • Polls every 1s: active window,  │                          │
│  │    keystroke count, mouse clicks,  │                          │
│  │    mouse distance, idle time       │                          │
│  │  • Multi-monitor distraction scan  │                          │
│  │  • Batches every 10s → POST /track │                          │
│  │  • Offline buffer (JSON) on fail   │                          │
│  └──────────────┬─────────────────────┘                          │
│                 │  HTTPS (ngrok or direct)                        │
└─────────────────┼────────────────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                     Central Server (Admin Machine)               │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  Flask Backend (backend/app.py) — port 5000              │    │
│  │                                                          │    │
│  │  • REST API: /track, /summary, /apps, /daily, /admin/*  │    │
│  │  • Productivity engine (bucketize → classify)            │    │
│  │  • PostgreSQL / SQLite storage                           │    │
│  │  • Auto-cleanup (DATA_RETENTION_DAYS)                    │    │
│  │  • Self-contained HTML dashboard per user                │    │
│  │  • Tracker online/offline status                         │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐  │
│  │ User Dashboard       │  │ Admin Dashboard                  │  │
│  │ (Streamlit :8501)    │  │ (Streamlit :8502)                │  │
│  │                      │  │                                  │  │
│  │ • Today's metrics    │  │ • Team leaderboard               │  │
│  │ • State distribution │  │ • Tracker online/offline status  │  │
│  │ • Daily trend        │  │ • Per-user drill-down            │  │
│  │ • App breakdown      │  │ • AI-powered summary (OpenAI)    │  │
│  │ • Auto-refresh       │  │ • Delete user data               │  │
│  └──────────────────────┘  │ • Executive summary page         │  │
│                            └──────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  ngrok tunnel (optional) — exposes :5000 to the internet │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

---

## Productivity Model

Time is classified into **two states**: `productive` and `non_productive`.

Each batch of raw events is grouped into **10-second buckets**. Each bucket is classified using this decision tree (first match wins):

| Priority | Condition | Result |
|----------|-----------|--------|
| 1 | App is in `NON_PRODUCTIVE_APPS` (YouTube, Netflix, Reddit, etc.) | `non_productive` |
| 2 | App is in `MEETING_APPS` (Zoom, Teams, Meet, etc.) | `productive` |
| 3 | Interaction meets threshold AND passes anti-cheat check | `productive` |
| 4 | Active presence detected (mouse movement, not idle, not distracted) | `productive` |
| 5 | None of the above | `non_productive` |

**Idle / away time is not counted at all** — only active computer usage contributes to either category.

### Interaction Thresholds (10-second buckets)

| Metric | Threshold | Meaning |
|--------|-----------|---------|
| Combined (keystrokes + clicks) | ≥ 2 | Any mix of typing and clicking |
| Keystrokes alone | ≥ 1 | Typing activity |
| Mouse clicks alone | ≥ 1 | Clicking activity |

### Active Presence (Reading Detection)

For users reading without typing (code review, documents, etc.):

- Mouse movement ≥ 8 pixels total in the bucket
- OS idle time < 30 seconds
- Movement in ≥ 3 of 10 samples (anti-wiggle)
- No visible distraction on other monitors

---

## Features

### Tracker Agent
- **1-second polling** with 10-second batch uploads
- **Offline buffering** — events saved to local JSON when backend is unreachable; auto-flushed on reconnect
- **Sleep/wake detection** — detects lid close, hibernation; resets counters on wake
- **Multi-monitor distraction detection** — scans all visible windows (not just active) for non-productive apps in split-view, PiP, or secondary monitors
- **Ghost app filtering** — ignores system processes (loginwindow, ScreenSaver, Dock, etc.) when no interaction
- **Window title privacy modes** — `full`, `redacted` (keywords only), or `off`
- **Cross-platform** — macOS (pyobjc), Windows (pywin32), Linux (xdotool)

### Backend
- **Flask REST API** with CORS support
- **PostgreSQL or SQLite** storage via SQLAlchemy
- **Timezone-aware day boundaries** (configurable via `TIMEZONE`)
- **Auto-cleanup** of events older than `DATA_RETENTION_DAYS` on startup
- **Self-contained HTML dashboard** served at `/dashboard/<user_id>` — shareable via ngrok
- **Tracker online/offline status** — real-time detection based on last event timestamp

### Dashboards
- **User Dashboard** (Streamlit, port 8501) — personal productivity view with metrics, trends, and app breakdown
- **Admin Dashboard** (Streamlit, port 8502) — team leaderboard with color-coded rows, tracker status indicators, per-user drill-down, and delete functionality
- **Summary Page** — AI-generated (OpenAI) or heuristic executive-ready team productivity report with regenerate capability
- **HTML Dashboard** — lightweight, self-contained page served by the backend at `/dashboard/<user_id>` for remote access via ngrok

### AI Summary
- **OpenAI integration** — generates executive-friendly summaries from aggregated, privacy-safe data
- **Heuristic fallback** — deterministic summary when OpenAI is unavailable
- **Caching** — 5-minute TTL to control API costs
- **Privacy-safe** — only aggregated metrics are sent; no window titles or keystroke content

---

## Project Structure

```
zinnia-axion/
├── backend/
│   ├── __init__.py
│   ├── app.py                  # Flask REST API (all endpoints)
│   ├── config.py               # Configuration loader (.env)
│   ├── models.py               # SQLAlchemy models (TelemetryEvent)
│   ├── productivity.py         # Productivity engine (bucketize, classify)
│   └── templates/
│       └── dashboard.html      # Self-contained HTML dashboard
├── tracker/
│   ├── __init__.py
│   ├── agent.py                # Main tracker loop (poll → batch → send)
│   ├── platform.py             # Platform detection (macOS/Windows/Linux)
│   ├── macos.py                # macOS collector (pyobjc)
│   ├── windows.py              # Windows collector (pywin32)
│   └── linux.py                # Linux collector (xdotool)
├── frontend/
│   ├── __init__.py
│   ├── dashboard.py            # User Streamlit dashboard (port 8501)
│   ├── admin_dashboard.py      # Admin Streamlit dashboard (port 8502)
│   ├── ai_summary.py           # AI summary engine (OpenAI + fallback)
│   └── pages/
│       └── executive_summary.py # Executive summary page (Streamlit multipage)
├── installer/
│   ├── mac/
│   │   ├── build.py            # PyInstaller build → ZinniaAxion.app
│   │   ├── launcher.py         # macOS app entry point
│   │   ├── setup_gui.py        # First-run Tkinter setup (User ID)
│   │   └── launchagent.py      # LaunchAgent for auto-start
│   └── windows/
│       ├── build.py            # PyInstaller build → ZinniaAxion.exe
│       ├── launcher.py         # Windows exe entry point
│       ├── setup_gui.py        # First-run Tkinter setup (User ID)
│       └── autostart.py        # Task Scheduler / Startup folder
├── scripts/
│   └── migrate_sqlite_to_pg.py # One-time SQLite → PostgreSQL migration
├── .github/
│   └── workflows/
│       └── build-windows.yml   # GitHub Actions: build Windows installer
├── .env                        # Local configuration (not committed)
├── .env.example                # Configuration template
├── requirements.txt            # Core Python dependencies
├── requirements-macos.txt      # macOS-specific dependencies
├── requirements-windows.txt    # Windows-specific dependencies
├── requirements-linux.txt      # Linux-specific dependencies
├── ZinniaAxion.spec            # PyInstaller spec file
├── UNINSTALL.md                # Uninstallation guide
└── README.md                   # This file
```

---

## Quick Start (Developer)

### Prerequisites

- Python 3.10+
- PostgreSQL (recommended) or SQLite
- ngrok (for remote tracker access)

### 1. Clone and set up

```bash
git clone <repo-url> zinnia-axion
cd zinnia-axion
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt

# Platform-specific:
pip install -r requirements-macos.txt      # macOS
# pip install -r requirements-windows.txt  # Windows
# pip install -r requirements-linux.txt    # Linux

# Optional: for AI summaries
pip install openai
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env — set DATABASE_URI, USER_ID, TIMEZONE, OPENAI_API_KEY, etc.
```

### 4. Start all services

```bash
# Terminal 1: Backend
python -m backend.app

# Terminal 2: ngrok (optional, for remote access)
ngrok http 5000

# Terminal 3: User Dashboard
streamlit run frontend/dashboard.py --server.port 8501 --server.headless true

# Terminal 4: Admin Dashboard
streamlit run frontend/admin_dashboard.py --server.port 8502 --server.headless true

# Terminal 5: Tracker Agent
python -m tracker.agent
```

### 5. Access

| Service | URL |
|---------|-----|
| Backend API | http://localhost:5000 |
| User Dashboard | http://localhost:8501 |
| Admin Dashboard | http://localhost:8502 |
| HTML Dashboard | http://localhost:5000/dashboard/default |
| Health Check | http://localhost:5000/health |

---

## Deploying to Employees

### Option A: Standalone Installer (no Python needed)

**macOS:**
```bash
export INSTALLER_BACKEND_URL=https://your-ngrok-url.ngrok-free.dev
python installer/mac/build.py
# Output: dist/ZinniaAxion.app — distribute to employees
```

**Windows:**
```bash
set INSTALLER_BACKEND_URL=https://your-ngrok-url.ngrok-free.dev
python installer/windows/build.py
# Output: dist/ZinniaAxion.exe — distribute to employees
```

The installer:
1. Opens a setup GUI on first run (employee enters their User ID)
2. Saves config to `~/.zinnia-axion/.env`
3. Installs auto-start (LaunchAgent on macOS, Task Scheduler on Windows)
4. Starts the tracker silently in the background

### Option B: GitHub Actions (Windows)

Use the `build-windows.yml` workflow:
1. Go to Actions → "Build Windows Installer"
2. Enter the backend URL
3. Download the artifact `ZinniaAxion-Windows`

### Option C: Manual Setup

On the employee's machine:
```bash
pip install -r requirements.txt -r requirements-<platform>.txt
cp .env.example .env
# Edit .env: set BACKEND_URL to ngrok URL, USER_ID to employee name
python -m tracker.agent
```

---

## Dashboards

### User Dashboard (port 8501)

Personal productivity view for individual employees.

- **Metric cards** — productive time, non-productive time, total tracked
- **State distribution** — horizontal bar showing productive vs. non-productive %
- **Daily trend** — area chart of productive vs. non-productive over N days
- **Productive vs. Non-Productive line chart** — daily line graph
- **App-wise breakdown** — stacked horizontal bar per application
- **Auto-refresh** — optional 30-second auto-refresh
- **Per-user filtering** — via `?user_id=X` query parameter

### Admin Dashboard (port 8502)

Central management view for administrators.

- **Team metrics** — total users, avg productive %, avg non-productive %, total tracked time
- **Summary link** — one-click access to AI-generated team report
- **Leaderboard table** — all users ranked by non-productive % (highest first)
  - Color-coded rows (red = high non-productive, green = high productive)
  - **Tracker status column** — green dot (Online) / red dot (Offline, Xm ago)
  - View and Delete actions per user
- **User detail view** — click "View" for drill-down:
  - Summary metrics
  - Non-productive app breakdown chart
  - 7-day daily trend line chart
- **Auto-refresh** — 10-second auto-refresh (configurable)

### Summary Page

Accessible from the admin dashboard via the "View Summary" button.

- **Team metrics row** — team size, avg productive %, total productive minutes, total tracked
- **AI-generated report** (OpenAI) with sections:
  - Executive Summary (3–5 sentence overview)
  - What's Going Well (3 bullets)
  - What Needs Attention (2–3 bullets)
  - Recommended Next Steps (3 bullets)
  - Data Note
- **Heuristic fallback** when OpenAI is unavailable
- **Regenerate button** — force a fresh summary
- **5-minute cache** — avoids redundant API calls

### HTML Dashboard

Self-contained HTML page served by the backend. Accessible remotely via ngrok.

```
http://localhost:5000/dashboard/<user_id>
https://<ngrok-url>/dashboard/<user_id>
```

---

## API Reference

### Telemetry

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/track` | Ingest batch of telemetry events |
| `GET` | `/health` | Health check |

### Productivity Queries

| Method | Endpoint | Query Params | Description |
|--------|----------|-------------|-------------|
| `GET` | `/summary/today` | `?user_id=X` | Today's productive/non-productive totals |
| `GET` | `/apps` | `?user_id=X` | Per-app breakdown for today |
| `GET` | `/daily` | `?days=7&user_id=X` | Daily time-series |
| `GET` | `/dashboard/<user_id>` | — | Self-contained HTML dashboard |

### Admin

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/leaderboard` | All users ranked by non-productive % |
| `GET` | `/admin/user/<id>/non-productive-apps` | Non-productive apps for a user today |
| `GET` | `/admin/tracker-status` | Online/offline status of all trackers |
| `DELETE` | `/admin/user/<id>` | Delete all data for a user |

### Maintenance

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/cleanup` | Manually purge old events |
| `GET` | `/db-stats` | Database size, event count, date range |

### POST /track — Request Body

```json
{
  "events": [
    {
      "user_id": "john",
      "timestamp": "2026-02-24T10:30:00+05:30",
      "app_name": "Google Chrome",
      "window_title": "GitHub - Pull Request",
      "keystroke_count": 15,
      "mouse_clicks": 3,
      "mouse_distance": 245.7,
      "idle_seconds": 2.1,
      "distraction_visible": false
    }
  ]
}
```

### GET /admin/tracker-status — Response

```json
[
  {
    "user_id": "default",
    "last_seen": "2026-02-24T13:20:29.908474",
    "seconds_ago": 6,
    "status": "online"
  },
  {
    "user_id": "wasim",
    "last_seen": "2026-02-24T13:04:23.963340",
    "seconds_ago": 966,
    "status": "offline"
  }
]
```

---

## Configuration

All configuration is via `.env` in the project root. See `.env.example` for the full template.

### Backend

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` for remote access) |
| `FLASK_PORT` | `5000` | Server port |
| `DATABASE_URI` | `sqlite:///telemetry.db` | SQLAlchemy connection string |
| `DATA_RETENTION_DAYS` | `14` | Auto-purge events older than this (0 = keep all) |
| `TIMEZONE` | `Asia/Kolkata` | IANA timezone for day boundaries |

### Tracker Agent

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_URL` | `http://127.0.0.1:5000` | Backend URL (use ngrok URL for remote) |
| `USER_ID` | `default` | Unique identifier for this employee |
| `POLL_INTERVAL_SEC` | `1` | How often to sample (seconds) |
| `BATCH_INTERVAL_SEC` | `10` | How often to send batches |
| `BUFFER_FILE` | `tracker/buffer.json` | Offline buffer file path |
| `WAKE_THRESHOLD_SEC` | `30` | Gap threshold for sleep/wake detection |
| `WINDOW_TITLE_MODE` | `full` | `full` / `redacted` / `off` |
| `GHOST_APPS` | *(system apps)* | Apps to ignore when no interaction |

### Productivity Thresholds

| Variable | Default | Description |
|----------|---------|-------------|
| `BUCKET_SIZE_SEC` | `10` | Time window for productivity classification |
| `PRODUCTIVE_INTERACTION_THRESHOLD` | `2` | Min keystrokes + clicks per bucket |
| `PRODUCTIVE_KEYSTROKE_THRESHOLD` | `1` | Min keystrokes alone per bucket |
| `PRODUCTIVE_MOUSE_THRESHOLD` | `1` | Min mouse clicks alone per bucket |
| `MOUSE_MOVEMENT_THRESHOLD` | `8` | Min mouse movement (pixels) for reading detection |
| `IDLE_AWAY_THRESHOLD` | `30` | Seconds idle before user is considered away |
| `MOUSE_MOVEMENT_MIN_SAMPLES` | `3` | Min samples with movement (anti-wiggle) |

### Anti-Cheat

| Variable | Default | Description |
|----------|---------|-------------|
| `MIN_ZERO_SAMPLE_RATIO` | `0.25` | Min fraction of zero-interaction samples (natural typing has pauses) |
| `MIN_DISTINCT_VALUES` | `2` | Min distinct per-sample interaction values |

### Distraction Detection

| Variable | Default | Description |
|----------|---------|-------------|
| `DISTRACTION_MIN_RATIO` | `0.3` | Fraction of samples with visible distraction to block reading pathway |

### App Classification

| Variable | Default | Description |
|----------|---------|-------------|
| `NON_PRODUCTIVE_APPS` | `youtube,netflix,reddit,...` | Always classified as non-productive |
| `MEETING_APPS` | `zoom,microsoft teams,...` | Always classified as productive |
| `BROWSER_APPS` | `safari,chrome,...` | Enables per-website breakdown in dashboard |

### Frontend & AI

| Variable | Default | Description |
|----------|---------|-------------|
| `API_BASE_URL` | `http://127.0.0.1:5000` | Backend URL for Streamlit dashboards |
| `OPENAI_API_KEY` | *(empty)* | Enables AI-generated summaries |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model for AI summaries |

---

## Security & Anti-Cheat

### Anti-Cheat Detection

The system detects fake productivity from auto-clickers and key repeaters:

1. **Zero-sample ratio** — Real typing is bursty (fast bursts then pauses). Auto-clickers produce constant input with no gaps. If fewer than 25% of samples have zero interaction, the bucket is flagged as suspicious.

2. **Distinct values** — Real typing produces varied keystroke counts per sample. Auto-clickers produce 1–2 repeating values. If fewer than 2 distinct per-sample interaction values are found, the bucket is flagged.

3. **Both conditions must trigger** — A bucket is only marked suspicious when both checks fail simultaneously, reducing false positives.

### Multi-Monitor Distraction Detection

- The tracker scans **all visible windows** across all monitors (not just the focused app)
- Uses `CGWindowListCopyWindowInfo` on macOS, `EnumWindows` on Windows
- If a non-productive app (YouTube, Netflix, etc.) is visible on another monitor, split-view, or Picture-in-Picture, the sample is flagged `distraction_visible = true`
- If ≥ 30% of samples in a bucket are flagged, the "active presence" (reading) pathway is blocked — the user is likely watching the distraction

### Sleep/Wake Detection

- If the gap between consecutive tracker samples exceeds `WAKE_THRESHOLD_SEC` (default 30s), the system assumes the machine was asleep
- On wake: flushes pre-sleep batch, resets input counters, skips the first inflated-idle sample

---

## Privacy

Zinnia Axion is designed with privacy as a core principle:

| What | Captured? | Details |
|------|-----------|---------|
| Keystroke **content** | **Never** | Only counts are recorded |
| Mouse click **targets** | **Never** | Only click counts |
| Window titles | **Configurable** | `full`, `redacted` (keywords only), or `off` |
| Screenshots | **Never** | No visual capture |
| File contents | **Never** | No file access |
| Browsing URLs | **Never** | Only app name + window title (if enabled) |
| AI summary data | **Aggregated only** | No window titles or personal data sent to OpenAI |

### Window Title Modes

| Mode | Example Window Title | Stored As |
|------|---------------------|-----------|
| `full` | "RE: Salary Review - Gmail" | "RE: Salary Review - Gmail" |
| `redacted` | "RE: Salary Review - Gmail" | "gmail" |
| `off` | "RE: Salary Review - Gmail" | *(empty)* |

---

## Database

### Supported Databases

- **PostgreSQL** (recommended for production)
- **SQLite** (default, good for development)

### Migration: SQLite → PostgreSQL

```bash
# 1. Set up PostgreSQL
createdb telemetry_db
createuser telemetry_user
psql -c "ALTER USER telemetry_user PASSWORD 'telemetry_pass';"
psql -c "GRANT ALL PRIVILEGES ON DATABASE telemetry_db TO telemetry_user;"

# 2. Update .env
# DATABASE_URI=postgresql://telemetry_user:telemetry_pass@localhost:5432/telemetry_db

# 3. Run migration script
python scripts/migrate_sqlite_to_pg.py
```

### Schema

**telemetry_events** table:

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer (PK) | Auto-increment |
| `user_id` | String(128) | Employee identifier |
| `timestamp` | DateTime | UTC timestamp of the sample |
| `app_name` | String(256) | Active application name |
| `window_title` | String(1024) | Window/tab title (respects privacy mode) |
| `keystroke_count` | Integer | Keystrokes in the interval (count only) |
| `mouse_clicks` | Integer | Mouse clicks in the interval |
| `mouse_distance` | Float | Mouse travel in pixels |
| `idle_seconds` | Float | OS-reported idle time |
| `distraction_visible` | Boolean | Non-productive app visible on another screen |

---

## Uninstallation

See [UNINSTALL.md](UNINSTALL.md) for platform-specific uninstallation instructions.

**Quick summary:**

**macOS:**
```bash
# Remove LaunchAgent
launchctl unload ~/Library/LaunchAgents/com.telemetry.tracker.plist
rm ~/Library/LaunchAgents/com.telemetry.tracker.plist
# Remove app and config
rm -rf /Applications/ZinniaAxion.app ~/.zinnia-axion
```

**Windows:**
```powershell
# Remove scheduled task
schtasks /Delete /TN "ZinniaAxion" /F
# Remove app and config
Remove-Item -Recurse "$env:LOCALAPPDATA\ZinniaAxion"
Remove-Item -Recurse "$env:USERPROFILE\.zinnia-axion"
```

---

## License

Internal use only. All rights reserved.
