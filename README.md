# Star CRM — Proof of Concept

A standalone, self-hosted version of the relationship board. The React
frontend reads and writes to a real backend (FastAPI + PostgreSQL) instead of
in-browser storage, so contacts and activity persist in a database (NEON)

The database is **PostgreSQL only**. The deploy target is **Google Cloud Run**
(one service serving both the API and the built site) with a managed
**Neon** Postgres database. Local development uses a Postgres container via
`docker-compose.yml`.

It is **multi-user via profiles, with no authentication**: each user is a named
profile that owns its own contacts. You switch profiles from a dropdown by the
logo - there is no login. The active profile is sent to the API in the
`X-User-Id` header. When SSO (Entra ID) is added later, only how the current
user is identified changes; the per-user data model stays the same. See the
security note at the bottom before putting it anywhere public.

## Architecture

```
frontend/   React + Vite (Bob's UI, wired to the API)
backend/    FastAPI + uvicorn, SQLAlchemy, PostgreSQL (psycopg v3)
```

- Frontend talks to the backend over a small REST API (`src/api.js`) using
  **same-origin relative paths** (`/api/...`). In dev, Vite proxies `/api` to the
  backend; in production the one Cloud Run service serves both, so no API URL is
  ever hard-coded.
- Backend persists to PostgreSQL. `DATABASE_URL` is required; the app refuses to
  start without it, so dev and production behave identically.
- First run auto-seeds the 26 contacts from the original board.

## Run it locally

You need Python 3.11+ (use 3.13 — see note), Node 18+, and Docker Desktop for the
local Postgres.

> **Python version:** build the venv on Python 3.13. Python 3.14 has no prebuilt
> wheels for the pinned deps yet and will try (and fail) to compile from source.
> On Windows: `py -3.13 -m venv .venv`.

### 1. Database (Docker)

From the project root:

```bash
docker compose up -d        # starts Postgres on localhost:5432, data in a volume
docker compose ps           # wait for star-pg to show "healthy"
```

### 2. Backend

```bash
cd backend
py -3.13 -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env              # the default points at the Docker Postgres above
uvicorn main:app --reload
```

Backend comes up at `http://localhost:8000`. Check `http://localhost:8000/docs`
for the auto-generated API explorer. It will not start until `DATABASE_URL` is
set (the `.env.example` default already points at the local Docker Postgres).

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite dev server proxies `/api` to the backend
on :8000, so you don't set any API URL. Add, edit, delete, log a touch, mark
actions done, and reset — all of it persists in Postgres.

> Don't run `npm audit fix --force` — it will upgrade Vite to a major version the
> React plugin doesn't support and break the install. Plain `npm audit fix` is
> safe; the dev-only advisories it leaves are not a concern for a local POC.

## The database: Neon (production)

The connection string format (psycopg v3 dialect) is:

```
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/star_crm?sslmode=require
```

Tables are created automatically on startup and the 26 seed contacts load on
first run. No manual migrations for the POC.

### Get a Neon database

1. Sign up at https://neon.tech (no credit card; commercial use allowed on the
   free tier). Create a project; a database is created for you.
2. In the project dashboard, copy the connection string. Neon gives you a
   pooled and a direct one — **use the pooled string** (host contains `-pooler`)
   for a serverless app like Cloud Run, so you don't exhaust connections.
3. Neon hands you a `postgresql://...` URL. Make two tweaks for this app:
   - change the scheme to `postgresql+psycopg://`
   - keep `?sslmode=require` (Neon requires SSL)

```
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@ep-xxxx-pooler.REGION.aws.neon.tech/star_crm?sslmode=require
```

> Free-tier Neon autosuspends after inactivity, so the first request after an
> idle period may take a second or two to wake. `pool_pre_ping` (already set in
> `database.py`) handles the stale-connection case.

### Local Postgres (already wired)

Local dev uses the Docker container from `docker-compose.yml`:

```
DATABASE_URL=postgresql+psycopg://star:password@localhost:5432/star_crm
```

## Hosting on Google Cloud Run (single service)

One Cloud Run service serves **both** the API and the built React site, so
there's one URL and one deploy. The root `Dockerfile` does a multi-stage build:
it compiles the frontend with Node, then copies the output into the FastAPI
image, which serves it from `/` while the API stays under `/api`.

Deploy from the **project root**:

```bash
gcloud run deploy star-crm \
  --source . \
  --region us-west1 \
  --allow-unauthenticated \
  --set-env-vars "^@@^DATABASE_URL=postgresql+psycopg://USER:PASSWORD@ep-xxxx-pooler.REGION.aws.neon.tech/star_crm?sslmode=require"
```

Notes:

- The `^@@^` prefix changes the env-var delimiter from comma to `@@`, because the
  Neon URL itself can contain characters that confuse the default comma parser.
  For anything beyond a demo, store the URL in Secret Manager and reference it
  with `--set-secrets` instead of putting it on the command line.
- Cloud Run's filesystem is ephemeral — that's exactly why the data lives in
  Neon, not in the container.
