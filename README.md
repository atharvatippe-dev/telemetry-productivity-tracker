# Zinnia Axion

A privacy-conscious, telemetry-driven Zinnia Axion that silently records how employees spend their computer time and surfaces the data through real-time dashboards. Deploys as a standalone executable on **macOS** and **Windows** — no Python installation needed on end-user machines.

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
11. [Uninstallation](#uninstallation)
12. [Known Limitations & Roadmap](#known-limitations--roadmap)

---

## Architecture

```
                          ┌──────────────────────────────────────────┐
                          │             Admin's Machine              │
                          │                                          │
┌──────────────┐  POST    │  ┌────────────┐       ┌──────────────┐  │
│ Employee PC  │  /track  │  │  Flask API  │──────▶│  PostgreSQL  │  │
│ (Zinnia Axion│─────────▶│  │  (Backend)  │       │   / SQLite   │  │
│  Agent .exe) │  (JSON)  │  └─────┬──────┘       └──────────────┘  │
└──────────────┘          │        │ REST                            │
                          │        ▼                                 │
 ┌──────────────┐         │  ┌──────────────┐  ┌─────────────────┐  │
│ Employee PC  │         │  │  Streamlit   │  │   Streamlit     │  │
│ (Zinnia Axion│────────▶│  │  User Dash   │  │   Admin Dash    │  │
│  Agent .exe) │         │  │  :8501       │  │   :8502         │  │
 └──────────────┘         │  └──────────────┘  └─────────────────┘  │
                          │        ▲                                 │
         ...              │        │                                 │
                          │  ┌──────────────┐                       │
 Employees access their   │  │  HTML User   │                       │
 dashboard via browser:   │  │  Dashboard   │                       │
 /dashboard/<user_id>     │  │  (Chart.js)  │                       │
                          │  └──────────────┘                       │
                          │                                          │
                          │  ┌──────────────┐                       │
                          │  │    ngrok     │ (exposes backend to   │
                          │  │   tunnel     │  remote employees)    │
                          │  └──────────────┘                       │
                          └──────────────────────────────────────────┘
```

### Components

| Component | Path | Description |
|-----------|------|-------------|
| **Backend** | `backend/` | Flask REST API + SQLAlchemy models + productivity inference engine |
| **Zinnia Axion Agent** | `tracker/` | Local agent collecting active window, keyboard/mouse counts, idle time |
| **User Dashboard** | `frontend/dashboard.py` | Streamlit dashboard for personal productivity stats |
| **Admin Dashboard** | `frontend/admin_dashboard.py` | Streamlit admin panel with leaderboard and per-user drill-down |
| **HTML Dashboard** | `backend/templates/dashboard.html` | Self-contained Chart.js dashboard served by Flask (no install needed) |
| **macOS Installer** | `installer/mac/` | PyInstaller build + LaunchAgent auto-start |
| **Windows Installer** | `installer/windows/` | PyInstaller build + Task Scheduler auto-start |
| **CI/CD** | `.github/workflows/` | GitHub Actions workflow for automated Windows `.exe` builds |

---

## Productivity Model

Zinnia Axion uses a **2-state productivity model** (`productive` / `non_productive`) powered by a decision tree in `backend/productivity.py`.

### Decision Tree (per 60-second bucket)

```
1. Is the app a MEETING app (Zoom, Teams, Meet)?
   └─ YES → productive (meetings are always work)

2. Is the app a NON-PRODUCTIVE app (YouTube, Netflix, Reddit, etc.)?
   └─ YES → non_productive

3. Does the bucket have enough interaction (keystrokes + clicks ≥ threshold)?
   ├─ YES → productive (actively working)
   └─ NO  → Check active presence...

4. Active Presence: mouse movement ≥ threshold AND idle < threshold AND no distraction?
   ├─ YES → productive (reading/reviewing code)
   └─ NO  → non_productive
```

### Anti-Cheat Detection

Each bucket also runs through anti-cheat checks:
- **Zero-sample ratio:** Real typing has natural pauses (≥25% of samples are zero); auto-clickers don't.
- **Distinct values:** Real typing produces many different per-sample counts; auto-clickers produce 1-2 repeating values.
- **Anti-wiggle:** Requires ≥15 distinct 1-second samples with mouse movement to count as "reading" (defeats occasional mouse nudges).

### Multi-Monitor / Distraction Detection

On macOS, Zinnia Axion enumerates ALL visible windows across monitors using `CGWindowListCopyWindowInfo`. If a non-productive app (YouTube, Netflix, etc.) is visible on any monitor while the user is working, the `distraction_visible` flag is set. The productivity engine blocks the "active presence" pathway when the distraction ratio exceeds 30%.

---

## Features

- **Cross-platform:** macOS, Windows, Linux (Zinnia Axion agent)
- **Standalone executables:** PyInstaller-bundled `.app` (macOS) and `.exe` (Windows) — no Python on employee machines
- **First-run setup wizard:** Tkinter GUI for user ID and backend URL configuration
- **Auto-start on boot:** macOS LaunchAgent / Windows Task Scheduler (with Startup folder fallback)
- **Offline resilience:** Zinnia Axion Agent buffers events locally (JSON file) when the backend is unreachable
- **Sleep/wake handling:** Detects laptop sleep, flushes pre-sleep data, skips inflated post-wake samples
- **Ghost app filtering:** Suppresses duplicate events when no actual app change occurred
- **Window title redaction:** 3 modes — `full`, `redacted` (keeps only classification keywords), `off`
- **Auto data retention:** Purges events older than N days on backend startup
- **Browser website extraction:** Parses browser window titles to extract website/service names
- **Real-time dashboards:** Auto-refreshing Streamlit and HTML dashboards with Chart.js
- **Admin leaderboard:** Ranks all employees by productive/non-productive time
- **User deletion:** Admin can delete all data for any user via the dashboard
- **Ngrok integration:** Expose the backend to remote employees through a secure tunnel

---

## Project Structure

```
zinnia-axion/
├── .env.example                        # Template configuration (all settings documented)
├── .github/
│   └── workflows/
│       └── build-windows.yml           # GitHub Actions: automated Windows .exe build
├── README.md                           # This file
├── TODO.md                             # Known loopholes & future improvements
├── UNINSTALL.md                        # Uninstall instructions (Windows & macOS)
├── ZinniaAxion.spec                    # PyInstaller spec for macOS .app
├── architecture.svg                    # Architecture diagram
├── requirements.txt                    # Core Python dependencies
├── requirements-macos.txt              # macOS-specific (pyobjc)
├── requirements-windows.txt            # Windows-specific (pywin32, psutil)
├── requirements-linux.txt              # Linux-specific (psutil)
│
├── backend/
│   ├── __init__.py
│   ├── app.py                          # Flask application + all REST routes
│   ├── config.py                       # Configuration loader (from .env)
│   ├── models.py                       # SQLAlchemy ORM model (TelemetryEvent)
│   ├── productivity.py                 # Productivity inference engine (bucketize, summarize)
│   └── templates/
│       └── dashboard.html              # Self-contained HTML dashboard (Chart.js)
│
├── tracker/
│   ├── __init__.py
│   ├── agent.py                        # Main tracker loop + batching + buffer
│   └── platform/
│       ├── __init__.py
│       ├── base.py                     # Abstract PlatformCollector interface
│       ├── factory.py                  # OS auto-detection factory
│       ├── macos.py                    # macOS collector (AppKit, Quartz, pynput)
│       ├── windows.py                  # Windows collector (pywin32, psutil, pynput)
│       └── linux.py                    # Linux collector (xdotool, xprintidle, pynput)
│
├── frontend/
│   ├── __init__.py
│   ├── dashboard.py                    # Streamlit user dashboard
│   └── admin_dashboard.py             # Streamlit admin dashboard (leaderboard + drill-down)
│
├── installer/
│   ├── __init__.py
│   ├── mac/
│   │   ├── __init__.py
│   │   ├── launcher.py                # macOS .app entry point
│   │   ├── build.py                   # PyInstaller build script (macOS)
│   │   ├── build_config.py            # Baked-in backend URL
│   │   ├── setup_gui.py              # First-run Tkinter setup wizard
│   │   └── launchagent.py            # macOS LaunchAgent auto-start
│   └── windows/
│       ├── __init__.py
│       ├── launcher.py                # Windows .exe entry point
│       ├── build.py                   # PyInstaller build script (Windows)
│       ├── build_config.py            # Baked-in backend URL
│       ├── setup_gui.py              # First-run Tkinter setup wizard
│       └── autostart.py              # Windows Task Scheduler auto-start
│
└── scripts/
    └── migrate_sqlite_to_pg.py        # One-time SQLite → PostgreSQL migration
```

---

## Quick Start (Developer)

### 1. Clone & Install

```bash
git clone https://github.com/atharvatippe-dev/zinnia-axion.git
cd zinnia-axion

python -m venv .venv
source .venv/bin/activate          # macOS/Linux
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt

# Install OS-specific dependencies
pip install -r requirements-macos.txt      # macOS
# pip install -r requirements-windows.txt  # Windows
# pip install -r requirements-linux.txt    # Linux
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — adjust DATABASE_URI, app lists, thresholds, timezone, etc.
```

### 3. Start the Backend

```bash
python -m backend.app
```

The Flask API starts on `http://127.0.0.1:5000`.

### 4. Start the Zinnia Axion Agent

```bash
python -m tracker.agent
```

> **macOS note:** Grant Accessibility permissions to your terminal in System Settings → Privacy & Security → Accessibility for window title and input monitoring.

### 5. Start the Dashboards

```bash
# User dashboard
streamlit run frontend/dashboard.py --server.port 8501

# Admin dashboard
streamlit run frontend/admin_dashboard.py --server.port 8502
```

### 6. (Optional) Expose via ngrok

```bash
ngrok http 5000
```

Use the ngrok URL as the `BACKEND_URL` for remote Zinnia Axion Agents.

---

## Deploying to Employees

### Windows (.exe via GitHub Actions)

1. Go to **Actions** → **Build Windows Installer** → **Run workflow**
2. Enter your backend URL (ngrok or server URL) and click **Run workflow**
3. Wait ~2 minutes for the build to complete
4. Download the **ZinniaAxion-Windows** artifact (`.zip` file)
5. Unzip and share the `ZinniaAxion.exe` with employees via email, Slack, or file share
6. Employee runs the `.exe` → enters their user ID and backend URL in the setup wizard → tracking starts automatically

The `.exe` is fully self-contained (no Python installation required). It auto-starts on boot via Windows Task Scheduler.

### macOS (.app)

```bash
# From the project root on a Mac:
python installer/mac/build.py
```

This produces `dist/ZinniaAxion.app`. Distribute the `.app` to macOS users. It auto-starts on login via a LaunchAgent.

---

## Dashboards

### 1. Admin Dashboard (Streamlit) — `http://localhost:8502`

For the admin/manager. Shows:
- **Leaderboard:** All employees ranked by productive vs. non-productive time
- **Per-user drill-down:** Click "View" to see an employee's non-productive apps, 7-day trend, and detailed breakdown
- **Delete user:** Remove all data for a specific user

### 2. User Dashboard (Streamlit) — `http://localhost:8501`

For the admin or local user. Shows:
- Metric cards (productive %, non-productive %, total time)
- State distribution (horizontal bar)
- 7-day daily trend (stacked area + line chart)
- App-wise breakdown (horizontal stacked bar)

### 3. HTML User Dashboard — `http://<backend>/dashboard/<user_id>`

For employees to view their own stats in a browser. **No installation required** — just visit the URL. Shows the same visualizations as the Streamlit user dashboard using Chart.js with auto-refresh every 30 seconds.

Employees running Zinnia Axion can access their dashboard at:
```
http://<ngrok-url>/dashboard/<their-user-id>
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/track` | Ingest a batch of telemetry events |
| `GET` | `/summary/today?user_id=X` | Today's productivity totals for a user |
| `GET` | `/apps?user_id=X` | Per-app breakdown for today |
| `GET` | `/daily?user_id=X&days=7` | Daily time-series of productivity totals |
| `GET` | `/dashboard/<user_id>` | Self-contained HTML dashboard for a user |
| `GET` | `/health` | Health check |
| `GET` | `/db-stats` | Database statistics |
| `POST` | `/cleanup` | Purge events older than retention period |
| `GET` | `/admin/leaderboard` | All users ranked by non-productive % |
| `GET` | `/admin/user/<user_id>/non-productive-apps` | Non-productive app breakdown for a user |
| `DELETE` | `/admin/user/<user_id>` | Delete all events for a user |

### POST /track — Request Body

```json
{
  "events": [
    {
      "user_id": "john.doe",
      "timestamp": "2026-02-18T14:30:00+05:30",
      "app_name": "Code",
      "window_title": "main.py - my-project",
      "keystroke_count": 42,
      "mouse_clicks": 3,
      "mouse_distance": 1200.5,
      "idle_seconds": 0.8,
      "distraction_visible": false
    }
  ]
}
```

---

## Configuration

All settings are controlled via the `.env` file (see `.env.example` for full documentation).

### Backend & Database

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_HOST` | `127.0.0.1` | Backend bind address |
| `FLASK_PORT` | `5000` | Backend port |
| `DATABASE_URI` | `sqlite:///telemetry.db` | SQLAlchemy database URI (SQLite or PostgreSQL) |
| `DATA_RETENTION_DAYS` | `14` | Auto-purge events older than N days (0 = keep forever) |
| `TIMEZONE` | `UTC` | Local timezone for day boundary calculations |

### Zinnia Axion Agent

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_URL` | `http://127.0.0.1:5000` | URL the Zinnia Axion Agent POSTs to |
| `POLL_INTERVAL_SEC` | `1` | Sampling interval (seconds) |
| `BATCH_INTERVAL_SEC` | `10` | Batch flush interval (seconds) |
| `BUFFER_FILE` | `~/.telemetry-tracker/buffer.json` | Local buffer for offline resilience |
| `USER_ID` | `default` | User identifier sent with each event |
| `WINDOW_TITLE_MODE` | `full` | Title capture mode: `full`, `redacted`, or `off` |

### Productivity Thresholds

| Variable | Default | Description |
|----------|---------|-------------|
| `BUCKET_SIZE_SEC` | `60` | Bucket width for productivity inference |
| `PRODUCTIVE_INTERACTION_THRESHOLD` | `10` | Min combined interactions for "productive" |
| `PRODUCTIVE_KEYSTROKE_THRESHOLD` | `5` | Min keystrokes alone for "productive" |
| `PRODUCTIVE_MOUSE_THRESHOLD` | `3` | Min mouse clicks alone for "productive" |
| `MOUSE_MOVEMENT_THRESHOLD` | `50` | Min mouse pixels for active presence |
| `IDLE_AWAY_THRESHOLD` | `30` | Seconds idle before user is "away" |
| `MOUSE_MOVEMENT_MIN_SAMPLES` | `15` | Anti-wiggle: min 1s samples with movement |
| `DISTRACTION_MIN_RATIO` | `0.3` | Fraction of samples with distraction to block reading pathway |

### App Classification

| Variable | Default | Description |
|----------|---------|-------------|
| `NON_PRODUCTIVE_APPS` | `youtube,netflix,reddit,...` | Always non-productive |
| `MEETING_APPS` | `zoom,microsoft teams,...` | Always productive (calls/meetings) |
| `BROWSER_APPS` | `safari,google chrome,...` | Parsed for website-level breakdown |
| `GHOST_APPS` | *(OS-specific)* | Transient system windows to ignore |

---

## Security & Anti-Cheat

| Mechanism | Description |
|-----------|-------------|
| **No keystroke content** | Only counts are recorded — never the actual keys pressed |
| **Window title redaction** | Configurable: `full`, `redacted`, or `off` |
| **Local-first** | Data stays on your infrastructure (self-hosted backend + database) |
| **Offline buffer** | Events buffered locally if backend is unreachable — never sent to third parties |
| **Anti-auto-clicker** | Zero-sample ratio + distinct-value checks detect macro/bot tools |
| **Anti-mouse-wiggle** | Requires sustained mouse movement (≥15 samples/bucket) to count as "reading" |
| **Multi-monitor distraction** | Detects non-productive apps visible on any monitor (macOS) |
| **Sleep/wake handling** | Skips inflated post-wake samples, caps idle values |

---

## Uninstallation

See [UNINSTALL.md](UNINSTALL.md) for detailed step-by-step instructions for both Windows and macOS.

**Quick summary:**
- **Windows:** End task → delete scheduled task → delete config folder → delete `.exe` and `.zip`
- **macOS:** Quit app → unload LaunchAgent → delete config folder → delete `.app`

> Note: Uninstalling Zinnia Axion from an employee's machine stops future data collection. Historical data already on the server persists until the admin deletes it via the Admin Dashboard.

---

## Known Limitations & Roadmap

See [TODO.md](TODO.md) for the full list of known loopholes and their status.

**Solved:**
- Multi-monitor / split-screen / PiP distraction detection
- Meeting app classification
- Privacy (window title redaction)
- Anti-cheat (auto-clickers, mouse wigglers)
- Sleep/wake handling
- Database growth (auto-retention)
- Timezone support

**Open:**
- Browser background tabs (requires browser extension)
- Remote Desktop / VM visibility (niche edge case)
- Enterprise features: SSO/authentication, centralized config push, role-based access

---

## License

Private / Internal use.
