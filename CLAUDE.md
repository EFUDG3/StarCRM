# Star CRM — project context for Claude

A relationship board / lightweight CRM for **Star Flooring & Remodeling** (San Diego).
Started as an in-browser artifact; now a real, deployable web app with a database,
business-card scanning, multi-user profiles, and (in progress) a Claude connector.

This file is the single source of truth for picking the project back up. Read it first.

> **Mileage tab (2026-07-07).** Port of the standalone mileage-tracker-v2 into a tab:
> `backend/mileage.py` + `frontend/src/Mileage.jsx` (`#mileage`). **Azure Maps** (account
> `starbot-maps`, RG `starbot`, location `global`, Gen2) does geocoding + routing
> server-side; key = container secret `azure-maps-key` / env `AZURE_MAPS_KEY` (also in
> local `.env`). One multi-stop trip = ONE route transaction. Tables: `places` (per-user
> address book, cached lat/lon so repeat sites never re-geocode; sources
> manual/calendar/trip) and `trips` (legs JSON + total). Saving a trip upserts its stops
> as places (office start excluded). "Scan events for addresses" = Graph calendarView
> (location field + text body) → Haiku (`SCAN_MODEL`) extracts CA street addresses →
> geocode-validated → saved (~1¢/scan; Graph+Maps cost no Claude credits). **Rate is
> per-entry:** chosen in the trip form (default `MILEAGE_RATE` env, 0.70), STORED on each
> trip (`trips.rate`) — a special-case rate never re-prices other entries. Date
> auto-stamps at save. **Log = flat rows, one per save, NO day grouping and NO grand
> total** (back-to-back saves can be different workers → rollups are meaningless); each
> row expands (chevron) to show start + every destination w/ per-leg miles. Decisions
> (Ethan): Azure Maps over MapQuest; places per-user only; clipboard export incl. Stops
> + Rate columns; own calendar only.

