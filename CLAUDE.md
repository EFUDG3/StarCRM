# Star CRM — project context for Claude

A relationship board / lightweight CRM for **Star Flooring & Remodeling** (San Diego).
Started as an in-browser artifact; now a real, deployable web app with a database,
business-card scanning, multi-user profiles, and (in progress) a Claude connector.

This file is the single source of truth for picking the project back up. Read it first.

> **Resume note (2026-07-06) — mid-migration, rebrand in progress.**
> - **Rebrand:** the project is becoming **"starbot"** (the future main Star site), not just a CRM. New resources use the `starbot` name.
> - **Hosting move (Google → Azure), in progress:** deployed to **Azure Container Apps** — resource group `starbot`, env `starbot-env`, region `westus3`, port `8080`, via `az containerapp up --source .`, still pointed at the **Neon** DB. Old **Google Cloud Run** service is still live production until cutover.
> - **Env-var gotcha that broke the first Azure run (likely still needs fixing):** `--env-vars` takes each `key=value` as its OWN space-separated, individually-quoted token. All pairs wrapped in ONE quoted string got swallowed into `DATABASE_URL` (`invalid sslmode value: "require ANTHROPIC_API_KEY=..."`), so the container crash-looped and the site timed out. Fix (also does Phase 2 = move secrets off plaintext):
>   `az containerapp secret set -n starbot -g starbot --secrets "database-url=<neon-url>" "anthropic-api-key=<key>"`
>   `az containerapp update -n starbot -g starbot --set-env-vars "DATABASE_URL=secretref:database-url" "ANTHROPIC_API_KEY=secretref:anthropic-api-key" "ENTRA_TENANT_ID=c80d56a1-72d7-4130-aab1-1150a1782ba9" "ENTRA_CLIENT_ID=066b737b-a053-4b53-af03-5cabc03fbf26" "ENTRA_AUDIENCE=api://066b737b-a053-4b53-af03-5cabc03fbf26"`
>   Confirm the site + `/api/health` load after the new revision before anything else.
> - **Connector is blocked on DNS.** It needs the app served at `starbot.starflooringandremodeling.com` (a verified subdomain) before Entra will accept the OAuth resource. Two records (CNAME `starbot` → the Container App FQDN, TXT `asuid.starbot` → the app's `customDomainVerificationId`) must be added at the site's DNS host — nameservers are `ns1/ns2.securedservers.info` (a VPS run by **DataNet or Greenman IT**, reached via the boss). GoDaddy only *registers* the domain; its DNS is elsewhere. Do NOT use GoDaddy Forwarding and do NOT switch nameservers.
> - **After DNS:** repoint App ID URI + scope + `ACCEPTED_AUDIENCES` + `MCP_RESOURCE_URL` to the custom domain, set Manifest `requestedAccessTokenVersion: 2`, redeploy, point the connector there. Heads-up: Entra's RFC 8707 support is finicky, so budget one more tuning pass (see "Connector OAuth" section + FastMCP's Azure integration guide).
> - **Confirmed via web research:** the verified-domain wall is **Microsoft Entra-specific**, not a Claude/MCP requirement (Entra identifier URIs must be a verified/initial tenant domain; other IdPs like Auth0 don't require it, and Entra's RFC 8707 support is finicky — see the "Connector OAuth" section).
> - **Two ways to test the connector WITHOUT waiting on the boss/DNS:** (a) register a **cheap domain you own** ($1-10, Cloudflare/Porkbun), verify it in Entra, point it at the app — tests the full OAuth flow today; or (b) deploy a **no-auth build** of the MCP server (drop `token_verifier`/`AuthSettings`) so Claude connects with no OAuth — fastest, but it's an OPEN endpoint on the LIVE Neon DB, so hard-scope it to the **ethan** test profile (NEVER Bob) and tear the revision down after.
> - Migration plan detail: open items #4 (domain) and #7 (DB to Azure Postgres). Connector fixes are in the working tree (mount, AS shim, `/mcp` redirect, resource override, `auth.py` audience list).
> - **SECURITY:** the live Neon password + Anthropic key were pasted into shell history and Azure logs repeatedly during setup — rotate BOTH before this reaches real users.

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
  --set-secrets "DATABASE_URL=DATABASE-URL:latest,ANTHROPIC_API_KEY=ANTHROPIC-API-KEY:latest"
