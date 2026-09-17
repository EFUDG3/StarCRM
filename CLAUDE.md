# Star CRM — project context for Claude

A relationship board / lightweight CRM for **Star Flooring & Remodeling** (San Diego).
Started as an in-browser artifact; now a real, deployable web app with a database,
business-card scanning, multi-user profiles, and (in progress) a Claude connector.

This file is the single source of truth for picking the project back up. Read it first.


> **History archive:** session-by-session development logs, completed investigations,
> past deploy details, and resolved issues live in [`CLAUDE-HISTORY.md`](CLAUDE-HISTORY.md).
> Search it by date or topic when you need context on why a decision was made.

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

> **Planning note (2026-08-25) - Mileage: PURPOSE-OF-TRAVEL field on manual trip entry.**
> - **Gap:** the calendar-scan flow already fills a per-visit purpose (event title, grounded-hybrid
>   from body when title is vague — decided 2026-07-20). Manual trip entry on the Mileage tab does
>   NOT — the form has date + stops + rate only. The mileage report XLSX has a Purpose column and
>   IRS substantiation explicitly requires business purpose per trip, so manual entries fall
>   short of what the report needs.
> - **Fix (small, ~30 min):** add a required `purpose` text input on the trip-entry form
>   (`frontend/src/Mileage.jsx`). Store it on the trip: either a new `purpose` column on `Trip`
>   OR reuse a single-string convention on the existing `legs_json` payload. The former is cleaner
>   and matches how scanned visits carry their label — `Trip.purpose = String` additive column,
>   default "" so existing rows aren't broken. Include in the log expandable row and in the CSV/
>   XLSX export (mileage_report.py already has a Purpose column — just read `trip.purpose` when
>   it's a manual entry, keep the visit label when it's from a scan).
> - **When:** cheap enough to slip in alongside another mileage change; no user-facing dependencies.
>   Add to the DDL migration in `_ensure_schema`: `ALTER TABLE trips ADD COLUMN IF NOT EXISTS
>   purpose VARCHAR DEFAULT ''`.

> **Planning note (2026-08-25) - PRODUCT CATALOG tab with commonly-stocked flooring items + labeled photos.**
> - **The workflow this replaces (Ethan, 2026-08-25):** sales reps currently google a carpet /
>   LVP / tile picture, open it in an image editor, type the product name on top, save the mangled
>   result, then share it with a customer. Every rep does it every day. We should have this ready
>   to go from Star's own stocked-item list, with the name already on the image.
> - **Scope: shared company-wide (like Accounts), read-mostly.** Office admin adds/edits the
>   catalog; sales reps search + share. Product data is not sensitive and every rep needs the same
>   list, so shared is the right model. Version 1 covers commonly-stocked items only, not every
>   SKU we can special-order.
> - **Data model:**
>   - `products` (id, name, brand, category [carpet|lvp|tile|hardwood|laminate|sheet_vinyl|cove_base|other],
>     sku, color, series, description, tags_json, image_blob_key, thumbnail_blob_key, active BOOL,
>     stocked BOOL, notes, updated_at, updated_by) — shared, no user_id.
>   - Later: `product_categories` if we need color/collection hierarchy; skip for v1.
> - **Image storage: Azure Blob Storage** (Star Personal CRM > Star Blob container, still to be
>   provisioned — same setup discussed 2026-08-19 for the deferred architectural-plans upload).
>   Direct browser upload with a short-lived SAS URL, image bytes never touch the container app.
>   Store `image_blob_key` on the product row, generate a fresh read-SAS URL when the frontend
>   requests it (cheap Azure SDK call). Auto-generate a thumbnail at upload time for the grid view.
> - **The "labeled image" ask — HOW to actually deliver it:**
>   - Store the RAW product photo, not a labeled variant. Labels change (name typos, rebrands)
>     but photos don't, and we don't want to lose the original.
>   - Render the label as a canvas overlay in the browser: product name + Star badge in a
>     bottom-band, generated client-side from the raw image via `<canvas>` toBlob(). One button
>     -> composite JPEG in clipboard, another -> download.
>   - This means one raw image serves as-is on the catalog list AND as the "share to customer"
>     asset, with the name text always current.
> - **UI (mirrors Accounts):** grid of product cards (thumbnail + name + brand + category tag),
>   filter chips by category, search across name/sku/color/brand/tags. Click card -> detail
>   with full-res image, "Copy labeled image" + "Download labeled image" buttons, description,
>   notes, stock status. Admin "Add product" / "Edit" gated to specific user set (or just
>   trust-and-verify like Accounts — every write stamps updated_by).
> - **Backend:** new `backend/products.py` (list/create/get/update/delete + Blob SAS helpers),
>   `Product` model, `ProductIn` schema, `frontend/src/Products.jsx`, api.js. Chat.py gets
>   `search_products` + `get_product` so starbot can answer "do we stock that Shaw pattern in
>   greige?" from the shared catalog.
> - **Blob storage is the real dependency**. This same account will later serve architectural
>   plans (parked from 2026-08-19). Provisioning steps: Storage Account (starflooringblob),
>   container `products` (private), managed identity or connection-string secret in Container
>   Apps, `azure-storage-blob` in requirements. ~$0.02/GB/mo + minimal transaction cost.
> - **When:** after phase-2 email digest ships AND after meeting-minutes tab. Provisioning the
>   Blob account can happen in parallel any time — it unblocks both Products AND the future
>   architectural-plans upload.