> **Resume note (2026-07-15, LATEST) — connector server-side COMPLETE; only the claude.ai add remains.**
> - **Entra:** App ID URI `https://starbot.starflooringandremodeling.com/mcp` ADDED
>   (kept `api://<client-id>` too — Entra accepted it, verified-domain wall cleared).
>   Redirect URI `https://claude.com/api/mcp/auth_callback` added alongside the
>   claude.ai one. tokenVersion was already 2.
> - **Code (commit 6b86d49):** `required_scopes`, the PRM override (`resource` +
>   `scopes_supported`), the token-verifier scope list, and the AS-shim
>   `scopes_supported` all derive from `MCP_RESOURCE_URL` now — full scope is
>   `<MCP_RESOURCE_URL>/access_as_user`. No more hardcoded `api://` scope forms.
> - **Deployed:** revision `starbot--0000019`, image `starbot:mcp-custom-domain`,
>   `ENTRA_AUDIENCE` = `MCP_RESOURCE_URL` = `https://starbot.starflooringandremodeling.com/mcp`.
>   Verified live: PRM resource/scope on the custom domain; AS shim → Microsoft
>   endpoints + S256; unauth POST /mcp/ → 401 with correct `resource_metadata`;
>   web app unaffected (root 200).
> - **Connector added in claude.ai, first connect FAILED post-login, root cause
>   FIXED (2026-07-15):** the tenant had no delegated consent grant for the app's
>   OWN `access_as_user` scope — only the Graph scopes were admin-consented, and
>   user consent is restricted, so Entra bounced the flow back to Claude
>   ("check your credentials and permissions", trace ofid_c389235781dc28be).
>   Fixed by creating an AllPrincipals `oauth2PermissionGrant` via Graph POST
>   (starbot SP `d8d887fe` as both clientId and resourceId, scope
>   `access_as_user`) and adding the self-API permission to
>   requiredResourceAccess for portal visibility. Everything else verified good:
>   authorize request validates for all scope/resource permutations (probed
>   unauthenticated), token endpoint resolves the custom-domain resource, new
>   connector secret is valid (and now in local `.env`; v2 tokens stamp aud =
>   client GUID, which ACCEPTED_AUDIENCES covers). **Retry the connector connect
>   in claude.ai — consent was the only missing piece found.**
> - **Diagnostic recipe (worked well, reuse):** (1) probe `/authorize`
>   unauthenticated — request-level AADSTS errors return pre-login as redirects;
>   (2) `client_credentials` grant with `<App ID URI>/.default` — tests secret
>   validity AND token-endpoint resource resolution without a user; (3) Graph
>   `oauth2PermissionGrants?$filter=clientId eq '<sp-id>'` — consent state;
>   (4) Entra interactive sign-in logs show NOTHING for pre-login and
>   consent-stage failures — absence of a failed entry is itself a clue.
> - **Gotchas:** `az acr build` log streaming crashes on `✓` under cp1252 — set
>   `PYTHONUTF8=1`; the build succeeds server-side anyway (`az acr task list-runs`).
>   Local `backend/.env` still holds the dead secret — replace it if local M365
>   login is ever needed.
>
> **Resume note (2026-07-09) — custom domain live, sign-in secret fixed, UI polish shipped, DB decision made.**
> - **Custom domain LIVE:** DNS records added by Greenman/DataNet (cPanel) 2026-07-09;
>   `az containerapp hostname add` + `bind` done — managed cert
>   `mc-starbot-env-starbot-starfloo-9216` (SniEnabled) on env `starbot-env`;
>   `PUBLIC_BASE_URL=https://starbot.starflooringandremodeling.com`; Entra redirect URI
>   for the custom-domain callback added. Login round-trip verified. **Later same day the
>   ENTIRE domain zone went down** (DNS host outage — affects the company site too;
>   DataNet is on it). The azurecontainerapps.io URL always works; custom domain resumes
>   automatically when their DNS returns. Nothing to redo on our side.
> - **Sign-in was broken for fresh logins (AADSTS7000215), now fixed:** the 7/7 rotation
>   deleted the WRONG Entra secret (`starbot-chat` gone; `OauthForStarCRM` survived).
>   Existing session cookies masked it. New secret `starbot-login-2026-07` (expires
>   2028-07-09) created + stored as container secret `entra-client-secret` (value never
>   in chat/logs — Entra→shell var→secret store; verified via 3-char hint `9au` + length).
>   TODO: sync new value to GCP Secret Manager if Cloud Run legacy stays, or retire it.
> - **Connector is UNBLOCKED** (verified-domain requirement satisfied). Remaining:
>   Entra App ID URI → custom-domain form, scope, `ENTRA_AUDIENCE`/ACCEPTED_AUDIENCES,
>   `MCP_RESOURCE_URL=https://starbot.starflooringandremodeling.com/mcp`, redeploy,
>   add connector in claude.ai. Budget a tuning pass (Entra RFC 8707 finicky).
> - **DB decision (settled after reviewing Azure SQL free tier etc.):** Azure Database for
>   PostgreSQL Flexible Server, **B1ms / 32 GiB / westus3 / RG starbot / Postgres auth
>   only / public access + allow-Azure-services**, on **StarSubscription** (not the trial
>   account — credits don't transfer; portal list price **$20.48/mo** = $16.06 compute +
>   $4.42 storage, and that IS the definitive number for the boss). Azure SQL free tier
>   rejected: 100k vCore-sec ≈ 55 online-hrs/mo → dead-or-$30/mo for an all-day app, plus
>   dialect port. Migration order matters: create server → pg_dump/restore from Neon →
>   THEN swap `database-url` secret (app seeds Bob on an empty DB if pointed first).
>   Keep Neon a week as rollback. Password: no `&`/`@` chars.
> - **Claude credits:** buying ~$25-50 at console.anthropic.com moves the org from legacy
>   free limits (the 1-chat-per-use problem) to Start tier: Haiku 2M ITPM / 400k OTPM,
>   cached reads don't count toward ITPM. Ethan asking Salam. Cost email drafted
>   (~$40-75/mo company-wide on Haiku; DB is the main fixed cost at ~$20.50).
> - **UI shipped (revisions 14→18, all committed + PUSHED to GitHub):** board page shares
>   the wide container (no width jump between tabs); tab group is an inset segmented
>   control, shrink-proof (`shrink-0` + nowrap — long profile names truncate instead of
>   squishing buttons); title block fixed `sm:w-60` so tabs never move between views;
>   Up-Next rail compacted to match contact-row sizing + collapsible (localStorage);
>   mileage Places list sorts newest-first (stack) so calendar-scan results land on top.
> - **Commit style: NO Co-Authored-By Claude trailers** (Ethan's rule; history was
>   filter-branch'd to strip them before the 2026-07-09 push).
> - **az CLI note:** `az` is not on the harness shell PATH — use
>   `& "C:\Program Files\Microsoft SDKs\Azure\CLI2\python.exe" -IBm azure.cli ...`
>   (also the safe path for secret values; avoids az.cmd `&` truncation).

> **Resume note (2026-07-07) — starbot LIVE on Azure: 4 tabs, company-gated, secrets rotated.** (Older — DNS/connector bullets below are superseded by the 07-09 note above.)
> - **What starbot is:** Star's company-wide internal AI assistant (Copilot-style chat over
>   each user's OWN email/calendar/SharePoint via per-user M365 OAuth). Owner (Salam)
>   requirements from the cancelled Data Net project: (1) query email/OneDrive/SharePoint/
>   docs, (2) review emails + draft replies, (3) organize/flag action items, (4) to-do
>   lists + reminders; later: persistent context workspaces, doc-gen from templates.
>   On-demand only — NO background processing.
> - **Live:** https://starbot.ashymoss-2b719eff.westus3.azurecontainerapps.io — Container
>   App `starbot`, RG `starbot`, westus3. Deploy = `az acr build --registry cadd18599bd0acr
>   --image starbot:TAG .` then `az containerapp update -n starbot -g starbot --image
>   cadd18599bd0acr.azurecr.io/starbot:TAG`. Old **Cloud Run star-crm kept alive** for now
>   (its GCP secrets were synced to the rotated values; retire when everyone's on Azure).
> - **Four tabs, one SPA** (App.jsx swaps components): **Starbot** chat (`#starbot`,
>   Chat.jsx — SSE agent loop, 12 tools incl. email/calendar/files/todos/CRM, citations
>   with webLinks), **Tasks** (`#tasks`, Tasks.jsx — kanban todo/in_progress/done +
>   priority flags, drag-and-drop; same `todos` table as the chat rail), **Mileage**
>   (`#mileage`, block above), **Board** (the CRM).
> - **Company gate:** Microsoft sign-in required for EVERYTHING (full-page landing).
>   `get_current_user` in main.py has three doors: Entra Bearer (Claude connector),
>   session cookie (browser; X-User-Id then only picks which profile to view), bare
>   X-User-Id ONLY when Entra env is unset (local dev). First sign-in links to an existing
>   unlinked profile **by email** (auth.user_from_claims) — Bob/Salam pre-mapped to
>   bob@/salam@starflooringandremodeling.com; Ethan's OAuth duplicate was merged into the
>   original profile `u2886afa72cb7`. Users land on their OWN board, not Bob's.
> - **Chat runs on Haiku** (`CHAT_MODEL=claude-haiku-4-5` env var; code default sonnet-4-6)
>   with prompt caching (cache_control on system block + last message; cached reads don't
>   count toward rate limits), history trim (~30 msgs), read_email caps (4k chars, max
>   3/round), friendly 429 messages. **Org rate limits are LOW** (legacy tier, ~10k
>   ITPM Sonnet, ~$4.50 credit): the fix is buying credit / requesting an increase at
>   console.anthropic.com/settings/limits. Watch for it during demos; new-conversation
>   button helps (smaller cache writes).
> - **Secrets ALL ROTATED 2026-07-07** (Neon pw, Anthropic key, Entra client secret,
>   SESSION_SECRET; Azure Maps key + ACR registry secret didn't need it). Old Entra secret
>   `OauthForStarCRM` deleted by Ethan. GCP Secret Manager got matching versions. See
>   Gotchas for the `&`-truncation trap that briefly took prod down during rotation.
>   Handling rule stands: secrets move az→variable→secret store or portal-paste, never
>   chat/inline; verify stored length after every write.
> - **Entra app `066b737b`:** redirect URIs for azurecontainerapps.io + localhost:8000
>   callbacks (claude.ai + run.app ones kept); delegated Graph scopes Mail.Read,
>   Calendars.Read, Sites.Read.All, Files.Read.All (+User.Read), tenant admin consent
>   granted; `requestedAccessTokenVersion: 2`. Container env vars all use `secretref:`.
> - **DNS records SENT to the DNS host (pending confirmation):** CNAME `starbot` →
>   `starbot.ashymoss-2b719eff.westus3.azurecontainerapps.io` and TXT `asuid.starbot` →
>   `158A2943A7A8ADBF8176E7CE25EDB042C36FD9D301A2A74E9B48E4F7730A2A82`. When they resolve:
>   `az containerapp hostname add` + `bind` (free managed cert), add
>   `https://starbot.starflooringandremodeling.com/api/auth/callback` to Entra redirect
>   URIs, update `PUBLIC_BASE_URL`, then the connector wiring below.
> - **Next up (Ethan's priority order):** email-drafting polish → persistent context
>   workspaces (Salam) → back to Sonnet + a Haiku screener tool for deep email search →
>   DB move to Azure Postgres → semantic mail index (phase 3, after DB move).
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
- **Hosting:** **Azure Container Apps** is production (app `starbot`, RG `starbot`,
  westus3; one container serves API + built frontend — root `Dockerfile`, multi-stage:
  Node builds the site → FastAPI serves it from `./static`). The old Google Cloud Run
  service (`star-crm`, project `starcrm-500221`) is still up as legacy until retirement.
- **AI:** Anthropic API — starbot chat agent loop (`backend/chat.py`, Haiku via
  `CHAT_MODEL`), calendar address extraction (`backend/mileage.py`), business-card
  extraction (`backend/cards.py`); plus a remote MCP server (`backend/mcp_server.py`)
  as the Claude custom connector (blocked on DNS). **Azure Maps** for mileage
  geocoding/routing.
- **Auth:** **Microsoft sign-in required site-wide** (server-side OAuth in
  `backend/m365.py`, signed-cookie sessions, per-user Graph token cache). The X-User-Id
  header now only selects which CRM profile a signed-in user views; it is an auth door
  only in local dev when Entra env vars are unset.

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

- **Secrets rotated 2026-07-07** (Neon password, Anthropic key, Entra client secret,
  SESSION_SECRET). GCP Secret Manager got matching new versions so old Cloud Run heals
  on cold start. Azure Maps key + the ACR registry secret needed no rotation.
- **`&` in secret values truncates through az on Windows.** `az.cmd` re-parses args via
  cmd.exe, so a connection string containing `&channel_binding=require` gets chopped at
  the `&` and the secret stores silently truncated (app then crash-loops on boot). Fix:
  invoke the CLI's Python directly — `& "C:\Program Files\Microsoft SDKs\Azure\CLI2\python.exe"
  -IBm azure.cli containerapp secret set ...` — which skips cmd parsing entirely. After
  ANY secret write, compare stored vs intended length before restarting.

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
secrets EXPIRE. When a secret expires, the thing using it silently stops working (the
CRM board is unaffected). Use the longest expiry (or a certificate) and record it here:
- `starbot-login-2026-07` (M365 sign-in, all tabs) expires: **2028-07-09**. Created
  2026-07-09 after discovering the 7/7-rotation secret (`starbot-chat`) had been
  DELETED from Entra (the wrong secret was removed during rotation cleanup — the
  intended-for-deletion `OauthForStarCRM` survived). Symptom was AADSTS7000215 on
  every fresh sign-in; existing session cookies masked it on the old URL.
- `OauthForStarCRM` (Claude connector) expires: **2028-06-29** — still present in Entra.
- Rotate: Entra → app `066b737b-…` → Certificates & secrets → new client secret → copy the
  **Value** → paste into the Claude connector settings.

**If it breaks while idle (the call-the-original-dev list)**
- Connector stops authenticating: most likely the Entra secret expired (above).
- Card scan returns 503: `ANTHROPIC_API_KEY` missing/disabled; the rest of the app is fine.
- First request after idle is slow: Cloud Run cold start + DB waking. Normal.
- `/api/contacts` 500s: check that a column did not get dropped from `models.py` (see Gotchas).

**Idle cost:** Cloud Run scales to zero and Neon/Azure free tiers auto-pause, so it costs
near nothing sitting unused.