```

- **Secrets via Secret Manager, never `--set-env-vars`** for anything with a credential.
- The Compute Engine default SA needs `roles/cloudbuild.builds.builder` (build) and
  `roles/secretmanager.secretAccessor` (read secrets) — granted once per project.
- Same service name/region/project ⇒ **same URL** across redeploys (new revision).
- Cloud Run scales to zero; first hit after idle is slow (cold start + Neon waking).
- **Secret names are `DATABASE-URL` and `ANTHROPIC-API-KEY`** (uppercase, hyphens) in this
  project; the lowercase forms don't exist and fail with "Secret ... was not found".
- **Test risky changes on a no-traffic preview first:** add `--no-traffic --tag connector`
  (plus the Entra + `MCP_RESOURCE_URL` env vars) to the deploy. It builds the new code at a
  separate URL (`https://connector---star-crm-931921353512.us-west1.run.app`) while the live
  URL keeps the current revision. Both the `…-931921353512.us-west1.run.app` and the newer
  `…-pcfkfoslca-uw.a.run.app` hosts resolve to the same service. Promote with
  `gcloud run services update-traffic star-crm --region us-west1 --to-latest`.
- **PowerShell line continuation is a backtick**, not `\` — multi-line `gcloud` commands
  pasted with `\` throw "Missing expression after unary operator". Use one line or backticks.
- **`az containerapp --env-vars` takes space-separated tokens**, each `key=value` its own
  quoted argument. Wrapping all pairs in one quoted string swallows them into the first
  var's value (this broke `DATABASE_URL`/`sslmode` and crash-looped the app). For
  credentials, prefer Container Apps **secrets** + `secretref:` over inline env vars.

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
   - ✅ **MCP mount fixed (code side).** The cause was NOT a version mismatch
     (`mcp==1.12.0` is correct) — it was `from __future__ import annotations` in
     `mcp_server.py`. PEP 563 stringized the tool param annotations, so FastMCP's
     `issubclass(param.annotation, Context)` received `"str"` and threw. Removed
     it (Py 3.13 evaluates the `X | None` syntax natively). Fixed two latent mount
     bugs that surfaced once it loaded: (a) set `streamable_http_path="/"` so
     mounting at `/mcp` yields `/mcp`, not `/mcp/mcp`; (b) wired the MCP
     session-manager lifespan into the FastAPI app in `main.py` (Starlette does
     not run a mounted sub-app's lifespan, so requests would 500 with "Task group
     is not initialized"). `build_mcp_app()` now constructs and registers all 6
     tools. Still TODO: set `ENTRA_TENANT_ID`/`ENTRA_CLIENT_ID` (+ `MCP_RESOURCE_URL`)
     in prod so the connector actually mounts, then a live `POST /mcp` smoke test.
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
   in us-west1). Needs a DNS record on the Star domain. **Also cleans up the connector
   OAuth:** once the MCP server is served at this verified-domain URL, set the Entra App
   ID URI to `https://crm.starflooringandremodeling.com/mcp`, point the connector +
   `MCP_RESOURCE_URL` at it, and delete the protected-resource `resource` override in
   `mcp_server.py` (that override exists only because the `run.app` preview host can't be
   a verified Entra App ID URI, so `resource=<run.app url>` fails AADSTS9010010). The
   AS-metadata shim and the `/mcp` trailing-slash redirect stay regardless.
5. Keep git current — commit working state frequently (see lesson below).
6. **Company-wide (shared) contacts** (do AFTER the connector is committed, to keep the
   diffs separate). Add a `visibility` column on `contacts` (`'personal'` default,
   `'company'`). Locked decisions: fully shared row; owner-only edit/delete/unshare,
   everyone else read-only with a "shared by <owner>" badge; notes are company-visible;
   follow-ups (`next_action`/`next_due`) stay the owner's and appear only on the owner's
   "Up Next". UI: one search bar plus an `All / Mine / Company` scope chip (default All)
   plus a Company badge; share toggle in the form, default off. Implementation:
   `list_contacts` returns `user_id == me OR visibility == 'company'`; GET uses an
   owner-or-company lookup, but PUT/DELETE/log/complete keep the strict owner-only
   `_get_or_404`; mirror in `mcp_server.py` (op_search/op_get include shared,
   op_update/op_delete owner-only); add `visibility`/`ownerName`/`mine` to `serialize()`
   and `visibility` to `ContactIn`.
7. **Move the DB off Neon for storage headroom.** Neon free tier is ~0.5 GB; the card-image
   BYTEA blobs are the growth driver as this goes company-wide. Two options, very different
   lifts:
   - **Azure Database for PostgreSQL (Flexible Server)**: real Postgres, so near-zero code
     change (swap `DATABASE_URL`, keep `postgresql+psycopg://` and `?sslmode=require`).
     `database.py` already anticipates this. Preferred for a small lift.
   - **Azure SQL Database** (the free 32 GB offer): this is SQL *Server*, not Postgres, so a
     bigger lift: new driver (pyodbc + the ODBC driver in the Dockerfile), `mssql+pyodbc://`
     dialect, and the Postgres-specific `_ensure_schema` SQL (`BYTEA`, `ADD COLUMN IF NOT
     EXISTS`, `CREATE UNIQUE INDEX IF NOT EXISTS`) would all need rewriting.
   - Either way, the real storage win is to stop storing card JPEGs in the DB (move them to
     object storage and keep only a key, or drop the scan-image feature).

