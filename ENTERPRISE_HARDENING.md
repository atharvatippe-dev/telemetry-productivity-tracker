# Enterprise Hardening v1 -- Implementation Task List

This document lists every change needed to make Zinnia Axion enterprise-safe. Each task is explained in plain English -- what it does, why it matters, which files are involved, and what new configuration is needed.

The system currently works in "demo mode" with no authentication, no access control, and full window titles stored in the database. The tasks below add security layers one by one. Each section is independent and can be implemented in any order.

---

## Section 1: Authenticated /track Ingestion

**Goal:** Only registered devices can send telemetry data to the backend. Right now, anyone who knows the backend URL can POST fake data.

### Task 1.1 -- Add a Device table to the database

**What:** Create a new database table called `Device` that stores every registered Zinnia Axion device.

**Why:** The backend needs a list of known devices so it can reject data from unknown sources. Each device gets a unique ID and a secret key (like a password for machines).

**Where:** `backend/models.py`

**Details:**
- The table has four columns: `device_id` (unique identifier), `device_secret_hash` (the secret stored as a hash, never in plain text), `user_id` (which employee this device belongs to), and `registered_at` (when the device was registered).
- The `device_secret_hash` is stored using the same kind of hashing used for passwords (bcrypt or SHA-256 with salt) so even if the database is compromised, the raw secrets are not exposed.

### Task 1.2 -- Create a device registration endpoint

**What:** Add a new API endpoint `POST /devices/register` that the Zinnia Axion Agent calls on first launch to register itself with the backend.

**Why:** Before a device can send telemetry, it needs to introduce itself and get its credentials stored in the backend.

**Where:** `backend/app.py`

**Details:**
- The Zinnia Axion Agent sends its `device_id`, `user_id`, and `device_secret` to this endpoint.
- The backend hashes the secret and stores the device in the `Device` table.
- This endpoint only needs to be called once per device (on first-run setup).

### Task 1.3 -- Generate device credentials during first-run setup

**What:** When the Zinnia Axion Agent runs for the first time and shows the setup wizard, it generates a random `device_id` (UUID) and a random `device_secret` (32 random bytes, base64-encoded).

**Why:** Each device needs its own unique identity. Generating it automatically means the employee doesn't have to manage credentials.

**Where:** `installer/windows/setup_gui.py`, `installer/mac/setup_gui.py`

**Details:**
- The `device_id` and `device_secret` are saved into the local config file (`config.env`).
- After saving, the setup wizard calls `POST /devices/register` to register the device with the backend.

### Task 1.4 -- Store the device secret securely on the local machine

**What:** Instead of storing the `device_secret` in plain text in `config.env`, store it using the operating system's secure storage.

**Why:** If someone accesses the employee's config file, they shouldn't be able to extract the secret and impersonate the device.

**Where:** `installer/windows/setup_gui.py`, `installer/mac/setup_gui.py`

**Details:**
- On Windows: use DPAPI (`win32crypt.CryptProtectData`) which encrypts data so only the same Windows user on the same machine can decrypt it.
- On macOS: use the Keychain via the `security` command-line tool.
- Fallback: if neither is available, encrypt with a machine-specific key derived from hardware identifiers.

### Task 1.5 -- Sign every POST /track request

**What:** Before sending telemetry data, the Zinnia Axion Agent computes a signature (HMAC-SHA256) over the request and attaches it as HTTP headers.

**Why:** The signature proves the request came from a device that knows the secret, and that the data wasn't tampered with in transit.

**Where:** `tracker/agent.py`

**Details:**
- Three new HTTP headers are added to every POST /track request:
  - `X-Device-Id` -- which device is sending this
  - `X-Timestamp` -- current UTC time (ISO 8601)
  - `X-Signature` -- HMAC-SHA256 computed over the timestamp + SHA256 hash of the request body, using the device secret as the key
- The signature changes with every request because the timestamp and body change.

### Task 1.6 -- Verify signatures on the backend

**What:** The backend checks every incoming `POST /track` request for valid headers and a correct signature before accepting the data.

**Why:** This is the enforcement step -- without it, the signatures would be generated but never checked.

**Where:** `backend/app.py` (new middleware or decorator), new file `backend/auth.py`

