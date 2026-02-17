# Telemetry Productivity Tracker

A privacy-conscious, telemetry-driven productivity tracker that runs locally and gives you insights into how you spend your computer time.

**Key privacy guarantee:** Only interaction *counts* are recorded — keystroke content is **never** captured.

---

## Architecture

```
┌────────────┐      POST /track       ┌──────────────┐
│   Tracker   │ ───────────────────▶  │   Flask API   │
│   Agent     │   (batched JSON)      │   (Backend)   │
│  (Python)   │                       │  + SQLite DB  │
└────────────┘                       └──────┬───────┘
                                            │ REST APIs
                                            ▼
                                     ┌──────────────┐
                                     │  Streamlit    │
                                     │  Dashboard    │
                                     │  (Frontend)   │
                                     └──────────────┘
```

### Components

| Component | Path | Description |
|-----------|------|-------------|
| **Backend** | `backend/` | Flask REST API + SQLAlchemy models + productivity inference engine |
| **Tracker** | `tracker/` | Local agent collecting window, keyboard/mouse counts, idle time |
| **Frontend** | `frontend/` | Streamlit dashboard with charts and metrics |

---

## Productivity States

| State | Meaning |
|-------|---------|
| `focused_work` | Productive app + high interaction (≥ threshold) |
| `passive_work` | Productive app + low interaction within grace period |
| `off_device_work` | Productive app + zero interaction beyond grace (phone/notebook proxy) |
| `non_productive` | Non-productive app (regardless of interaction) |
| `idle_unknown` | No interaction + unknown context |

Each time bucket also includes a **confidence score** [0..1] based on interaction density, event coverage, and idle penalty.

---

## Quick Start

### 1. Clone & install

```bash
cd telemetry-productivity-tracker

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# Install core dependencies
pip install -r requirements.txt

# Install OS-specific deps
pip install -r requirements-macos.txt    # macOS
# pip install -r requirements-windows.txt  # Windows
# pip install -r requirements-linux.txt    # Linux
```

### 2. Configure

Copy and edit the environment file:

```bash
cp .env.example .env
# Edit .env to adjust thresholds, app lists, ports, etc.
```

### 3. Start the backend

```bash
python -m backend.app
```

The Flask API will start on `http://127.0.0.1:5000`.

### 4. Start the tracker agent

```bash
python -m tracker.agent
```

> **macOS note:** You may need to grant Accessibility permissions to your terminal app in System Settings → Privacy & Security → Accessibility for window title and input monitoring to work.

### 5. Start the dashboard

```bash
streamlit run frontend/dashboard.py
```

Open `http://localhost:8501` in your browser.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/track` | Ingest batch of telemetry events |
| `GET` | `/summary/today` | State totals for today |
| `GET` | `/apps` | Per-app breakdown for today |
| `GET` | `/daily?days=7` | Daily time-series of state totals |
| `GET` | `/health` | Health check |

### POST /track — Request body

```json
{
  "events": [
    {
      "timestamp": "2026-02-06T14:30:00+00:00",
      "app_name": "Code",
      "window_title": "main.py — my-project",
      "keystroke_count": 42,
      "mouse_clicks": 3,
      "mouse_distance": 1200.5,
      "idle_seconds": 0.8
    }
  ]
}
```

---

## Configuration (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_HOST` | `127.0.0.1` | Backend bind address |
| `FLASK_PORT` | `5000` | Backend port |
| `DATABASE_URI` | `sqlite:///telemetry.db` | SQLAlchemy database URI |
| `BACKEND_URL` | `http://127.0.0.1:5000` | Tracker → backend URL |
| `POLL_INTERVAL_SEC` | `1` | Tracker sampling interval |
| `BATCH_INTERVAL_SEC` | `10` | Tracker batch flush interval |
| `BUCKET_SIZE_SEC` | `60` | Productivity bucket width |
| `FOCUSED_INTERACTION_THRESHOLD` | `30` | Min interactions for "focused" |
| `PASSIVE_GRACE_SEC` | `120` | Grace period before off-device |
| `IDLE_THRESHOLD_SEC` | `300` | Idle threshold for idle_unknown |
| `PRODUCTIVE_APPS` | *(see .env)* | Comma-separated productive app substrings |
| `NON_PRODUCTIVE_APPS` | *(see .env)* | Comma-separated non-productive app substrings |

---

## Project Structure

```
telemetry-productivity-tracker/
├── .env                        # Configuration (git-ignored)
├── .env.example                # Template configuration
├── requirements.txt            # Core Python dependencies
├── requirements-macos.txt      # macOS-specific deps
├── requirements-windows.txt    # Windows-specific deps
├── requirements-linux.txt      # Linux-specific deps
├── README.md
├── backend/
│   ├── __init__.py
│   ├── app.py                  # Flask application + routes
│   ├── config.py               # Configuration loader
│   ├── models.py               # SQLAlchemy ORM models
│   └── productivity.py         # Productivity inference engine
├── tracker/
│   ├── __init__.py
│   ├── agent.py                # Main tracker loop + batching
│   └── platform/
│       ├── __init__.py
│       ├── base.py             # Abstract PlatformCollector
│       ├── factory.py          # OS auto-detection factory
│       ├── macos.py            # macOS collector
│       ├── windows.py          # Windows collector
│       └── linux.py            # Linux collector
└── frontend/
    ├── __init__.py
    └── dashboard.py            # Streamlit dashboard
```

---

## Privacy & Security

- **No keystroke content** is ever recorded — only counts per interval.
- All data stays **local** (SQLite file on your machine).
- Window titles are captured for app classification; disable with `CAPTURE_WINDOW_TITLE=false`.
- The tracker agent buffers locally if the backend is down — no data is sent externally.