- The build needs no separate frontend host: the site is baked into the image
  and served by the same service.

To redeploy after changes, run the same command again. To see logs:
`gcloud run services logs read star-crm --region us-west1`.

## Business-card scanner

The "Scan card" button (upload, or camera on mobile) sends the photo to the
backend, which downscales it, runs it through **Claude vision** to extract name,
company, role, email, and phone, and returns those to **prefill the contact form
for review**. On save, the (downscaled JPEG) card image is stored with the
contact and shown on its detail page.

Setup: get an Anthropic API key from console.anthropic.com and set
`ANTHROPIC_API_KEY` in `backend/.env`. Without it, the scan endpoint returns a
clear 503 and the rest of the app is unaffected. Cost is a fraction of a cent
per card; the default model is a cheap Haiku (override with `CLAUDE_MODEL`).

In production, pass the key as a **secret**, not a plain env var:

```bash
# One-time: store the key in Secret Manager
echo -n "sk-ant-..." | gcloud secrets create anthropic-api-key --data-file=-

# Then on deploy, mount it:
gcloud run deploy star-crm --source . --region us-west1 --allow-unauthenticated \
  --set-env-vars "^@@^DATABASE_URL=postgresql+psycopg://USER:PASS@HOST/neondb?sslmode=require" \
  --set-secrets "ANTHROPIC_API_KEY=anthropic-api-key:latest"
```

## API reference

All `/api/contacts*` and `/api/reset` routes require an `X-User-Id` header
identifying the active profile, and operate only on that profile's data. The
`/api/users` routes do not.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness check |
| GET | `/api/users` | List profiles |
| POST | `/api/users` | Create a profile `{ "name": "..." }` (blank board) |
| PATCH | `/api/users/{id}` | Rename a profile |
| DELETE | `/api/users/{id}` | Delete a profile and all its contacts |
| GET | `/api/contacts` | List the active user's contacts (with logs) |
| POST | `/api/contacts` | Create a contact |
| GET | `/api/contacts/{id}` | Get one contact |
| PUT | `/api/contacts/{id}` | Update a contact |
| DELETE | `/api/contacts/{id}` | Delete a contact |
| POST | `/api/contacts/{id}/log` | Add a touch `{ "note": "..." }` |
| POST | `/api/contacts/{id}/complete` | Log the next action as done and clear it |
| POST | `/api/contacts/scan-card` | Upload a card photo (multipart `file`); returns prefill fields + a compact image to save |
| GET | `/api/contacts/{id}/card` | The stored card image (404 if none) |
| POST | `/api/reset` | Restore the demo set for the active user only |

## Claude connector (MCP) + Entra auth — in progress

Lets Claude read and write contacts on a user's own board ("add Jane Doe, GC at
Acme…"). It's a remote MCP server scoped to the authenticated Microsoft Entra
(M365) user.

What's scaffolded and tested:

- `auth.py` validates Entra Bearer tokens (signature/issuer/audience/expiry) and
  maps each identity to a profile, auto-creating one on first sign-in. Inactive
  unless `ENTRA_TENANT_ID` + `ENTRA_CLIENT_ID` are set — until then the app uses
  the `X-User-Id` header as before.
- `get_current_user` accepts an Entra Bearer token when configured, else falls
  back to the header, so the live app keeps working during rollout.
- `mcp_server.py` exposes `search_contacts`, `get_contact`, `create_contact`,
  `update_contact`, `delete_contact`, `log_touch`, scoped per user. Mounted at
  `/mcp` (guarded — a missing SDK or unconfigured Entra won't break the API).
- `users` table gains `microsoft_oid` + `email` (additive migration on startup).

To finish (needs the M365 tenant):

1. Register an Entra app (Directory/tenant ID, client ID, client secret, the
   App ID URI for the token audience). Set `ENTRA_TENANT_ID` / `ENTRA_CLIENT_ID`
   (and `ENTRA_AUDIENCE` if it's the `api://<client-id>` form).
2. Register a second Entra app (or reuse) for the Claude connector; its redirect
   URI is the callback Claude shows when adding the connector.
3. Finalize the MCP OAuth resource metadata in `build_mcp_app()` against the
   deployed `/mcp` URL, deploy, then in Claude (Team plan): **Org settings →
   Connectors → Add custom → Web**, enter the MCP URL + connector client
   ID/secret; members connect individually and sign in with M365.

## Security note (read before hosting publicly)

This POC has **no authentication**. Profiles are a convenience, **not a security
boundary**: anyone using the app can pick any profile, and anyone can hit the API
directly with any `X-User-Id`. The CORS allowlist limits browser origins but is
not real protection. That is acceptable for local use or a locked-down Cloud Run
instance behind IAM, but **do not expose it on the open internet as-is.**

The production plan (next phase): the frontend logs users in via Entra ID
(Microsoft 365) and sends the resulting JWT as a `Bearer` token on every
request. The backend gets a middleware that validates that token against Entra's
public keys (issuer, audience, expiry, signature) before any route runs, and
scopes every query to the authenticated user. The route handlers here are
structured so that middleware drops in without reworking them.