**Details:**
- The backend performs four checks in order:
  1. Does `X-Device-Id` exist in the `Device` table?
  2. Is `X-Timestamp` within 5 minutes of the current server time? (Prevents old captured requests from being replayed.)
  3. Does the computed HMAC-SHA256 match `X-Signature`?
  4. Has this exact request been seen before? (See Section 2 -- Replay Protection.)
- If any check fails, the request is rejected with HTTP 401 (Unauthorized).

**New config variables:**
- `AUTH_TIMESTAMP_TOLERANCE_SEC=300` -- how many seconds of clock drift to allow between the Zinnia Axion Agent and the backend (default: 5 minutes).

---

## Section 2: Replay Protection

**Goal:** Even with valid signatures, prevent an attacker from capturing a valid request and sending it again to inject duplicate data.

### Task 2.1 -- Add an in-memory cache of recent requests

**What:** Keep a fast lookup cache (LRU) of recently seen request fingerprints. Each fingerprint is the combination of `device_id`, `timestamp`, and a hash of the request body.

**Why:** If the same fingerprint appears twice within the time window, it's a replay -- either malicious or accidental.

**Where:** New file `backend/auth.py` (same file as the signature verifier)

**Details:**
- Use Python's `functools.lru_cache` or a dictionary with automatic expiry.
- The cache holds up to 10,000 entries (configurable).
- Entries older than the tolerance window (5 minutes) are automatically evicted.

### Task 2.2 -- Reject duplicate requests

**What:** Before processing a `POST /track` request, check the cache. If the fingerprint already exists, return HTTP 409 (Conflict) and do not store the data.

**Why:** Closes the replay attack vector. Even if an attacker captures a valid signed request, sending it again will be rejected.

**Where:** `backend/auth.py` (called from the signature verification flow in Task 1.6)

**New config variables:**
- `REPLAY_WINDOW_SEC=300` -- how long to remember request fingerprints (default: 5 minutes, should match `AUTH_TIMESTAMP_TOLERANCE_SEC`).
- `REPLAY_CACHE_SIZE=10000` -- maximum number of fingerprints to keep in memory.

---

## Section 3: Role-Based Access Control (RBAC) for Dashboards

**Goal:** Employees should only see their own dashboard. Admin pages (leaderboard, user detail, delete) should only be accessible to administrators.

### Task 3.1 -- Add an AdminUser table

**What:** Create a database table `AdminUser` that stores admin accounts with usernames and hashed passwords.

**Why:** The system needs to know who is an admin. Currently, anyone with the URL can access the admin dashboard.

**Where:** `backend/models.py`

**Details:**
- Columns: `id`, `username`, `password_hash`, `created_at`.
- A command-line script or first-run admin setup creates the initial admin account.

### Task 3.2 -- Add authentication to the backend

**What:** Create a login system that supports two modes: SSO (single sign-on) for enterprises with existing identity providers, and local login for standalone deployments.

**Why:** The backend needs to know WHO is making each request to enforce access rules.

**Where:** New file `backend/rbac.py`, changes to `backend/app.py`

**Details:**
- **SSO mode** (when `SSO_ENABLED=true`): The backend trusts the `X-Authenticated-User` header, which is set by the enterprise's reverse proxy (e.g., Okta, Azure AD). The backend checks if that user is in the `AdminUser` table to determine admin status.
- **Local mode** (when `SSO_ENABLED=false`): The backend provides a `/login` page. Admin users log in with username/password. A Flask session cookie tracks their login state.

### Task 3.3 -- Protect dashboard routes with access control

**What:** Add decorators to each route that check whether the current user has permission.

**Why:** This is where the access rules are actually enforced.

**Where:** `backend/app.py`, using decorators from `backend/rbac.py`

**Details:**
- `/dashboard/<user_id>` -- accessible to that specific user OR any admin. An employee visiting someone else's dashboard gets HTTP 403 (Forbidden).
- `/admin/leaderboard` -- admin only.
- `/admin/user/<user_id>/non-productive-apps` -- admin only.
- `DELETE /admin/user/<user_id>` -- admin only.
- `POST /track` -- device auth only (from Section 1), not affected by RBAC.
- `/health` -- public, no auth needed.

### Task 3.4 -- Update the Streamlit admin dashboard to handle authentication

**What:** Modify the admin dashboard to prompt for login credentials and pass them to the backend.