## Connector OAuth: status & the run.app blocker (IMPORTANT)

The Claude connector's OAuth is fully working EXCEPT the final step, and the blocker is
now definitively understood:

- **Working end-to-end:** discovery → Microsoft login → scope `api://<client-id>/access_as_user`
  → PKCE S256 → Entra issues the auth code. What we added to get here, all still needed:
  - `mcp_server.py`: AS-metadata shim advertised via `issuer_url = <origin>` (Entra omits an
    RFC 8414 doc and never advertises `code_challenge_methods_supported`, so Claude couldn't
    discover it); `main.py` serves that doc at `/.well-known/oauth-authorization-server`
    (+ `openid-configuration`) pointing at Microsoft's real authorize/token endpoints with
    `code_challenge_methods_supported: ["S256"]`.
  - `main.py`: `/mcp` → `/mcp/` 307 redirect (the mount serves at `/mcp/`; a bare `/mcp`
    otherwise 405s).
  - `main.py` SPA catch-all returns 404 (not index.html) for `/.well-known/*`, `/authorize`,
    `/token`, etc. — else Claude mistook the React app for an auth server.
- **The wall:** token exchange fails `AADSTS9010010 / invalid_target`. Claude sends
  `resource=<the MCP server URL>` (RFC 8707), and Entra only accepts a resource that is a
  registered **Application ID URI**. The deployed URL is `*.run.app` (Google's domain), and
  this tenant **enforces** "IdentifierUris must use a verified domain of the organization or
  its subdomain" — confirmed by trying to add the run.app URL to `identifierUris` and getting
  that exact error. So run.app **cannot** be registered. NOTE: the `resource` Claude sends is
  the URL it connects to, NOT the metadata `resource` field (proven: overriding that field
  did nothing), so there is no server-side workaround.
- **Therefore the connector REQUIRES the MCP server at a verified-domain URL**, i.e.
  `https://crm.starflooringandremodeling.com/mcp` (subdomain of the verified
  `starflooringandremodeling.com`). This is roadmap item #4 and is a hard prerequisite, not a
  cleanup. Same Google Cloud hosting — just the custom domain in front. Then: register that
  URL as the App ID URI, set the scope to `https://crm.../mcp/access_as_user`, add that
  audience to `auth.ACCEPTED_AUDIENCES`, set `MCP_RESOURCE_URL` + the connector URL to it, and
  delete the protected-resource `resource` override in `mcp_server.py` (it was a no-op anyway).
- The `connector---…run.app` host is just the `--tag connector --no-traffic` preview; the
  stable host is `star-crm-931921353512.us-west1.run.app`. Neither is registrable (both run.app).
- Also still required regardless: set the app **Manifest `requestedAccessTokenVersion: 2`** or
  Entra issues v1 tokens whose issuer `auth.py` rejects.

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

---

## Handoff / account ownership

This app is meant to outlive its original developer. If you are picking it up cold, this is
who owns what and what to check first.

**Accounts & where things live**
- **Source:** GitHub `EFUDG3/StarCRM` (currently a personal account; TRANSFER to a Star
  GitHub org before handoff).
- **Hosting:** Google Cloud Run, project `starcrm-500221` (number 931921353512), region
  us-west1. Add a second company Owner; put billing on a company card.
- **Database:** Neon (personal account today). Planned move to Azure under the Star
  subscription (open item #7); do this before handoff to cut the personal tie.
- **Card scanner:** Anthropic API key, stored as the `ANTHROPIC-API-KEY` secret. Confirm it
  bills to a company Anthropic account, not a personal one.
- **Identity:** Microsoft Entra, Star Flooring tenant `c80d56a1-…`, app registration
  `066b737b-…`. Already in the company directory; add a second app **Owner** so it is not
  tied to one person.
- **Claude connector:** registered in the Star Team/Enterprise org. Ensure a second org
  admin exists.
- **Domain:** crm.starflooringandremodeling.com (planned), company-owned.

**The thing most likely to break unattended: the Entra client secret.** Entra client
secrets EXPIRE. When the connector's secret expires, Claude can no longer sign in and the
connector silently stops (the website is unaffected). Use the longest expiry (or a
certificate) and record it here:
- Client secret expires: __________  (set at creation; update on each rotation)
- Rotate: Entra → app `066b737b-…` → Certificates & secrets → new client secret → copy the
  **Value** → paste into the Claude connector settings.

**If it breaks while idle (the call-the-original-dev list)**
- Connector stops authenticating: most likely the Entra secret expired (above).
- Card scan returns 503: `ANTHROPIC_API_KEY` missing/disabled; the rest of the app is fine.
- First request after idle is slow: Cloud Run cold start + DB waking. Normal.
- `/api/contacts` 500s: check that a column did not get dropped from `models.py` (see Gotchas).

**Idle cost:** Cloud Run scales to zero and Neon/Azure free tiers auto-pause, so it costs
near nothing sitting unused.
