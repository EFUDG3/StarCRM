# Star CRM — project context for Claude

A relationship board / lightweight CRM for **Star Flooring & Remodeling** (San Diego).
Started as an in-browser artifact; now a real, deployable web app with a database,
business-card scanning, multi-user profiles, and (in progress) a Claude connector.

This file is the single source of truth for picking the project back up. Read it first.

---

## Stack

- **Frontend:** React + Vite + Tailwind. Single-page UI in `frontend/src/App.jsx`,
  API client in `frontend/src/api.js`. No router — views are state-driven.
- **Backend:** FastAPI + SQLAlchemy 2 + psycopg v3. **PostgreSQL only** (no SQLite).
- **DB:** Neon (managed Postgres) in production; Docker Postgres locally
  (`docker-compose.yml`). Standard Postgres — no vendor lock-in on the data layer.
- **Hosting:** **one** Google Cloud Run service serves BOTH the API and the built
  frontend (root `Dockerfile`, multi-stage: Node builds the site → FastAPI serves it
  from `./static`). One URL, one deploy.
- **AI:** Anthropic API (Claude vision) for business-card extraction; a remote MCP
  server (`backend/mcp_server.py`) as the Claude custom connector.
- **Auth:** currently **none** — "users" are profiles selected client-side via an
  `X-User-Id` header. Microsoft Entra (M365) SSO is scaffolded but not the live login yet.

**Use Python 3.13.** Not 3.14 — pydantic-core/pillow have no 3.14 wheels and pip will
try (and fail) to compile from Rust. Build the venv with `py -3.13 -m venv .venv`.

---

## Repo layout

```
Dockerfile            root, multi-stage: build frontend → serve from FastAPI (Cloud Run)
docker-compose.yml    local Postgres
backend/
  main.py             FastAPI app: routes, schema migration, static serving, /mcp mount
  models.py           SQLAlchemy: User, Contact, Interaction
  schemas.py          Pydantic request models
  database.py         engine/session; requires DATABASE_URL (postgresql+psycopg://)
  seed.py             the 26 demo contacts
  cards.py            image downscale + Claude vision extraction (card scan)
  auth.py             Entra JWT validation + identity→profile mapping (optional/scaffold)
  mcp_server.py       MCP connector: CRUD tools + Entra TokenVerifier (scaffold)
  .env                local secrets (gitignored)
  .env.example        documented template
frontend/
  src/App.jsx         the whole UI
  src/api.js          fetch client (sends X-User-Id; scanCard; card blob fetch)
  vite.config.js      dev server + /api proxy to :8000
```

---

## Data model

- **User** = a profile (id, name). Optional `microsoft_oid` + `email` once linked to
  an M365 identity. Owns contacts. No password.
- **Contact** — scoped to a user (`user_id`). Fields: name, company, role, email, phone,
  `category`, `category_label` (free text when category="other"), next_action, next_due
  (ISO date string), notes, `card_image`/`card_image_type` (scanned card JPEG), created_at.
- **Interaction** — activity-log "touch" on a contact (date, note), cascade-deleted.
- **Categories:** `bd, gc, vendor, property, client, sub, designer, insurance, other`.
  Each is a colored, filterable block; "other" reveals a free-text label field.
- Schema migrations are **additive** in `main.py:_ensure_schema()` via
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (Postgres). The only destructive path is
  dropping/recreating if a pre-multi-user `contacts` table (no `user_id`) is detected.
  On an empty DB, "Bob Bendixen" is created and seeded with the 26 demo contacts;
  new profiles start blank.

---

## Run locally

```bash
# 1. Database
docker compose up -d                       # Postgres on localhost:5432

# 2. Backend  (Python 3.13!)
cd backend
py -3.13 -m venv .venv
.venv\Scripts\activate                     # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                        # then set DATABASE_URL (+ keys)
uvicorn main:app --reload                   # http://localhost:8000  (/docs for API)

# 3. Frontend
cd frontend
npm install                                 # do NOT run `npm audit fix --force`
npm run dev                                 # http://localhost:5173 (proxies /api → :8000)
```

The frontend uses **same-origin relative `/api` paths**; the Vite proxy handles dev, and
the single Cloud Run service handles prod, so no API URL is hard-coded.

---

## Environment variables (`backend/.env`; Secret Manager in prod)