**Why:** The Streamlit dashboard makes API calls to the backend. Those calls need to include authentication so the backend can verify admin status.

**Where:** `frontend/admin_dashboard.py`

**Details:**
- Add a login form at the top of the dashboard.
- Store the session token in Streamlit's session state.
- Pass the token with every API request.

**New config variables:**
- `SSO_ENABLED=false` -- whether to trust the `X-Authenticated-User` header (default: false, use local login).
- `ADMIN_USERNAME=admin` -- default admin username for first setup.
- `ADMIN_PASSWORD=` -- default admin password (must be set on first deployment, no default).

---

## Section 4: Audit Logging

**Goal:** Keep a tamper-evident log of all security-relevant actions for compliance and incident investigation.

### Task 4.1 -- Add an AuditLog table

**What:** Create a database table that records every important action with who did it, when, and from where.

**Why:** Enterprise compliance (SOC 2, ISO 27001, etc.) requires audit trails. If something goes wrong, you need to be able to trace what happened.

**Where:** `backend/models.py`

**Details:**
- Columns: `id`, `timestamp`, `actor` (who performed the action -- admin username, device_id, or "system"), `action` (what happened -- e.g., "delete_user", "login_failed", "retention_cleanup"), `target_user` (which user was affected, if applicable), `ip_address` (requester's IP), `user_agent` (requester's browser/agent string).

### Task 4.2 -- Create a logging helper function

**What:** Write a simple `log_action()` function that inserts a row into the `AuditLog` table.

**Why:** A single helper makes it easy to add audit logging to any route with one line of code, keeping the codebase clean.

**Where:** New file `backend/audit.py`

**Details:**
- The function takes `actor`, `action`, `target_user` (optional), and automatically captures the timestamp, IP, and user agent from the Flask request context.

### Task 4.3 -- Instrument all security-relevant actions

**What:** Call `log_action()` at every point where something important happens.

**Why:** The audit log is only useful if it captures all relevant events.

**Where:** `backend/app.py`, `backend/auth.py`, `backend/rbac.py`

**Details:**
- Actions to log:
  - Admin deletes a user's data (`DELETE /admin/user/<user_id>`)
  - Someone accesses a dashboard (`GET /dashboard/<user_id>`)
  - Authentication failure (wrong signature, expired timestamp, invalid device)
  - Replay attempt detected
  - Admin login success / failure
  - Automatic data retention cleanup (scheduled purge of old events)

---

## Section 5: Data Minimization

**Goal:** Reduce the amount of sensitive personal information collected and stored. Window titles can contain email subjects, document names, private URLs, and internal project codes.

### Task 5.1 -- Change the default window title mode to "redacted"

**What:** Change the default value of `WINDOW_TITLE_MODE` from `full` to `redacted` everywhere.

**Why:** In "full" mode, titles like "RE: John's Salary Review - Outlook" or "secret-project-v2.docx - Word" are stored in plain text. The "redacted" mode only keeps classification keywords (e.g., "youtube", "zoom") and strips everything else.

**Where:** `tracker/agent.py` (line 70: change default from `"full"` to `"redacted"`), `installer/windows/setup_gui.py` (line 57: change `WINDOW_TITLE_MODE=full` to `WINDOW_TITLE_MODE=redacted`), `installer/mac/setup_gui.py` (same change), `.env.example`

### Task 5.2 -- Add regex-based scrubbing for sensitive patterns

**What:** Before any title is stored (even in "full" mode), scrub out patterns that are almost certainly sensitive.

**Why:** Even if an admin chooses "full" mode, email addresses and internal case IDs should not end up in the database.

**Where:** `tracker/agent.py` (inside the `_apply_title_mode()` function)

**Details:**
- Patterns to scrub (replace with `[REDACTED]`):
  - Email addresses: anything matching `name@domain.com`
  - Long numeric sequences: 8 or more consecutive digits (phone numbers, account numbers)
  - Internal case/policy IDs: patterns like `CA12345`, `POL67890`, `TKT-2024-001` (configurable regex)

### Task 5.3 -- Add a backend flag to drop titles entirely on ingestion

**What:** Add a `DROP_TITLES` flag to the backend that, when enabled, replaces all `window_title` values with an empty string before storing them in the database.

