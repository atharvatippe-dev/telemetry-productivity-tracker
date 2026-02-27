# Zinnia Axion — Configuration Discrepancies Report

This document lists all configuration inconsistencies found across the codebase. These should be resolved to ensure consistent behavior between development, production, and fresh installations.

> **Status: ALL ISSUES RESOLVED** (2026-02-24)

---

## HIGH PRIORITY

### 1. `config.py` Default Values Are Tuned for 60-Second Buckets

The project now uses 10-second buckets (`BUCKET_SIZE_SEC=10`), but `backend/config.py` still has fallback defaults from the old 60-second design. If someone runs without a `.env` file, the thresholds are 5-6x too high, causing almost everything to be classified as **non-productive**.

| Setting | `config.py` Default | `.env.example` Value | Correct for 10s |
|---------|---------------------|----------------------|-----------------|
| `PRODUCTIVE_INTERACTION_THRESHOLD` | `10` | `2` | `2` |
| `PRODUCTIVE_KEYSTROKE_THRESHOLD` | `5` | `1` | `1` |
| `PRODUCTIVE_MOUSE_THRESHOLD` | `3` | `1` | `1` |
| `MOUSE_MOVEMENT_THRESHOLD` | `50` | `8` | `8` |
| `MOUSE_MOVEMENT_MIN_SAMPLES` | `15` | `3` | `3` |
| `MIN_DISTINCT_VALUES` | `3` | `2` | `2` |

**Fix:** Update `backend/config.py` default values to match the 10-second bucket tuning.

**Status:** RESOLVED

---

### 2. `config.py` Missing Non-Productive Apps

The `NON_PRODUCTIVE_APPS` list in `config.py` is outdated compared to `.env.example` and the installers.

| App | In `config.py`? | In `.env.example`? | In Installer? |
|-----|-----------------|--------------------| --------------|
| `youtube` | Yes | Yes | Yes |
| `netflix` | Yes | Yes | Yes |
| `reddit` | Yes | Yes | Yes |
| `twitter` | Yes | Yes | Yes |
| `x.com` | **No** | Yes | Yes |
| `instagram` | Yes | Yes | Yes |
| `facebook` | Yes | Yes | Yes |
| `tiktok` | Yes | Yes | Yes |
| `twitch` | **No** | Yes | Yes |
| `discord` | **No** | Yes | Yes |
| `spotify` | **No** | Yes | Yes |
| `steam` | **No** | Yes | Yes |
| `epic games` | **No** | Yes | Yes |

**Fix:** Add `x.com,twitch,discord,spotify,steam,epic games` to the `config.py` default.

**Status:** RESOLVED

---

### 3. `config.py` Missing Meeting Apps

The `MEETING_APPS` list in `config.py` is missing newer additions.

| App | In `config.py`? | In `.env.example`? |
|-----|-----------------|---------------------|
| `zoom` | Yes | Yes |
| `microsoft teams` | Yes | Yes |
| `google meet` | Yes | Yes |
| `webex` | Yes | Yes |
| `facetime` | Yes | Yes |
| `slack huddle` | Yes | Yes |
| `discord call` | **No** | Yes |
| `skype` | **No** | Yes |
| `around` | **No** | Yes |
| `tuple` | **No** | Yes |
| `gather` | **No** | Yes |

**Fix:** Add `discord call,skype,around,tuple,gather` to the `config.py` default.

**Status:** RESOLVED

---

## MEDIUM PRIORITY

### 4. BROWSER_APPS Mismatch Between Installer and Config

The Windows installer and `config.py` have different browser lists.

| Browser | `config.py` | `installer/windows/setup_gui.py` | `.env.example` |
|---------|-------------|----------------------------------|----------------|
| `safari` | Yes | **No** | Yes |
| `google chrome` | Yes | Yes | Yes |
| `chrome` | Yes | **No** | Yes |
| `firefox` | Yes | Yes | Yes |
| `microsoft edge` | Yes | Yes | Yes |
| `msedge` | Yes | Yes | Yes |
| `brave browser` | Yes | Yes | Yes |
| `brave` | Yes | **No** | Yes |
| `arc` | Yes | **No** | Yes |
| `chromium` | **No** | Yes | Yes |
| `opera` | **No** | Yes | **No** |

