# Productivity Tracker — Known Loopholes & Future Improvements

## HIGH Severity

### 1. ~~Multi-Monitor Blind Spot~~ SOLVED
- **Problem:** Tracker only captures the active/foreground window. YouTube on Monitor 1 + Cursor (focused) on Monitor 2 = tracker sees only Cursor = productive.
- **Fix:** Added `get_visible_windows()` using `CGWindowListCopyWindowInfo` to enumerate ALL on-screen windows across monitors. Tracker checks each visible window against `NON_PRODUCTIVE_APPS` and sets `distraction_visible=True` on the sample. Productivity engine blocks the "active presence" (reading) pathway when distraction ratio ≥ `DISTRACTION_MIN_RATIO` (30%). Genuine typing/clicking still counts as productive but with a confidence penalty.
- **Status:** DONE

### 2. ~~Meetings / Calls Misclassified~~ SOLVED
- **Problem:** Zoom/Teams/Meet calls where you're actively participating but not typing or clicking. Tracker sees zero interaction = non-productive. But meetings ARE work.
- **Fix:** Added `MEETING_APPS` list in `.env`. Meeting apps are always classified as productive (Rule 2 in decision tree).
- **Status:** DONE

## MEDIUM-HIGH Severity

### 3. ~~Split-Screen / Side-by-Side~~ SOLVED
- **Problem:** macOS Split View — Cursor on left, Safari (YouTube) on right. Only the last-clicked pane is "active." YouTube occupies 50% of screen but is invisible to the tracker.
- **Fix:** Same mechanism as multi-monitor (Loophole 1). `CGWindowListCopyWindowInfo` sees both split-view panes. Non-productive pane triggers `distraction_visible` flag.
- **Status:** DONE

### 4. ~~Privacy Risk — Window Titles~~ SOLVED
- **Problem:** Window titles leak sensitive data: email subjects, document names, private URLs. Stored in plaintext in SQLite.
- **Fix:** Added `WINDOW_TITLE_MODE` in `.env` with 3 modes: `full` (default, unchanged), `redacted` (keeps only classification keywords like "youtube"/"zoom", strips everything else), `off` (no titles). Redaction happens at the tracker before data is transmitted.
- **Status:** DONE

## MEDIUM Severity

### 5. Browser Background Tabs
- **Problem:** YouTube playing audio in a background tab. Active tab is work-related. Tracker only sees the active tab's title.
- **Fix:** Would require a browser extension to enumerate all tabs. Impractical without one.
- **Effort:** HIGH (requires browser extension)

### 6. ~~Picture-in-Picture (PiP)~~ SOLVED
- **Problem:** YouTube PiP playing in a corner while coding. PiP is not the active window — tracker doesn't see it.
- **Fix:** Same mechanism as multi-monitor (Loophole 1). `CGWindowListCopyWindowInfo` lists PiP windows alongside regular windows. The 100×100 px minimum-size filter keeps the PiP window (typically ~300×170) while excluding tiny UI elements.
- **Status:** DONE

### 7. ~~Phone at Desk + Occasional Mouse Wiggle~~ SOLVED
- **Problem:** User scrolling phone but occasionally nudges mouse to prevent screen lock. Mouse movement > threshold + idle < threshold = "reading code" = productive. But they're not.
- **Fix:** Added `MOUSE_MOVEMENT_MIN_SAMPLES=15` anti-wiggle check. Now counts how many individual 1-second samples within a bucket have mouse movement. Real reading = 20-50 samples with movement; wiggle = 1-5 samples. Must have >=15 samples with movement to count as "reading".
- **Status:** DONE

### 8. ~~Database Growth~~ SOLVED
- **Problem:** 1 sample/second = 86,400 rows/day = ~2.6M rows/month. SQLite performance degrades on large datasets.
- **Fix:** Added `DATA_RETENTION_DAYS=14` in `.env`. Auto-cleanup runs on backend startup, deleting events older than 14 days. Manual `POST /cleanup` endpoint and `GET /db-stats` endpoint also added.
- **Status:** DONE

### 9. ~~Timezone / Travel~~ SOLVED
- **Problem:** Timestamps stored in UTC. `_today_range()` uses UTC. For non-UTC users (e.g., IST = UTC+5:30), "today" on the dashboard doesn't match local day.
- **Fix:** Added `TIMEZONE=Asia/Kolkata` in `.env`. `_today_range()` and `_day_range()` now use local timezone for day boundaries, converting to UTC for DB queries.
- **Status:** DONE

## LOW-MEDIUM Severity

### 10. ~~Gaming the System (Auto-Clickers / Key Repeaters)~~ SOLVED
- **Problem:** Macro tool pressing a key every 5 seconds fakes consistent keystrokes = productive. Person could be asleep.
- **Fix:** Added anti-cheat detection using two signals: (1) zero-sample ratio — real typing has natural pauses (>=25% of samples are zero), auto-clickers don't; (2) distinct values — real typing produces many different per-sample counts, auto-clickers produce 1-2. Both must be suspicious to flag. Suspicious buckets are downgraded to non_productive.
- **Status:** DONE

## LOW Severity

### 11. ~~Sleep / Hibernate / Lid Close~~ SOLVED
- **Problem:** When laptop sleeps, tracker process is suspended. On wake, there's a gap in data. First sample after wake may have huge `idle_seconds` (equal to the entire sleep duration), polluting the first post-wake bucket.
- **Fix:** Two-layer defence: (1) Tracker compares wall-clock time between iterations; if gap > `WAKE_THRESHOLD_SEC` (30s), it logs the wake, flushes the pre-sleep batch, resets stale input counters, and **skips the first post-wake sample** (the one with inflated idle). (2) Productivity engine defensively caps `max_idle` per sample at `bucket_size` — even if an inflated value sneaks through, it can't destroy the confidence score.
- **Status:** DONE

### 12. Remote Desktop / VMs
- **Problem:** Remote Desktop app (Parsec, RDP, VNC) shows as active app. Tracker has no visibility into what's running inside the remote session.
- **Fix:** Edge case — not worth solving unless remote work is primary workflow.
- **Effort:** LOW (niche)

---

## Priority Implementation Order
1. **Meetings detection** (HIGH severity, LOW effort) — quick win
2. **Timezone support** (MEDIUM severity, LOW effort) — quick win
3. **Database archival** (MEDIUM severity, LOW effort) — quick win
4. **Privacy: title redaction** (MEDIUM-HIGH severity, LOW effort) — quick win
5. **Mouse wiggle detection** (MEDIUM severity, MEDIUM effort)
6. **Anti-cheat: interaction variance** (LOW-MEDIUM severity, MEDIUM effort)
7. **Multi-monitor / Split-screen / PiP** (HIGH severity, HIGH effort) — biggest impact but most work
8. **Browser extension for tabs** (MEDIUM severity, HIGH effort) — last resort