**Why:** Some enterprises may want productivity tracking but consider window titles too sensitive to store at all. This is a server-side enforcement -- even if a Zinnia Axion Agent sends titles, they are discarded.

**Where:** `backend/app.py` (in the `POST /track` handler, before inserting events into the database)

**New config variables:**
- `DROP_TITLES=false` -- if set to `true`, all window titles are discarded on the backend before storage (default: false).
- `TITLE_SCRUB_PATTERNS=` -- optional comma-separated list of additional regex patterns to scrub from titles (default: empty, uses built-in patterns only).

---

## Section 6: Rate Limiting and Input Validation

**Goal:** Protect the backend from abuse -- a compromised or buggy Zinnia Axion Agent sending massive payloads, malformed data, or flooding the API.

### Task 6.1 -- Add request size limits

**What:** Reject any request to `POST /track` that exceeds a configurable size limit.

**Why:** Without a size limit, a single malicious request could send gigabytes of data and crash the server or fill the database.

**Where:** `backend/app.py` (Flask's `MAX_CONTENT_LENGTH` setting)

**Details:**
- Default limit: 512 KB per request. A normal batch of 10 events is roughly 2-5 KB, so 512 KB is very generous while still preventing abuse.

### Task 6.2 -- Validate the JSON schema of incoming events

**What:** Before processing a `POST /track` request, validate that the JSON body matches the expected structure. Every event must have the required fields with the correct types.

**Why:** Malformed data can cause database errors, crash the productivity engine, or produce misleading dashboard results.

**Where:** `backend/app.py` (in the `POST /track` handler)

**Details:**
- Required fields per event: `timestamp` (string, ISO 8601), `app_name` (string), `keystroke_count` (integer >= 0), `mouse_clicks` (integer >= 0), `mouse_distance` (number >= 0), `idle_seconds` (number >= 0).
- Optional fields: `user_id` (string), `window_title` (string), `distraction_visible` (boolean).
- If validation fails, return HTTP 400 (Bad Request) with a description of what's wrong.
- Add `jsonschema` to `requirements.txt` for schema validation.

### Task 6.3 -- Add per-device rate limiting

**What:** Limit how many requests each device can make per minute. If a Zinnia Axion Agent is sending too fast, throttle it.

**Why:** A stuck or compromised Zinnia Axion Agent could flood the backend with thousands of requests per second. Rate limiting prevents one device from affecting the system for everyone.

**Where:** `backend/app.py` (using `flask-limiter` package)

**Details:**
- Default limit: 120 requests per minute per device (normal operation sends 6 requests/minute at a 10-second batch interval, so 120 is 20x headroom).
- The rate limit key is the `X-Device-Id` header (or IP address if device auth is not enabled).
- When rate-limited, return HTTP 429 (Too Many Requests) with a `Retry-After` header.

**New config variables:**
- `MAX_REQUEST_SIZE_KB=512` -- maximum request body size in kilobytes.
- `RATE_LIMIT_PER_DEVICE=120/minute` -- maximum requests per device per minute.

---

## Section 7: Demo Mode

**Goal:** Allow the system to run in two modes -- relaxed (for demos and prototyping) and strict (for production). Currently, the system has no authentication at all, which is fine for demos but unacceptable for enterprise use.

### Task 7.1 -- Add a DEMO_MODE toggle

**What:** Add a single `DEMO_MODE` environment variable that controls whether security features are enforced.

**Why:** During development and demos, you don't want to deal with device registration, signatures, and admin passwords. But in production, all of it must be enforced. A single toggle makes this easy.

**Where:** `backend/config.py`, `.env.example`

**Details:**
- `DEMO_MODE=true` (default for backward compatibility):
  - `POST /track` accepts data without any signature or device ID.
  - Dashboards are accessible without login.
  - The backend logs a warning on startup: "WARNING: Running in DEMO MODE. Authentication and access control are disabled."
  - All other features (productivity engine, retention, etc.) work normally.
- `DEMO_MODE=false` (production):
  - All security features from Sections 1-6 are enforced.
  - `POST /track` requires valid `X-Device-Id`, `X-Timestamp`, `X-Signature`.
  - Dashboards require authentication.
  - Admin endpoints require admin role.
  - Rate limiting and schema validation are active.

### Task 7.2 -- Add a startup security check

**What:** When the backend starts in production mode (`DEMO_MODE=false`), verify that critical security settings are configured. If not, refuse to start.

**Why:** Prevents accidentally running in production without setting an admin password, database URI, or other required config.

**Where:** `backend/app.py` (in the `create_app()` function)

**Details:**
- Checks to run on startup (only when `DEMO_MODE=false`):
  - `ADMIN_PASSWORD` must be set (not empty).
  - `DATABASE_URI` must not be the default SQLite path (production should use PostgreSQL).
  - `SECRET_KEY` must be set (used for Flask session signing).
- If any check fails, log an error and exit with a clear message explaining what's missing.

**New config variables:**
- `DEMO_MODE=true` -- master toggle for all security features (default: true for backward compatibility).
- `SECRET_KEY=` -- Flask secret key for session cookies (must be set in production, no default).

---

## Summary of All New Config Variables

| Variable | Default | Section | Purpose |
|----------|---------|---------|---------|
| `DEMO_MODE` | `true` | 7 | Master toggle: true = relaxed, false = enforce all security |
| `AUTH_TIMESTAMP_TOLERANCE_SEC` | `300` | 1 | Max clock drift allowed for request signatures (seconds) |
| `REPLAY_WINDOW_SEC` | `300` | 2 | How long to remember request fingerprints (seconds) |
| `REPLAY_CACHE_SIZE` | `10000` | 2 | Max entries in the replay detection cache |
| `SSO_ENABLED` | `false` | 3 | Trust X-Authenticated-User header from reverse proxy |
| `ADMIN_USERNAME` | `admin` | 3 | Default admin username |
| `ADMIN_PASSWORD` | *(none)* | 3 | Admin password (must be set in production) |
| `SECRET_KEY` | *(none)* | 7 | Flask session signing key (must be set in production) |
| `DROP_TITLES` | `false` | 5 | Discard all window titles on the backend before storage |
| `TITLE_SCRUB_PATTERNS` | *(empty)* | 5 | Additional regex patterns to scrub from window titles |
| `MAX_REQUEST_SIZE_KB` | `512` | 6 | Maximum request body size |
| `RATE_LIMIT_PER_DEVICE` | `120/minute` | 6 | Per-device rate limit for POST /track |

---

## Summary of New Files

| File | Section | Purpose |
|------|---------|---------|
| `backend/auth.py` | 1, 2 | Signature verification, replay detection |
| `backend/rbac.py` | 3 | Role-based access control decorators |
| `backend/audit.py` | 4 | Audit logging helper |

---

## Summary of Modified Files

| File | Sections | Changes |
|------|----------|---------|
| `backend/models.py` | 1, 3, 4 | Add Device, AdminUser, AuditLog tables |
| `backend/app.py` | 1, 2, 3, 4, 5, 6, 7 | Add middleware, route protection, validation, startup checks |
| `backend/config.py` | All | Add new config variables |
| `tracker/agent.py` | 1, 5 | Sign requests, change title default, add regex scrubbing |
| `installer/windows/setup_gui.py` | 1, 5 | Generate device credentials, secure storage, change title default |
| `installer/mac/setup_gui.py` | 1, 5 | Generate device credentials, secure storage, change title default |
| `frontend/admin_dashboard.py` | 3 | Add login form, pass auth tokens |
| `requirements.txt` | 6 | Add flask-limiter, jsonschema |
| `.env.example` | All | Document all new variables |
| `README.md` | All | Add production deployment notes |

---

## Implementation Order (Recommended)

1. **Section 7 (Demo Mode)** -- Implement first so all other sections can check `DEMO_MODE` before enforcing.
2. **Section 6 (Rate Limiting + Validation)** -- Low risk, immediate protection, no client changes needed.
3. **Section 5 (Data Minimization)** -- Config change + small code change, no new dependencies.
4. **Section 4 (Audit Logging)** -- New table + helper, no breaking changes.
5. **Section 1 (Authenticated Ingestion)** -- Biggest change; requires both backend and Zinnia Axion Agent updates, plus a new build of the `.exe`.
6. **Section 2 (Replay Protection)** -- Small addition on top of Section 1.
7. **Section 3 (RBAC)** -- Last because it affects how dashboards are accessed and requires admin account setup.