| Var | Purpose |
|---|---|
| `DATABASE_URL` | **Required.** Must use `postgresql+psycopg://` (a bare `postgresql://` makes SQLAlchemy seek psycopg2, which isn't installed). Neon: use the **pooled** host + `?sslmode=require`. |
| `ANTHROPIC_API_KEY` | Card scanner (Claude vision). Without it, scan returns a clean 503; rest of app unaffected. |
| `CLAUDE_MODEL` | Optional; defaults to a cheap Haiku. |
| `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID` | Turn on Entra token acceptance. `ENTRA_AUDIENCE` = `api://<client-id>`. Unset → app uses the `X-User-Id` fallback. |
| `MCP_RESOURCE_URL` | Set to the deployed `/mcp` URL when hosting the connector. |
| `FRONTEND_ORIGINS` | Optional CORS allowlist (not needed for the single-service deploy). |

---

## Deploy (Cloud Run + Neon)

Project: GCP `starcrm-500221` (region `us-west1`). DB: Neon (pooled string).
Deploy from the repo root; `--source .` builds the root `Dockerfile` via Cloud Build.

```bash
gcloud run deploy star-crm --source . --region us-west1 --allow-unauthenticated \
  --project starcrm-500221 \
  --set-secrets "DATABASE_URL=database-url:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest"
```

- **Secrets via Secret Manager, never `--set-env-vars`** for anything with a credential.
- The Compute Engine default SA needs `roles/cloudbuild.builds.builder` (build) and
  `roles/secretmanager.secretAccessor` (read secrets) — granted once per project.
- Same service name/region/project ⇒ **same URL** across redeploys (new revision).
- Cloud Run scales to zero; first hit after idle is slow (cold start + Neon waking).

---

## Current state (what works)

- ✅ Multi-profile board: switch/add/rename/delete profiles (dropdown by the logo),
  per-profile contacts, search, category filters, "Up Next" rail.
- ✅ Business-card scan → Claude vision → review form → contact (image stored, shown
  on detail). Clear error banner on failure (busy/timeout/unreadable), 30s timeout.
- ✅ Brand styling (red `#922525` / black / white + muted category tones, ★ on title),
  mobile-responsive header + truncation, auto "Added <date>", labeled form fields,
  Back button closes overlays, stable scrollbar gutter.
- ✅ Deployed on Cloud Run + Neon.
- ✅ Entra auth **scaffolded** (`auth.py`): `get_current_user` accepts an Entra Bearer
  token when configured, else falls back to `X-User-Id` — so enabling Entra doesn't
  break the current app. Verified: token validation, identity auto-provision, per-user
  scoping.
- ✅ MCP connector **scaffolded** (`mcp_server.py`): CRUD tools, per-user scoping,
  conversational tool descriptions. `op_*` logic tested.

---

## Open items / next steps (priority order)

1. **Rotate the exposed secrets** — the Anthropic API key, Neon DB password, and Entra
   client secret were all pasted in chat at some point. Reissue each and update
   `.env` / Secret Manager. (Identifiers like client/tenant IDs are fine.)
2. **Finish the Claude connector (M365, full CRUD, conversational):**
   - Fix the `MCP connector not mounted: issubclass() arg 1 must be a class` log — the
     installed `mcp` version differs from what `build_mcp_app()` was wired against
     (verified working on `mcp==1.12.0`). Pin/verify the SDK version and the
     TokenVerifier/AuthSettings call.
   - In Entra: **Expose an API → add scope** (e.g. `access_as_user`); set the app to
     issue **v2 tokens** (`requestedAccessTokenVersion: 2`); add the Claude connector
     **redirect URI** (shown when adding the connector).
   - Deploy with `MCP_RESOURCE_URL` = the live `/mcp` URL. On Team plan: Org settings →
     Connectors → Add custom → Web → MCP URL + connector client ID/secret; members
     connect and sign in with M365. Then test "add a contact" via Claude.
3. **Web SSO login** — replace the profile dropdown with M365 sign-in, reusing
   `auth.py`'s identity→profile mapping. (Same Entra plumbing as the connector.)
4. **Custom domain** — `crm.starflooringandremodeling.com` via **Firebase Hosting in
   front of Cloud Run** (free, managed SSL; Cloud Run domain mapping is preview and not
   in us-west1). Needs a DNS record on the Star domain.
5. Keep git current — commit working state frequently (see lesson below).

---

## Gotchas & lessons

- **Python 3.13, always.** 3.14 has no wheels for pydantic-core/pillow yet → Rust build
  failure. Activate the venv before `pip` (otherwise pip uses global 3.14).
- **`DATABASE_URL` scheme** must be `postgresql+psycopg://`, not `postgresql://`.
- **Secrets:** local in `.env` (gitignored); prod in Secret Manager. Never paste a
  credential into a terminal command or chat — rotate it if you do.
- **npm:** never `npm audit fix --force` — it bumped Vite to a major the React plugin
  didn't support and broke the install. Vite is pinned `^5.4.20`.
- **FastAPI** was bumped `0.115.6 → 0.138.1` so it coexists with the MCP SDK's newer
  Starlette (`mcp==1.12.0` + `starlette 1.3.x`).
- **Neon free tier autosuspends** — first request after idle is a few seconds. Use the
  **pooled** connection string in prod (Cloud Run scales to multiple instances).
- **Commit often.** A `category_label` column definition got silently dropped from
  `models.py` (likely a revert/undo), 500ing `/api/contacts` until re-added. Frequent
  commits + diffs prevent this.