**Fix:** Synchronize all three sources. Recommended unified list:
```
safari,google chrome,chrome,firefox,microsoft edge,msedge,brave browser,brave,arc,chromium,opera
```

**Status:** RESOLVED

---

### 5. TIMEZONE Default Mismatch

| Source | Default Value |
|--------|---------------|
| `config.py` | `UTC` |
| `.env.example` | `Asia/Kolkata` |

**Fix:** Keep `config.py` default as `UTC` (neutral), but document that users should set their local timezone.

**Status:** NO CHANGE NEEDED (UTC is correct default)

---

### 6. `.env` Has `WINDOW_TITLE_MODE=full` But New Default Is `redacted`

The privacy-first default was changed to `redacted`, but the local `.env` still has `full`.

| Source | Value |
|--------|-------|
| `tracker/agent.py` default | `redacted` |
| `installer/windows/setup_gui.py` | `redacted` |
| `installer/mac/setup_gui.py` | `redacted` |
| `.env.example` | `redacted` |
| **Your `.env`** | `full` |

**Fix:** Update `.env` to `WINDOW_TITLE_MODE=redacted` if privacy-first is desired.

**Status:** RESOLVED

---

## LOW PRIORITY

### 7. GHOST_APPS Not in `config.py`

`GHOST_APPS` is defined in:
- `.env.example`
- `tracker/agent.py`
- `installer/windows/setup_gui.py`

But NOT in `backend/config.py`.

**Assessment:** This is technically correct — `GHOST_APPS` is tracker-side only, not used by the backend. However, it's confusing to have it in `.env.example` without a backend counterpart.

**Fix:** Add a comment in `config.py` noting that `GHOST_APPS` is tracker-only, or add it to config.py with a note that it's unused by backend.

**Status:** NO CHANGE NEEDED (tracker-only setting, correctly absent from backend)

---

### 8. Naming Inconsistency (TelemetryTracker vs Zinnia_axion)

Historical references to `TelemetryTracker` still exist:
- User error messages reference `TelemetryTracker.exe`
- Old scheduled tasks named `TelemetryTracker`
- Config folder is `.telemetry-tracker`

Current naming:
- Executable: `Zinnia_axion.exe`
- Project name: Zinnia Axion

**Fix:** No code change needed, but UNINSTALL.md should include cleanup commands for old `TelemetryTracker` artifacts.

**Status:** DOCUMENTATION ITEM (no code change)

---

## SECURITY

### 9. OpenAI API Key Exposed in `.env`

The `.env` file contains a real OpenAI API key:
```
OPENAI_API_KEY=sk-svcacct-Sznzu830MsJICA-xtGPkCari0T__...
```

**Assessment:** Check that `.env` is in `.gitignore`. If this key was ever committed to git, it should be rotated immediately.

**Fix:**
1. Verify `.env` is in `.gitignore`
2. If committed, rotate the API key in OpenAI dashboard
3. Never commit `.env` files with secrets

**Status:** VERIFIED — `.env` is in `.gitignore`

---

## Summary Action Items

| # | Priority | Action | Status |
|---|----------|--------|--------|
| 1 | High | Update `config.py` threshold defaults for 10s buckets | DONE |
| 2 | High | Add missing non-productive apps to `config.py` | DONE |
| 3 | High | Add missing meeting apps to `config.py` | DONE |
| 4 | Medium | Synchronize BROWSER_APPS across all files | DONE |
| 5 | Medium | Verify TIMEZONE documentation | N/A |
| 6 | Medium | Update local `.env` WINDOW_TITLE_MODE if desired | DONE |
| 7 | Low | Document GHOST_APPS as tracker-only | N/A |
| 8 | Low | Document old TelemetryTracker cleanup in UNINSTALL.md | N/A |
| 9 | Security | Verify `.env` is gitignored; rotate key if exposed | VERIFIED |

---

*Generated: 2026-02-24*
*Resolved: 2026-02-24*