> **Planning note (2026-08-25) - MEETING MINUTES tab, next up after phase-2 digest ships.**
> - **Purpose:** every Teams meeting a user was in gets summarized and stored on THEIR profile,
>   so anyone who attended has the record and anyone who didn't never sees it. Star runs
>   everything in Teams and many crew use Read.ai natively.
> - **Scope (Ethan's call 2026-08-25): PER-USER, not shared.** Same privacy model as
>   Contacts/Todos/Email. A per-meeting "share to team" button in phase 2 (like the shared-account-
>   facts idea) can promote specific meetings company-wide when a decision needs everyone. Default
>   private — never opt-in-to-be-private.
> - **Sources:**
>   1. **Teams meetings (primary):** Graph `/me/onlineMeetings/{id}/transcripts/{tid}/content`
>      returns the VTT. Requires new delegated scopes — `OnlineMeetings.Read` for listing +
>      `OnlineMeetingTranscript.Read.All` for transcript bodies (admin consent required, ethan
>      can grant). We do NOT need `Chat.Read` (which would pull all user chats — too broad).
>   2. **Manual paste (fallback):** a text field on each meeting record, for when a transcript
>      isn't available or someone took hand notes. Zero scopes.
>   3. **Read.ai (supplemental, phase 2):** their webhook posts a JSON summary to a URL we own.
>      Structured data, no chat scope needed. Skip parsing their Teams-chat notifications; the
>      webhook is cleaner. Only wire this if the Teams-transcript path proves insufficient.
> - **"Catch every meeting" strategy (Ethan's explicit ask): polling job, same pattern as the
>   digest.** A Container Apps Job runs every ~30 min per work-hours cron and for each opted-in
>   user: list their recent onlineMeetings, find the ones that ended AND have a transcript AND we
>   haven't stored yet, fetch the VTT, summarize via Claude (Ethan has an existing skill for this
>   — reuse the prompt), store the summary. Cheaper than change notifications for our volume and
>   simpler than the transcript-subscription model (which has strict lifetime rules).
> - **Model (tentative):** `meetings` (id, user_id, source, teams_meeting_id, title, start_at,
>   end_at, organizer, attendees_json, transcript_url, recording_url, shared BOOL DEFAULT
>   FALSE, created_at) and `meeting_notes` (meeting_id, summary TEXT, decisions TEXT,
>   action_items_json, generated_by, raw_transcript_ref). NO raw transcripts stored in Postgres
>   — keep the SharePoint/OneDrive link; the transcript itself can be many MB and re-fetching is
>   cheap. Same reasoning as the "no email bodies" rule from phase-1.
> - **Action-item extraction (worth doing on day 1):** the summarizer returns a JSON array
>   `[{owner, action, due?}]`. If `owner` matches the signed-in user's first name, optionally
>   create a todo with `source_link` pointing at the meeting record. That closes the loop from
>   "Salam said in the meeting I should X" -> a real task on Tuesday morning.
> - **UI shape (matches the tab family):** list ranked by start_at desc; row = title, date,
>   attendee count, action-item count; click into detail = summary + decisions + action-items
>   (checkable) + link to Teams recording + expandable transcript. Same lucide icon pattern
>   (MessageSquare or Presentation). Manual "Add meeting" button for the fallback path.
> - **Backend:** new `backend/meetings.py` (list/get/log/share endpoints + poll_teams_meetings
>   job entrypoint mirroring digest's shape), Meeting/MeetingNote models, MeetingIn schema,
>   `frontend/src/Meetings.jsx` mirroring the Projects tab, api.js helpers. Chat.py gets 2-3
>   new starbot tools (list_meetings, get_meeting_summary) so starbot can answer "what did
>   we decide in the sales meeting Tuesday?".
> - **Deferred until:** phase-2 digest is live (target 2026-08-25 today). Then meetings is
>   next in line.

---

## Stack

- **Frontend:** React + Vite + Tailwind. Single-page UI in `frontend/src/App.jsx`,
  API client in `frontend/src/api.js`. No router — views are state-driven.
- **Backend:** FastAPI + SQLAlchemy 2 + psycopg v3. **PostgreSQL only** (no SQLite).
- **DB:** **Azure Database for PostgreSQL Flexible Server** (`star-crm-pg`, PG 18) in production as of 2026-08-19 (migrated off Neon; Neon kept as rollback until ~2026-08-26). Docker Postgres locally
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

## Deploy (Azure Container Apps)

Production: Container App `starbot`, RG `starbot`, region westus3, ACR `cadd18599bd0acr`.

```bash
# Build image in ACR
az acr build --registry cadd18599bd0acr --image starbot:<TAG> .

# Deploy with revision suffix matching the tag
az containerapp update -n starbot -g starbot \
  --image cadd18599bd0acr.azurecr.io/starbot:<TAG> \
  --revision-suffix <TAG>
```

- **`--revision-suffix` must match the image TAG** (standardized 2026-09-14). Omitting
  it falls back to Azure's auto-incrementing number, which tells you nothing.
- **Secrets via Container Apps secrets + `secretref:`**, never inline env vars. Use the
  az CLI Python directly to avoid `&` truncation:
  `& "C:\Program Files\Microsoft SDKs\Azure\CLI2\python.exe" -IBm azure.cli containerapp secret set ...`
- **KEDA cron scale rule** (`business-hours`): holds 1 replica warm Mon-Fri 7:30am-3:30pm
  Pacific, scales to zero off-hours. `minReplicas=0`, `maxReplicas=10`.
- **Custom domain:** `starbot.starflooringandremodeling.com` with managed cert.
- **Legacy:** Google Cloud Run service `star-crm` (project `starcrm-500221`) is still up
  but superseded. See CLAUDE-HISTORY.md for Cloud Run deploy commands.

---

## Open items / next steps (priority order)

### Completed

- ✅ **Phase 1: DB-backed system prompt + admin API** (2026-09-15)
  - `SystemPromptSection` model with key/label/content/sort_order/active
  - `is_admin` flag on User, ethan set as admin via migration
  - Admin router (`/api/admin/prompt`, `/api/admin/glossary`) with CRUD
  - `_system_prompt()` reads sections from DB, substitutes runtime placeholders
  - Fallback prompt if table is empty; 3 seeded sections (identity, capabilities, rules)
  - Note: admin API has GET + PUT for sections but no POST to create new ones yet

### Phase 2: Chat feedback + per-user preferences (IN PROGRESS)

- **ChatFeedback model** — thumbs up/down on assistant responses. Stores rating, optional
  quick-tap chip (wrong_tool, too_verbose, outdated_info, missed_files, already_knew),
  optional correction text, and a snapshot of tools used + message previews.
- **ChatPreference model** — per-user behavior preferences injected into the system prompt.
  Category-tagged free text (format, tool, knowledge, general). Source tracks provenance
  (manual vs suggested).
- **ChatSuggestion model** — proposed preferences from pattern detection (Phase 4 logic).
  Never auto-applied; evidence_json + rationale for trust.
- **API endpoints**: `POST /api/chat/feedback`, CRUD `/api/chat/preferences`
- **Frontend**: thumbs up/down bar on each assistant message, chip picker on thumbs-down,
  preferences management page
- **System prompt injection**: active preferences appended after glossary in `_system_prompt()`

### Phase 3: File upload + Azure Blob store

- Users upload files as context for AI conversations
- Azure Blob Storage for private file content, Postgres row with summary_key
- Metadata shadow: everyone sees "Amanda uploaded X on date Y" but only Amanda's chat
  can fetch actual content
- New section in system prompt listing available files per user

### Phase 4: Pattern detection + admin suggestion queue

- 3+ similar thumbs-down corrections trigger a suggested preference
- Suggestion card with rationale + evidence ("you gave thumbs-down to 3 verbose responses")
- User accepts or rejects; accepted suggestions become ChatPreference rows
- Admin queue for company-wide suggestions (patterns across users)


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
