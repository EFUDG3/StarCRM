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

> **DEPLOY FAILURE MODE (2026-09-14) - revisions `starbot--0000046` and `starbot--0000047` are BAD,
> built from image tag `starbot:next-action`. DO NOT roll back to either. Both crash-loop on
> startup with `Cannot reach the database at star-crm-pg... connection timeout expired`.**
> - **Ethan's observed pattern:** deploying while physically at Star Flooring worked; deploying
>   from elsewhere produced both bad revisions. **Until the real root cause is confirmed, deploy
>   only from Star Flooring's network.**
> - **Worth checking before treating that as the permanent mechanism - flagged, not confirmed:**
>   `az containerapp show -n starbot -g starbot` lists 150+ `outboundIpAddresses` for this app -
>   Consumption-plan Container Apps draw from a shared, non-static Azure outbound IP pool, not one
>   fixed IP tied to a physical location. That means the DEPLOYED CONTAINER's own DB connectivity
>   shouldn't depend on where the `az` command was run FROM - it depends on whether the Postgres
>   firewall's "Allow public access from Azure services" setting is actually enabled. **Adding
>   Ethan's own current IP to the firewall (the usual local-dev fix) does nothing for the deployed
>   container** - that's a separate setting entirely. Check the `Networking` blade on
>   `star-crm-pg`, or `az postgres flexible-server firewall-rule list`, before assuming "deploy
>   from the office" is the real permanent fix rather than a coincidence or a fragile stopgap.
> - **Related, separate issue that prompted this:** the `uvicorn --reload` failure seen right
>   before the bad deploys was ordinary LOCAL dev hitting the Postgres firewall (Ethan's current
>   public IP wasn't allowlisted) - unrelated to the Container App issue above even though the
>   error text is identical (same code path, `_ensure_schema_or_explain` in `main.py`), which is
>   exactly why the two got conflated in the moment. Local fix: Portal -> `star-crm-pg` ->
>   Networking -> **+ Add current client IP address** -> Save. Does NOT fix the deployed container.
> - **Last known-good revision:** `starbot--0000045` - confirm its actual image tag with
>   `az containerapp revision show -n starbot -g starbot --revision starbot--0000045 --query
>   properties.template.containers[0].image` before using it as a rollback target from memory.
> - Also documented in Notion: **Failure Modes** (full writeup) and **Deployment** (bad-revision
>   callout + tag list).
> - **STANDARDIZED (2026-09-14): `--revision-suffix` is no longer optional on `containerapp
>   update`, and it must match the image TAG exactly** (`starbot:crm-mobile-fixes` ->
>   `--revision-suffix crm-mobile-fixes`). Root cause of the inconsistent naming Ethan noticed
>   (some revisions read `starbot--triagerules`, others `starbot--0000046`): omitting the flag
>   falls back to Azure's auto-incrementing number, which tells you nothing about what's actually
>   running. One rule, no separate naming scheme to remember. Also updated in the Deployment
>   Notion page's rollback command, which previously used a generic `rollback` suffix instead of
>   the previous tag - same fix applies there.

> **CRM OVERHAUL (2026-09-11, same day, after the UI pass below) - worked Ethan's backlog of CRM
> annoyances top-to-bottom, one item at a time with review in between. Login scoping, an address
> field, several real bugs, and a full log-note auditing system (attribution + edit + soft-delete)
> that also absorbed and retired the old unaudited `notes` field. Built + reviewed by Ethan in
> local dev, which points at the SAME live Azure Postgres as prod (see Database Notion page) - so
> the data-side migration below has ALREADY run against real prod data. Not yet deployed to the
> Container App itself; that's the next step, Ethan running it himself as usual.**
> - **Login now always defaults to the signed-in user's OWN profile** (`App.jsx` init effect).
>   The bug: `api.getCurrentUser()` reads a `localStorage` value that persists indefinitely across
>   reloads/sign-ins, and the old logic only overrode it when that cached profile no longer
>   existed at all - otherwise a stale "last viewed" profile (e.g. from once switching to check
>   Bob's board) would keep showing on every future login on that device, with no visual cue you
>   weren't looking at your own data. Now checks `me.userId` (the actual M365-linked profile)
>   FIRST on every fresh load. The profile switcher itself is untouched - it just doesn't "stick"
>   as the default anymore. Confirmed via `main.py:300-303` that the switcher itself (any signed-in
>   employee can view another profile's board) is intentional design, not something to lock down.
> - **Contact address field**, end-to-end: `Contact.address` (additive column), `ContactIn`,
>   `serialize()`, both card-scan and manual entry. Deliberately **left off the list table**
>   (Name/Company/Role/Email/Phone/Next action/Category unchanged) - shown only on the detail
>   view, per Ethan's call that the table shouldn't grow another column for something most rows
>   won't need to compare at a glance. `cards.py`'s OCR prompt now asks Claude for a 6th field.
> - **Fixed: scanning a card while the blank "Add contact" form was already open did nothing
>   visible.** Root cause - `ContactForm` had no React `key`, so completing a scan updated the
>   `editing` prop but React reused the same mounted instance, whose internal form state is only
>   seeded from `initial` on first mount. Fixed with a `newContactNonce` counter bumped on both
>   "Add contact" and a completed scan, used as the form's `key` - existing-contact edits already
>   keyed correctly off their own `id`. Side effect (matches what Ethan wanted): clicking "Add
>   contact" again on a dirty blank form now also resets it, not just the scan path.
> - **Duplicate-name detection, framed as a nudge, not a wall.** Exact match (trimmed,
>   case-insensitive) against the user's already-loaded contacts, excluding the contact being
>   edited - checked live on every keystroke, no debounce needed and no API call, since an exact
>   match can't false-positive on a partially-typed name. First pass greyed out and disabled
>   "Save contact" on a match; **reverted after Ethan's pushback** ("plenty of people share a
>   name... refusing to save would be a huge disappointment in prod") to a "Save anyway" label
>   swap instead - same warning copy, button stays clickable the whole time.
> - **Phone types expanded**: `fax` and `tollfree` added alongside cell/work/home/other, on both
>   the OCR prompt (`cards.py`, with label hints - F/fax, TF/toll-free) and the manual dropdown.
>   Frontend gained a shared `PHONE_TYPES` label map (`App.jsx`) used by both the `<select>` and
>   the detail-view display, replacing a naive `.charAt(0).toUpperCase()` that would have rendered
>   "tollfree" as "Tollfree" instead of "Toll-free".
> - **Removed dead dev-only surface, all the way through the stack**: the "Reset data" button,
>   `api.resetData()`, the `POST /api/reset` route, `load_seed()`, and the Bob-creation-and-seed
>   block inside `_seed_on_first_run()`. Since that block was the only caller of `SEED_CONTACTS`,
>   **deleted the now-fully-orphaned `backend/seed.py` outright** (186 lines) rather than leave it
>   dangling - confirmed nothing else imports from it first. `_seed_on_first_run()`'s docstring
>   corrected too (it still claimed to seed a demo profile). None of this touches Bob's real,
>   already-existing prod data - the `count() == 0` guard hasn't fired since long before there
>   were 15 real users, so this was dead code, not a live data path.
> - **Card-scan camera UX, per Ethan's two rounds of feedback**: added a hint below the camera
>   frame ("Keep the card's text upright in the frame, scanner has a hard time with sideways
>   text") - short version, after an initial draft he said "said too much." Then **removed the
>   dashed landscape guide-box overlay entirely** - his reasoning: it implied the card must fit a
>   fixed box, which a non-technical user can't intuit is optional, and a bigger/closer card in
>   frame reads better for OCR than one constrained to a fixed aspect ratio.
> - **Fixed: "sort by newest" silently behaved like "sort by name" for anything added the same
>   day.** `serialize()` was truncating `created_at` to date-only (`.date().isoformat()`) before
>   the comparator ever saw it, so same-day contacts had no time-of-day to compare and fell
>   through to the existing name tiebreaker. Added `createdAt` (full timestamp) alongside the
>   existing date-only `created` (left alone - still used for the "Added [date]" display) and
>   pointed the sort comparator at it.
> - **Log-note auditing system** - the big one, built in response to "people have access to other
>   people's CRMs, it's important to log who made what note":
>   - `Interaction` gained `by`, `edited_by`/`edited_at`, `deleted`/`deleted_by`/`deleted_at`,
>     `created_at` (additive). Mirrors `AccountInteraction.by`, which already did this for shared
>     Accounts - this closes the same gap for personal-CRM contacts.
>   - New `PUT`/`DELETE /api/contacts/{id}/log/{interaction_id}` - edit a note's text (stamps
>     `edited_by`/`edited_at`, leaves the original author alone) and **soft-delete** (flips
>     `deleted`, stamps who/when - never a hard delete). `serialize()` now returns `log` (active)
>     and a separate `deletedLog`.
>   - Frontend: each note shows "Logged by X · edited by Y", hover-reveal edit/delete icons
>     (inline edit, no page nav), delete asks for confirmation, and a **"Deleted notes"** section
>     that only renders when non-empty (struck-through text, who logged it, who deleted it).
>   - **REAL BUG CAUGHT MID-BUILD, Ethan's catch**: attribution was using `get_current_user()`,
>     which resolves to whichever profile is being VIEWED (honors `X-User-Id`, i.e. the switcher),
>     not who's actually signed in. Switch to view Bob's board, log a note, and it would have said
>     "Logged by Bob Bendixen" even though Ethan wrote it - silently defeating the entire point of
>     the feature. Fixed with `m365.get_actor_name()` (a new dependency, reads the session cookie
>     directly, ignores `X-User-Id` entirely, `None` when there's no browser session to read -
>     local dev without M365, or the MCP connector's bearer-token path). Wired into `log_touch`,
>     `edit_log`, `delete_log`, `complete_action`, `create_contact` as `by=actor_name or user.name`.
>     Placed in `m365.py` (not `main.py`) specifically so `accounts.py` could share it without a
>     circular import.
>   - **Checked accounts.py for the same bug - it does NOT have it.** Every route there resolves
>     `user` via `Depends(m365.get_session_user)` directly, never `get_current_user()` - Accounts
>     is fully shared with no per-user "viewing as" concept at all, so there was never a switcher
>     to bypass. Confirmed across all six routes before saying so; almost "fixed" something that
>     wasn't broken.
> - **Retired the freeform `notes` field entirely - it was a silent, unaudited bypass of the
>   system above.** Anyone could type anything into `notes` via the edit form with zero
>   attribution, timestamp, or history, completely sidestepping log-note auditing. Ethan's
>   diagnosis mid-session, and the fix:
>   - `ContactIn.notes` removed; `ContactIn.note` added - **honored only on `create_contact`**
>     (seeds the contact's first log entry, attributed via `get_actor_name`), silently ignored on
>     update. This closes the bypass server-side, not just in the UI - a direct API call can't
>     write unaudited text into a contact anymore either.
>   - `update_contact` no longer touches notes at all. `serialize()` no longer returns `notes`.
>   - New `_migrate_notes_to_log()`, run once at startup (`lifespan` handler): any contact with
>     existing `notes` content gets it copied into a real log-note Interaction, then `notes` is
>     cleared. Idempotent - only touches rows where `notes` is still non-empty, no-op after the
>     first full pass. **The `notes` column is NOT dropped** (this project never drops columns) -
>     just permanently blank going forward.
>   - **Attribution on migrated/legacy notes iterated once**: first pass used a generic
>     `"(imported from Notes field)"` placeholder; Ethan asked for it to say the contact's actual
>     owner's name instead ("there were not many logged notes and all of it was done in front of
>     me or by me... this would not be an auditing issue and would look better for a non-technical
>     user"). Now attributes to `contact.owner.name`. Also backfills ANY pre-existing log note
>     whose `by` is still blank (predates the `by` column entirely) the same way, and self-heals
>     any row already stamped with the old placeholder text - so re-running it after Ethan's first
>     local test cleaned those up too.
>   - Frontend: Notes textarea gone from the contact form. Replaced with an optional **"Log a
>     note"** field shown ONLY when creating a NEW contact (`!form.id`) - hidden on edit, since
>     further notes go through the detail view's log-note UI, not the edit form. Bare unstyled
>     notes paragraph removed from the detail view (nothing left to render there). Contact-list
>     search now matches log-note text instead of the old `notes` field.
>   - **This also answered the next backlog item for free** ("able to log on card entry") - a
>     scanned card lands on this same form, so the optional "Log a note" field covers it.
> - **Fixed: the CRM tab's loading state blanked the ENTIRE page**, header and tab bar included -
>   the only tab built this way, because the CRM board is inlined directly in the top-level
>   component rather than a separate child component like Email/Mileage/Tasks/Accounts/Projects,
>   each of which already scopes its own "Loading…" to its own content area. Removed the early
>   `if (!contacts && view === "board") return <fullscreen loading>` and moved the loading state
>   inside the board's own content block, matching Email.jsx's existing `py-16 text-center`
>   pattern - header/tabs/profile switcher now stay visible and interactive while contacts load.
> - **Deploy plan, agreed with Ethan:** `_migrate_notes_to_log()` stays in the codebase until the
>   Azure Container App deploy is confirmed clean (it's idempotent and already a no-op against
>   local/prod data, so leaving it costs nothing) - remove it and its `lifespan()` call ONLY after
>   that verification, not before. `get_actor_name()` is NOT a one-time migration and is not
>   scheduled for removal.
> - **`README.md` flagged, not touched**: it still describes the original Cloud-Run-vs-GCP-and-Neon,
>   no-auth, X-User-Id-only proof-of-concept architecture, including a documented `/api/reset`
>   route that no longer exists as of this session. Significantly stale relative to the real
>   current stack (Azure Container Apps + Azure Postgres + M365 auth) - out of scope for today,
>   worth a dedicated pass separately rather than folding into this already-large change.

> **UI ACCESSIBILITY PASS (2026-09-11) - larger text/touch-targets, darker muted grays, wider
> content area, button hover feedback, looser line-height. Frontend-only, no backend/schema
> changes. Committed as `90e3ba4`, built + visually reviewed by Ethan in local dev across this
> session.**
> - **PUSHED + DEPLOYED (per Ethan):** `git push` sent this commit to GitHub together with the
>   previously-stranded `50c721d` (see below - it had never actually reached `origin/main` despite
>   an earlier note in this file claiming it had). Ethan then ran `az acr build --registry
>   cadd18599bd0acr --image starbot:ui-a11y .` followed by `az containerapp update -n starbot -g
>   starbot --image cadd18599bd0acr.azurecr.io/starbot:ui-a11y` himself, per usual. **Not
>   independently verified from this session** (no Azure CLI access here) - a future session
>   should confirm with `az containerapp revision list -n starbot -g starbot -o table` before
>   treating "ui-a11y is live" as ground truth, same caution this file applies to every other
>   deploy claim.
> - **Rollback target, if needed:** `starbot--triagerules` (image tag `triage-rules`) was the
>   revision live immediately before this one.
> - **Why:** make StarCRM easier to use for older/less-technical staff. Specific complaints came
>   from Ethan looking at real screenshots mid-session, not guessed at up front (e.g. the
>   Mileage "Pull from my calendar" label called out twice before it actually read as dark enough).
> - **Root font-size bumped 16px->17px** (`index.css`, `html { font-size: 106.25% }`) - scales
>   every rem-based Tailwind text/spacing utility app-wide in one line rather than touching each
>   component. Does NOT reach hardcoded `text-[Npx]` classes (see column headers below) since
>   those are literal pixels, not rem.
> - **Global button hover/press feedback** (`index.css`): buttons carry color via inline
>   `style={{ background: INK }}` etc. rather than Tailwind classes, so a `filter: brightness()`
>   rule on `button:hover`/`:active` was the one approach that reaches every button regardless of
>   its specific color.
> - **Page canvas darkened** (`App.jsx`): new `PAGE_BG = "#EBE6E0"`, ~4pt darker than the existing
>   `MIST` (`#F3F0EC`). Applied ONLY to the four outer page wrappers (loading/sign-in/board-empty/
>   main shell) - `MIST` itself is untouched because it's reused everywhere as a chip/panel
>   background *inside* white cards, and darkening it too would have killed the contrast it was
>   meant to create.
> - **Muted-gray text ladder darkened across every tab** (~195 occurrences across Accounts/App/
>   Chat/Email/EmailRules/Mileage/Projects/Tasks): `#8b9a9f` (~2.9:1 contrast, the most common
>   offender) -> `#5f6e74` (~5.3:1); `#b0b8ba` (~1.9:1) -> `#6f7d82` (~4.3:1); `#6b7a80` (~4.5:1,
>   borderline) -> `#55646a` (~6.1:1); `#4a5a60` -> `#343e41` (measured ~7-11:1 but still looked
>   pale in practice on small-caps/tracking-widest labels - contrast-ratio math undersells how
>   much anti-aliasing thins small tracked-out type). The Mileage "Pull from my calendar" label
>   specifically now uses `INK` directly rather than the shared tier, since even the darkened tier
>   read as too light there.
> - **Column headers bumped `text-[10px]` -> `text-[11px]`** in the CRM contact table (`App.jsx`)
>   and account table (`Accounts.jsx`) - these use hardcoded pixel sizes, so they were the one
>   spot the root font-size bump didn't reach.
> - **Icon-only action buttons (edit/delete/dismiss/move) got +4px padding on each side** app-wide
>   (`p-1`->`p-2`, `p-1.5`->`p-2.5`, `p-2`->`p-3`), including Email's "Move"/"Restore" chips
>   (`px-1.5 py-0.5`->`px-2.5 py-1.5`) - bigger touch targets without changing icon size.
> - **Line-height loosened via `tailwind.config.js` theme override**, not a generic CSS rule -
>   Tailwind's `text-xs`/`text-sm`/`text-base` utilities set line-height directly on the element
>   (used almost everywhere in this codebase), which would silently override a `body { line-height
>   }` rule. New pairs: xs 1rem->1.125rem, sm 1.25rem->1.375rem, base 1.5rem->1.625rem. Elements
>   with an explicit `leading-*` class are unaffected (more specific).
> - **Main content container widened then re-narrowed to taste**: `max-w-5xl` (64rem) ->
>   `max-w-6xl` (72rem) -> settled at `max-w-[70.25rem]` (1124px) after two rounds of "shave a few
>   px" feedback, keeping the existing `lg:` calc-split-the-difference formula proportional to the
>   new base.
> - **Verified, not assumed: real hyperlinks (`<a>` tags) already use `underline` app-wide.**
>   Checked every `<a>` in the codebase before claiming a gap. Only exception is the 6 "Sign in
>   with Microsoft" links, correctly unstyled since they're solid CTA buttons, not text links.
> - **Process note:** an early pass used `sed` to bulk-replace hex colors and silently stripped
>   this repo's CRLF line endings on 8 files, turning a dozen real edits into a 1000+-line-diff per
>   file. Caught via `git diff --stat` before anything was committed; restored CRLF and re-verified
>   diffs were proportional to the actual changes. Worth remembering: `sed -i` on this Windows repo
>   needs a manual CRLF restore pass (`sed -i 's/\r$//; s/$/\r/'`) or should be avoided in favor of
>   the Edit tool, which preserves line endings natively.
> - **Also swept up in this commit: a stale doc gap below.** The 2026-09-02 `50c721d` triage-engine
>   commit was never actually pushed to GitHub despite this file previously saying "Ethan pushed it
>   himself, per usual" - confirmed with a fresh `git fetch` before touching anything. Going out
>   together with today's UI commit.

> **DEPLOYED 2026-09-10 (rev `starbot--triagerules`) - the triage engine split, per-user rules and
> correction feedback loop are LIVE. NOT COMMITTED TO GIT - see the warning below.**
> - **Build:** `az acr build --registry cadd18599bd0acr --image starbot:triage-rules .` -> Run ID
>   **`ds1k`**, 1m0s, digest **`sha256:3832944c9816dee802b782e8bf173cc1e43b3dc73926e01a3ce675e0cabaf947`**.
>   Base images python 3.12-slim + node 20-slim. Claude ran the deploy this time at Ethan's explicit
>   request ("go ahead and do it for me so i can see it in prod").
> - **Live revision:** `starbot--triagerules`, 100% traffic, 1 replica, provisioning Succeeded.
> - **ROLLBACK TARGET: `starbot--mileagev3`** (Active, 0% traffic, created 2026-09-01) on image
>   **`starbot:mileage-purpose-ui`**. Roll back with
>   `containerapp update -n starbot -g starbot --image cadd18599bd0acr.azurecr.io/starbot:mileage-purpose-ui`.
>   (An earlier note in this session guessed `emailv2`/`email-refinements` as the rollback - WRONG;
>   the mileage purpose-field work shipped after that. Verify the actual previous revision with
>   `containerapp revision list`, never from memory.)
> - **Also shipped in this image: Ethan's `connect_timeout` fix** in `database.py` -
>   `create_engine(..., connect_args={"connect_timeout": 10})`. Without it an unreachable host
>   (usually a local client IP that dropped out of the Azure Postgres firewall) hangs on the TCP
>   connect with NO error, which reads as "the app froze" rather than "the DB is unreachable".
>   Import-checked before the build for exactly this reason - a syntax error in `database.py` would
>   crash-loop the container rather than fail visibly.
> - **SCHEMA MIGRATED CLEANLY on boot** (additive `_ensure_schema()`; it runs at module import, so
>   reaching "Application startup complete" IS the proof it succeeded). Verified directly against
>   prod: 3 new tables `triage_rules` / `triage_feedback` / `triage_suggestions` (all 0 rows, as
>   expected) + 5 new columns `email_threads.signals_json|decided_by|manual_msg_id` and
>   `user_prefs.triage_rules_json|triage_profile`. **21 tables now (was 18). Existing data intact:
>   accounts 120, email_threads 2,212.**
> - **Post-deploy verification (all passed):**
>   - `/api/health` 200 in 0.46s warm.
>   - **IDENTITY CHECK - the one that actually proves the new build is serving:** four routes that
>     exist ONLY in this build all return **401, not 404** - `GET /api/email/rules`,
>     `GET /api/email/corrections/summary`, `POST /api/email/resync`, `POST /api/email/retriage`.
>     (A 404 would mean the old image was still live; health 200 proves nothing on its own.)
>   - Clean boot in the container logs: "Application startup complete" + "StreamableHTTP session
>     manager started" - so the MCP connector is unaffected and no import/DDL error occurred.
>   - KEDA `business-hours` cron scale rule SURVIVED the image update again (America/Los_Angeles,
>     start `30 7 * * 1-5`, end `30 15 * * 1-5`, desiredReplicas 1, min 0 / max 10).
>   - Live JS bundle `/assets/index-B_jJP5Cm.js` (499,769 bytes, matches the local build) greps
>     clean for "Triage rules", "Suggested rules", "Rebuild from Outlook", "About your job",
>     "Built-in rules", "Where does this belong", "Something in the wrong lane", "more below the
>     top", "Not my area", "you moved this here", "Handled" - and does NOT contain "starbotMail",
>     confirming the named-tab revert shipped (rows open a new tab each, as Ethan wants).
> - **COMMITTED: `50c721d`** "email: triage engine split (signals + decide), per-user rules,
>   correction feedback loop" (Ethan pushed it himself, per usual; no Co-Authored-By trailer).
>   Working tree clean. **So `starbot--triagerules` == commit `50c721d`** - prod is traceable to
>   a known ref, which is what revs 41-42 lacked in August. Note the deploy happened BEFORE the
>   commit (`acr build` packs the working directory, not a git ref), so the image and the commit
>   match only because nothing changed in between - verify with `git status` before trusting the
>   equivalence on any future deploy.
> - **REMAINING MANUAL STEP, once per pilot mailbox:** Email tab -> **Triage rules** -> Maintenance ->
>   **Rebuild from Outlook**. Rebuilds snippets with the signature fix, re-triages with the model
>   pass (recovering whatever the old ack regex swallowed), preserves hand-moved threads. ~80s and
>   1-2 cents each. Existing mailboxes auto-seed the legacy tier-2 rule set on their first sync via
>   `ensure_rule_set()`; brand-new ones get tier 1 only (14 of 23 rules on, not 23/23).
> - **Worth doing in the same sitting, both free:** fill in "About your job" for Ethan and Salam
>   (highest-leverage single field - it rides into every model call), and dismiss the Salam
>   triage-test forwards so the suggester is not trained on synthetic mail.

> **Follow-up (2026-09-02, later) - FIRST REAL MISCLASSIFICATION BATCH triaged: 5 new company-wide rules, `cold` redefined. 31/31 cases pass.**
> - **Source:** 10 wrong verdicts reported from Ethan's, Salam's and Bob's mailboxes (the only three
>   that have run the tab). All 10 are now fixture cases in `tests/fixtures/cases.json`, tagged
>   `REAL:`, using the FIRST-300-CHAR snippet the engine actually sees rather than the full body -
>   so the test is faithful to what triage gets from Graph.
> - **THE HEADLINE FINDING: 6 of the 10 came from the tier-1 "mechanical" bulk rules** - the ones
>   called universally safe that morning. The premise was wrong. **"No human reads a reply to this
>   address" is NOT the same claim as "there is nothing here for you to do."** A California SB
>   certification notice with a 09/09/26 discontinuance deadline (`donotreply@fiscal.ca.gov`), a
>   Paychex 401(k) amendment needing a wet signature on page 14 (`noreply@paychex.com`), and an
>   SDUSD Group 5C shade-shelter bid invitation (`no-reply@reproconnect.com`, with an unsubscribe
>   footer) were all filed to Cleanup at rank 5.
> - **TIER-1 ORDER IS NOW LOAD-BEARING.** Real obligations get first look; automation detection only
>   claims what is left: `ooo -> action-required -> bid-invitation -> pending-on-us ->
>   financial-record -> bulk-sender -> bulk-copy -> bulk-subject -> cc-bystander -> closure`.
> - **5 new tier-1 rules (all company-wide, all on by default):**
>   1. **`tier1:action-required`** - `DEMANDS_ACTION` (response needed / action required / must
>      submit-sign-return / on or before <date> / failure to comply / to avoid discontinuance /
>      signature required / past due / payment declined). **Gated on `to_me or named or flagged`** -
>      that gate is what stops it over-promoting the RingPlan thread, whose subject literally reads
>      "Action Required:...Welcome to RingPlan!" but where Salam is only Cc and Olga is running it.
>   2. **`tier1:bid-invitation`** - `BID_INVITATION` (ad for bids / invitation to bid / notice to
>      bidders / RFP|RFQ|RFI|ITB / bid date-due-opening-package / prequalification). Deliberately
>      NOT gated on `to_me`, because plan rooms mail these to shared addresses. **For a contractor
>      these are inbound revenue - the worst possible thing to file as junk.**
>   3. **`tier1:pending-on-us`** - `PENDING_ON_US` ("assuming the deposit gets sent", "pending your
>      signature", "once you", "as soon as we", "waiting on you", "needs your approval"). This is
>      the Updated Drawing case (David Jenkins, whequip): "I would expect it around 3 weeks from now,
>      assuming the deposit gets sent soon." No question mark, so `answered-statement` demoted it and
>      **the model agreed and said fyi**. The deposit was still ours to send. Now deterministic - the
>      only one of the 10 where the RULES were right and the MODEL was wrong.
>   4. **`tier1:financial-record`** - receipts/invoices/statements -> **fyi at rank 40, not Cleanup**.
>      Money records are informational but never junk; an owner wants to see what the company was
>      charged. Anything past-due or declined is promoted by rule 1 instead, which is checked first.
>      (The Anthropic $175 receipt was hitting `BULK_SUBJECT`'s "your receipt".)
>   5. **`tier1:cc-bystander`** - Cc'd, never To, never spoke, on a thread with a real
>      back-and-forth -> fyi. The owner's common case: Olga runs the vendor install, Salam rides 8
>      messages on Cc.
> - **`has_conversation` signal (`we_spoke or msg_count > 2`) now blocks all three bulk rules.**
>   **Bulk mail does not hold conversations.** This is the cheapest available guard and it is what
>   kept the 8-message RingPlan/ztelco project thread from being filed as a newsletter because its
>   subject contained "Welcome to".
> - **`cold` REDEFINED: was `msg_count == 1`, now `not has_conversation`** (nobody here replied AND
>   it is not a real thread). The old test missed every salesperson who bumps their own email -
>   **two unanswered messages from a stranger is MORE cold, not less**, but the bump promoted Justin
>   Bullerjahn's pitch into Bob's reply lane. Fixed at the signal level, so it is company-wide.
> - **2 of the 10 were already fixed** by the morning's colleague-first-message bug: Bob's "T&M
>   TICKET TEMPLATE -> Here you go Boss" and Ethan's "Chase Banking Cards" briefing to Salam. Both
>   were internal senders opening a thread, both got labelled "colleague replied - verify it covers
>   what was asked of you", both now land in needs_reply via `score:evidence`.
> - **A LIMITATION THE BATCH EXPOSED (not fixed, by design):** the Chase Banking Cards mail ends
>   "Thoughts on this?" - roughly 1,800 characters in. `_SNIPPET_CAP` is 300, so `asks` is FALSE for
>   it. **A question at the end of a long email is invisible to the engine.** That case still lands
>   correctly because `internal` is independent positive evidence, but a long external email whose
>   only ask is in the last line would not. This is the cost of the no-bodies rule and it is worth
>   keeping; raising the cap raises per-row storage for every thread to fix a minority case.
> - **Bob's cold-sales case is the clean illustration of why per-user rules exist.** With his legacy
>   tier-2 set the thread reaches `tier2:cold-outreach -> model -> fyi`. On a FRESH inbox that rule
>   is off, so the same thread scores `to_me + asks` and lands in needs_reply. Both are in
>   `cases.json` as a matched pair. Bob's stated preference ("doesn't want to reply to salespeople")
>   is a per-user rule, not a company default - a sales rep needs the opposite.
> - **Test state: 31/31 cases + 7 invariants + 12 suggester checks, all passing.** Rule registry is
>   now 22 (3 structural / 10 tier 1 / 9 tier 2), 29 signals. `tests/test_triage.py` gained
>   per-case `user` support (salam/bob/ethan - whose mailbox a thread sits in changes the verdict,
>   since internal-domain and first-name matching key off it) and `"active_rules": "tier1"` to
>   assert that a verdict DEPENDS on an opt-in tier-2 rule.
> - **Still not deployed, and one deploy step matters:** fixing the ack regex does NOT retroactively
>   un-hide the threads the old rule already resolved, and a FREE rules-only re-run will not either
>   (a thread that recomputes to `model` gets its prior verdict restored rather than being stranded
>   in a state no lane renders). **After deploying, run `POST /api/email/retriage?model=true` once
>   per mailbox** (~1-2 cents each) to pull back anything the old ack rule swallowed. A plain
>   Refresh will not do it.

> - **SECOND PASS, same day - the phrase rules were not general enough. A no-reply address is now
>   WEAK EVIDENCE and never sufficient for Cleanup on its own.** Ethan's push-back was correct: a
>   phrase list is still a list, and `DEMANDS_ACTION` would have missed "your certificate of
>   insurance expires 10/01 - upload a replacement through the vendor portal" (no pattern matches),
>   sending it to Cleanup exactly like the Paychex notice. Per-item rules do not scale at a company
>   that runs on email.
> - **The principle, which this engine already applied elsewhere and I missed here:** the sender
>   address tells you how the mail was SENT, not what it SAYS. `to_me` alone was ruled insufficient
>   for the reply lane back in August for the same reason. `bulk_sender` alone is now insufficient
>   for Cleanup. CONTENT signals stay sufficient on their own, because they are much stronger:
>   unsubscribe copy is legally required on marketing mail, and "verification code" is a subject
>   only a machine writes.
> - **Three outcomes for an automated sender, layered:**
>   1. no-reply + obligation language -> **needs_reply** (`tier1:action-required`)
>   2. no-reply + unsubscribe copy OR machine subject -> **bulk / Cleanup** (`tier1:bulk-sender`,
>      which now REQUIRES `bulk_copy or bulk_subject`)
>   3. no-reply + nothing else -> **fyi at rank 20** (`tier1:automated-notification`)
>   Case 3 is the important one: it is the catch-all for obligations worded in ways the phrase list
>   does not know yet. **Being wrong there costs a row near the bottom of "Worth knowing" instead of
>   a message nobody ever sees again.** This fixes 4 of the 6 reported bulk misfires on its own,
>   independent of any phrase matching.
> - **Deliberate expectation change:** the "no-reply sender" fixture (Concur, "Expense report
>   submitted / Your report was received") moved from `bulk` to `fyi` via
>   `tier1:automated-notification`. That is the new intended behavior, not a regression.
> - **8 generalization cases added, none from a reported email** (CSLB license renewal, workers-comp
>   premium audit, GC W-9 request, BuildingConnected ITB with an unsubscribe footer, city permit
>   status, COI expiry) plus **2 PRECISION cases** proving Cleanup still works (a Surfaces Expo
>   marketing blast and a RollMaster verification code both still land in Cleanup via the
>   two-signal path). This is what shows the fix is structural rather than scoped to the senders
>   that were reported. **39/39 cases pass.** Registry is now 23 rules (3 structural / 11 tier 1 /
>   9 tier 2), 29 signals.
> - **`DEMANDS_ACTION` also broadened** to cover "please complete/fill out/sign/submit (and return)"
>   and "complete/return/sign the attached" - the W-9 and audit-worksheet pattern, which is constant
>   for a contractor and was not matched by the deadline-oriented phrasings.
> - **EXPECTED SIDE EFFECT, worth measuring after deploy: the Cleanup lane shrinks and "Worth
>   knowing" grows.** Ethan's mailbox had ~34 in Cleanup; every one whose only bulk evidence was
>   the From address now moves to fyi rank 20. That is the intended trade (visible-but-low beats
>   hidden), but it should be checked rather than assumed. `decided_by` makes it a one-line query:
>   `SELECT decided_by, state, COUNT(*) FROM email_threads WHERE user_id = '<id>' GROUP BY 1,2
>   ORDER BY 3 DESC;` If `tier1:automated-notification` is enormous, the two-signal threshold is
>   the knob to revisit - and any single sender that dominates it is a one-click user rule.

> - **THIRD PASS (from Ethan's live UI screenshots) - SNIPPET WAS SHOWING THE SENDER'S SIGNATURE.**
>   Both forwarded rows in the reply lane displayed "📍 NEW ADDRESS - Effective immediately: Mailing:
>   4620 Alvarado Canyon Rd., Suite 18..." instead of the email. Cause: `_fresh()` split at the quote
>   divider and took the HEAD, and on a forward where the sender typed no note the head IS the
>   signature. **Graph caps `bodyPreview` at 255 characters and Star's signature block is 200+ of
>   them** (address banner + title + "Celebrating 47 Years"), so the signature consumed the entire
>   preview. `_SNIPPET_CAP = 300` never bites; Graph's 255 does.
> - **Why that mattered more than cosmetics:** the snippet is not just display. It feeds `asks`,
>   `named`, and the model prompt. So the engine was reasoning about a mailing address, and a human
>   could not judge the row well enough to correct it - which would have corrupted the very
>   correction data the rollout exists to collect.
> - **Fix, in `triage_engine.fresh()` (now the SINGLE implementation - `email_triage._fresh`
>   delegates to it, since two copies would have drifted the moment either was tuned):**
>   1. **Signatures are stripped** via `_SIGNATURE_START`: the `--` delimiter, the NEW ADDRESS
>      banner, "Best/Kind/Warm regards", "Very Respectfully" (Bob's), "Celebrating N Years in
>      Business", "Sent from my <device>", "Get Outlook for <os>".
>   2. **A bare forward falls back to the forwarded text.** If the new text is under 20 chars after
>      stripping, read below the divider instead and drop its From/Sent/To/Cc/Subject/Date header
>      lines. Verified against the real Paychex forward: the snippet went from the address banner to
>      "You must print, review, and indicate your approval by physically signing page 14."
> - **"Thanks," and "Regards," are DELIBERATELY NOT signature markers.** Cutting there would
>   recreate the ack bug that silently hid real work - "Thanks, can you also send the invoice?" is
>   content. Two new invariant tests pin this: `test_signature_is_stripped_from_the_snippet` and
>   `test_signature_strip_does_not_eat_a_real_ask`. **39 cases + 9 invariants pass.**
> - **THIS FIX NEEDS A FULL RE-BACKFILL, not a re-triage.** Snippets are built at sync time in
>   `_compact`, so `retriage` (which recomputes signals from the STORED snippet) cannot fix existing
>   rows, and delta sync will never return an unchanged message. To rebuild snippets for a mailbox:
>   `UPDATE email_sync_state SET inbox_delta = '', sent_delta = '' WHERE user_id = '<id>';` then hit
>   Refresh - that forces the full 30-day re-fetch (~80s, ~60-70 Haiku verdicts, 1-2 cents).
>   **CONVENIENTLY THIS ALSO FIXES THE ACK PROBLEM** (everything gets re-triaged with the model
>   pass), so it replaces the `retriage?model=true` step in the note above. One operation, both
>   problems.
> - **Two adoption changes, because a feedback loop nobody notices collects nothing:**
>   1. The row's move control was a bare 13px icon between the external-link and the X. It now reads
>      **"⤴ MOVE"**, matching the existing "Restore" button style. Discoverability IS the feature
>      here - most of the users being rolled out to are sales and field ops, not developers.
>   2. A one-time hint above the lanes ("Something in the wrong lane? Hit Move on the row...")
>      that clears on the user's first correction, keyed to `localStorage.starEmailTaughtMove`.
> - **NEW: `GET /api/email/corrections/summary?days=30` - company-wide rule telemetry.**
>   AGGREGATE COUNTS ONLY: no senders, no subjects, no message content, no user identities (email is
>   per-user private by design, so this returns nothing that could reconstruct anyone's mail).
>   Returns `byRule` ranked by correction count with each rule's label and tier, `transitions`
>   (`fyi -> needs_reply` etc.), `reasons` (the why-chip histogram), and `distinctMailboxes`.
>   **This is the number that decides company vs per-user:** a rule with 12 corrections against it
>   across 4 mailboxes is a bad DEFAULT and belongs in the registry, not in twelve people's personal
>   rule lists. Credits the rule that ROUTED a thread, unwrapping `model(via <rule>)`.
> - **Confirmed working live by Ethan (2026-09-02):** forwarded Salam mail correctly promoted to the
>   reply lane; on Refresh a thread he had replied to moved itself into Handled with no action from
>   him (`structural:you-spoke-last`). Counts on his mailbox at that moment: All 111 = 9 reply / 71
>   worth knowing / 31 cleanup, plus 32 handled (17 replied, 15 closed themselves).
> - **THE VOLUME NUMBER TO WATCH: "Worth knowing" was 71 of 111 visible rows.** That is the
>   predicted side effect of demoting single-signal automated mail out of Cleanup, and it is a real
>   usability risk - nobody corrects mail they cannot find. Not addressed yet; the cheap options are
>   (a) render the top ~25 with a "show all" toggle, (b) sub-group fyi by category
>   (customer/vendor/internal/notification), or (c) raise the bar for what reaches fyi at all.
>   Decide with `corrections/summary` data rather than by guessing.

> - **FOURTH PASS - lane cap + digest control removed (Ethan's calls, 2026-09-02).**
> - **WHY SIGNATURES CANNOT BE SOLVED VIA GRAPH (answered, worth not re-asking):** there is no
>   signature field in MIME or in Graph - a signature is ordinary body text. **Ethan's Outlook
>   screenshot is the proof:** Outlook's own preview line shows "📍 NEW ADDRESS - Effective..." on
>   the exact same four forwards. Outlook has identical data and makes an identical call, so the
>   heuristic in `triage_engine.fresh()` is now BETTER than what the mail client does, not a
>   workaround for a gap on our side. Two partial exceptions: `-- ` (RFC 3676 dash-dash-space) is
>   the nearest standard and is matched, though Outlook never emits it; and **`uniqueBody`** is the
>   Graph field that would help - it strips quoted thread history server-side, more reliably than
>   our `From:`/`________` split - but it is **not supported in `$select` on a collection** (one GET
>   per message: ~330 extra Graph calls per backfill instead of a few delta pages) AND it would
>   still not strip signatures, since the sender's signature IS their unique contribution. Fixes the
>   smaller half of the problem at large cost; rejected.
> - **Future option if the regex starts leaking: detect boilerplate by REPETITION.** A signature is
>   text recurring across many messages from the same sender, and per-sender trails are already
>   stored. That would learn Star's block plus every vendor's with no patterns written by hand. Real
>   machinery, so only worth it if the heuristic proves insufficient.
> - **LANE DISPLAY CAP (`LANE_CAP = 25`).** Ethan's mailbox ran **71 of 111 visible rows in "Worth
>   knowing"**, and his framing settles the fix: *"I don't really get any spam or trash so almost all
>   of my inbox is fair as fyi."* **The classification is CORRECT - the problem is the view, so do
>   NOT re-tune rules to shrink fyi.** Any lane over 25 now renders its top slice with a
>   "Show all N - N-25 more below the top 25" footer (`LaneMore`). Three details that matter:
>   the cap runs AFTER the sort comparator, so "top 25" means top 25 by the ACTIVE sort (importance
>   by default, newest if switched) and the button names it; capping is SKIPPED while searching,
>   because a search is the user narrowing things down already; and expansion is session-only state,
>   so every visit starts tidy.
> - **Digest control is now a DEAD LABEL, not a switch.** It read "Daily digest email [toggle]
>   COMING SOON" and was flipped ON in Ethan's screenshot - a preference that stores fine and then
>   sends nothing for a week is a trust cost on the feature we most need people to trust. Now plain
>   text + a "coming soon" chip. `toggleDigest` and `user_prefs.digest_enabled` are BOTH still wired
>   and the function is kept (with an eslint-disable and a comment), so restoring the switch the day
>   Mail.Send + the Container Apps Job land is a one-line change.
> - **Ethan is dismissing his own Salam triage-test forwards** - their subjects carry his
>   annotations ("Cleanup -> action required"), so correcting them would train the suggester on
>   synthetic mail.
> - `npm run build` clean (1761 modules, 497 KB). Test suites unchanged and still green: 39 cases,
>   9 invariants, 12 suggester checks.

> - **FIFTH PASS - `POST /api/email/resync` replaces the psql delta reset, and a REAL BUG it
>   exposed: manual corrections did not survive a sync.**
> - **The bug:** only `_retriage` skipped `decided_by == "user:manual-move"`. `sync_user` did NOT,
>   so re-touching a thread - a full resync, or ANY sync where that thread changed - silently
>   reverted the user's hand-filed verdict. That is the single most trust-destroying thing this
>   feature could do, and it was one delta reset away from happening to all three pilot mailboxes.
> - **Fix: a MANUAL HOLD in `_triage_thread`, mirroring the dismiss semantics.** New additive column
>   `email_threads.manual_msg_id` stamps the message id the user's verdict was made against
>   (set in `reclassify_thread`). While `manual_msg_id == last_message_id` the engine refreshes
>   `signals_json` (so a later correction is recorded against current facts) but LEAVES the state,
>   rank, reason and decided_by alone. A genuinely new message on the thread releases the hold, same
>   as a dismiss. `_retriage`'s early `continue` stays as a cheap short-circuit; the hold in
>   `_triage_thread` is the real guarantee, since it covers the sync path too.
>   **4 new checks in `test_suggester.py` pin all of it** (recorded with the right msg id / resync
>   does not revert / signals still refresh under the hold / new message releases it). 16 checks
>   pass.
> - **CLARIFYING WHAT WAS AND WAS NOT AT RISK (Ethan asked whether resync would have deleted user
>   RULES - it would not, and the separation he suggested already exists):**
>   - **NEVER touched by sync/resync/retriage:** `triage_rules` (per-user hard + soft rules),
>     `triage_feedback` (the correction snapshots), `triage_suggestions`, and
>     `user_prefs.triage_rules_json` / `triage_profile`. Sync only ever READS `triage_rules` -
>     in `_user_rules()` to evaluate them and in `_bump_rule_hits()` to increment `hit_count`.
>     It has no delete or overwrite path to any of them.
>   - **Rewritten by sync/resync:** `email_threads` (the mail + its verdict) and
>     `email_sync_state` (the delta tokens, cleared deliberately). That is the whole blast radius.
>   - So the bug was narrower than "corrections are lost": a hand-moved thread's LANE lived in the
>     volatile table with no protection. **The durable path was always safe** - a correction that
>     had been promoted into an accepted rule would simply re-apply on resync and produce the right
>     lane again, and `triage_feedback` kept the evidence either way, so the suggester never lost
>     learning data. What reverted was the one-off, not-yet-ruled override.
>   - **This is the intended architecture, now correct rather than merely intended:** durable intent
>     belongs in `triage_rules`; a manual move is a single-thread override that holds until the
>     thread actually changes. `manual_msg_id` is the only schema addition needed - no separate
>     table, because the rules already have theirs.
> - **Why resync is an ENDPOINT and not a documented SQL statement:** some fixes change how a
>   message is PARSED rather than how it is judged - `fresh()` signature stripping is the first and
>   there will be more. Stored snippets are built at sync time, so `retriage` (which reads the
>   stored snippet) cannot reach them, and Graph delta never returns an unchanged message. The only
>   route is re-fetching. Ethan could not run the psql one-liner (no native `psql` - the Aug DB
>   migration used the `postgres:18` Docker image for exactly that reason - and it was written with
>   bash `$DATABASE_URL`, not PowerShell `$env:`). Per-mailbox SQL also does not scale past three
>   pilot users and cannot be handed to a non-technical one. So: one authenticated call, no DB
>   access. Clears `email_sync_state.inbox_delta`/`sent_delta`, re-runs `sync_user`, returns the
>   overview with `fullResync: true`. ~80s + 1-2 cents vs ~19s for a normal Refresh.
> - **UI: a "Rebuild from Outlook" button in the Rules tab's new MAINTENANCE section** - not beside
>   Refresh, where it would get mis-clicked. Copy states the cost and that hand-moved threads stay
>   put.
> - **THIS REPLACES BOTH earlier deploy instructions** (the `retriage?model=true` step AND the
>   `UPDATE email_sync_state ...` SQL). After deploying: open the Email tab -> Triage rules ->
>   Rebuild from Outlook, once per pilot mailbox. It rebuilds snippets, re-triages with the model
>   pass (recovering anything the old ack rule swallowed), and preserves corrections.
> - **PowerShell note for anything like this in future:** `$DATABASE_URL` is bash; PowerShell wants
>   `$env:DATABASE_URL`. And there is no `psql` on this machine - reach for the project venv
>   (`backend/.venv/Scripts/python.exe`, which loads `backend/.env` through `database.py`
>   automatically) or the `postgres:18` Docker image, not a bare `psql`.

> - **RESEARCH CLOSED: "open the row in the Outlook DESKTOP app" is NOT POSSIBLE for New Outlook.**
>   Do not re-derive this. Ethan asked for it pre-deployment 2026-09-02; diagnosed on his machine.
> - **What his machine actually runs (read from the registry / appx manifest, not assumed):**
>   - `olk.exe` from `Microsoft.OutlookForWindows_1.2026.818.100_x64__8wekyb3d8bbwe` = **NEW
>     Outlook**. **Classic `OUTLOOK.EXE` is NOT INSTALLED** (absent from
>     `HKLM\...\App Paths\OUTLOOK.EXE`).
>   - The `outlook:` protocol is registered NOWHERE (`HKCU\Software\Classes\outlook` and
>     `HKCR\outlook` both absent). **That is why Win+R `outlook:inbox` offered a Microsoft Store
>     search and the Store found nothing** — Windows offers a Store lookup for ANY unregistered
>     scheme, and no Store app registers `outlook:`. It was never an "Outlook is missing" problem.
>   - New Outlook's appx manifest registers protocols **`ms-outlook`, `mailto`, `webcal`,
>     `webcals`** and declares `windows.appUriHandler` for **outlook.office.com,
>     outlook.office365.com, outlook.cloud.microsoft**.
> - **THE VERDICT PER CLIENT:**
>   | Client | Per-message desktop deep link? | How |
>   |---|---|---|
>   | Classic Outlook (Win32) | YES | `outlook:<hex EntryID>`, invoked `OUTLOOK.EXE /select "outlook:<hex>"` |
>   | **New Outlook (`olk.exe`)** | **NO** | Microsoft ships no URI scheme for a specific message on Windows |
>   | Outlook mobile (iOS/Android) | YES | `ms-outlook://emails/message?id=...` |
> - **Why New Outlook cannot do it, two independent reasons:** (1) EntryID is a **MAPI** construct
>   and New Outlook has **no MAPI and no COM** at all - it is a web wrapper; (2) `ms-outlook://`
>   message deep links are an **iOS/mobile-only** feature - there are Microsoft Q&A threads titled
>   exactly "ms-outlook:// URL scheme does not work for Windows". Microsoft's own answer: *"there
>   currently isn't a direct URI scheme method to open a specific email in the Outlook desktop
>   app"* (Power Automate is the only workaround they name).
> - **The appUriHandler is a DEAD END from our app, and the reason is worth remembering: app URI
>   handlers do not fire on link clicks made INSIDE a browser.** Chrome/Edge already hold the URL
>   and open a tab; they never ask Windows whether an installed app claims that host. App URI
>   handlers fire when the OS resolves a URL (Run dialog, Start, a non-browser app). Custom SCHEMES
>   are the opposite - browsers hand unknown schemes to the OS - which is why `outlook:` works for
>   Classic and `https://outlook.office365.com/...` never will.
> - **CORRECTION to an earlier claim in this session:** registering the `outlook:` protocol does
>   NOT require admin / DataNet. `HKEY_CURRENT_USER\Software\Classes\outlook` is merged into
>   HKEY_CLASSES_ROOT for that user and HKCU needs no elevation. The Intune/MOTW comparison was
>   wrong. (DataNet would still be the right route for FLEET-wide consistency, and Ethan confirmed
>   they are willing - they already scoped his starbot requests - but they cannot fix New Outlook;
>   that is a Microsoft product gap.)
> - **SHIPPED INSTEAD (1 line, works on every client): the row anchor uses a NAMED target
>   (`target="starbotMail"`) rather than `_blank`,** so every row reuses ONE Outlook tab instead of
>   stacking one per click. Working a 9-thread reply lane used to leave 9 tabs open. This addresses
>   the actual daily annoyance for New Outlook and mobile users, who cannot be given the desktop app.
> - **IF the Classic path is ever wanted** (only worth it if a real share of users are on Classic -
>   note Ethan himself is on New Outlook and so CANNOT test it on his own machine): per-user
>   registry registration, no admin -
>   `New-Item "HKCU:\Software\Classes\outlook\shell\open\command" -Force`, set `(default)` =
>   `URL:Outlook Protocol` and `URL Protocol` = `""` on the parent, and the command to
>   `"<OUTLOOK.EXE path>" /select "%1"` (get the path from
>   `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE`). Then the code side:
>   batched `POST /me/translateExchangeIds` at sync (`sourceIdType: restId`, `targetIdType:
>   entryId`, up to 1000 ids per call, so ONE extra POST per sync), **convert the returned
>   URL-safe-base64 entryId to HEX** (the protocol wants hex, Graph returns base64url), store the
>   hex on the thread row (~200 bytes), and gate it behind a per-user preference defaulting OFF so
>   New Outlook and phone users keep the OWA link and nobody gets rows that silently do nothing.
> - **Strategic note:** Microsoft is moving everyone to New Outlook (Classic supported to 2029 but
>   the direction is set), so the payoff on Classic-only deep linking shrinks every quarter. Weigh
>   that before spending the hour.

> - **CORRECTIONS to the Outlook-deeplink note above (Ethan asked for sources; two claims did not
>   survive checking).** Keep these straight, because the Classic path IS worth building later.
>   1. *"Microsoft's own answer: there currently isn't a direct URI scheme method..."* was a SEARCH
>      ENGINE PARAPHRASE, not a quote. The real verbatim answer is weaker: MS moderator Katherine,
>      2024-09-18, *"I tested it for you on my side and it seems that now we can only do this by
>      PowerAutomate."* A support-forum moderator, not documentation.
>      (learn.microsoft.com/en-us/answers/questions/4661350)
>   2. *"`ms-outlook://` message links are iOS/Android only"* was **UNSOURCED INFERENCE**. The
>      Windows thread (Faery Fu-MSFT, 2023-09-19,
>      learn.microsoft.com/en-us/answers/questions/1371335) only says `ms-outlook://` did not launch
>      Outlook on Windows and points at a registry workaround. **It never discusses message ids.**
> - **OPEN, UNTESTED AVENUE worth 30 seconds someday:** that 2023 thread PREDATES New Outlook, and
>   `olk.exe`'s own appx manifest registers the **`ms-outlook`** protocol - so the scheme IS live on
>   a New Outlook machine today. Whether it accepts a message identifier is untested by anyone in
>   those threads. Probe: Win+R -> `ms-outlook://` (does olk.exe come up?), then try a message form.
>   What IS still solid: New Outlook has no MAPI/COM (Microsoft's own documented feature gaps), so
>   the `outlook:<hex EntryID>` route is genuinely impossible there regardless.
> - **CLIENT MIX (Ethan, 2026-09-02) - this changes the calculus in favour of building the Classic
>   path:** *"I honestly imagine most of my users will stay on classic outlook until they simply
>   can't. I at least know my office staff, management and boss use classic."* So Salam, the office
>   staff and management are on **Classic** - i.e. the users who most need the reply lane are
>   exactly the ones `outlook:<hex EntryID>` would serve. Ethan himself is the New Outlook outlier,
>   which is only a testing inconvenience, not a reason to skip it. **Downgrade the earlier
>   "payoff shrinks every quarter" note accordingly** - still true long-term, but the near-term
>   audience is real. Ethan is also fine bringing DataNet in (they already scoped his starbot
>   requests), which makes fleet-wide registry deployment straightforward.
> - **REVERTED: the named-tab change.** Rows go back to `target="_blank"` (a new tab each).
>   Ethan's reasoning, which is better than mine: the OWA deeplink renders that ONE message with no
>   inbox around it, so reusing a single tab destroys the message you were reading. Stacking lets
>   you open several, return to Starbot, and decide what to do next. A comment in `Email.jsx` says
>   not to "fix" this into a shared tab again.

> **Resume note (2026-09-02) - TRIAGE ENGINE SPLIT (signals + decide) + PER-USER RULES + FEEDBACK LOOP. Built + tested, NOT committed/deployed.**
> - **Why the rewrite:** `_triage_deterministic` was a cascade of **12 early returns**, so (a) rule
>   ORDER silently decided outcomes (recorder-bot ran before the hit-list account check, so a real
>   customer whose sender name matched went to Cleanup at rank 5), (b) rules SHADOWED each other
>   ("Ticket #4412 has been resolved" hit the ticket rule and went to the model, so the closure rule
>   written for exactly that case never ran), and (c) nothing recorded WHY, so "this was classified
>   wrong" could not be traced to the rule that did it. The `strong` predicate was 9 interacting
>   booleans and threw its evidence away on the losing branch, so the audit line lied about why a
>   thread landed where it did.
> - **New shape - `backend/triage_engine.py`:** `collect_signals()` observes everything and NEVER
>   returns early (23 signals, stored per thread as `email_threads.signals_json`, ~250 bytes);
>   `decide()` walks a priority-ordered rule list as DATA and stamps `email_threads.decided_by`
>   with the rule that fired. Precedence, stated once: **structural -> user hard rules -> tier 1 ->
>   tier 2 (if enabled) -> deterministic scoring -> model.**
> - **THE TIERING DECISION (Ethan, 2026-09-02): do NOT ship the 12 tuned rules into new inboxes.**
>   All of them were tuned against ONE IT-admin mailbox, and inheriting that gives a salesperson an
>   IT admin's idea of what matters (demoting cold outreach is right for IT, wrong for sales whose
>   job it is). Three tiers, all 17 rules kept in the registry as a log:
>   - **STRUCTURAL (3, always on, not editable):** empty thread, note-to-self, you-spoke-last. Facts
>     about thread shape, not opinions - no rule should claim you didn't send a message you sent.
>   - **TIER 1 (5, default on, user may disable):** bulk sender / unsubscribe copy / notification
>     subject / out-of-office / explicit closure. Mechanical automation detection - nobody should
>     have to teach the bot that a newsletter is a newsletter.
>   - **TIER 2 (9, seeded but INACTIVE on a new inbox):** recorder-bot, ticket-update, ack,
>     colleague-covered, colleague-verify, replied-elsewhere, cold-outreach, robot-sender,
>     answered-statement. Offered per-user once that user's own corrections corroborate them.
>   - **Migration rule (no hardcoded emails): a mailbox that ALREADY has triaged threads keeps the
>     rules that produced it; a brand-new mailbox starts clean.** `email_triage.ensure_rule_set()`
>     runs once per user at the top of `sync_user` and writes `user_prefs.triage_rules_json`.
> - **THREE REAL BUGS FIXED (two were latent in production):**
>   1. **`_ACK` swallowed real work.** `^(got it|thanks|...)[^?]{0,80}$` let 80 free characters
>      follow the ack word, so **"Thanks Ethan, also send the invoice to Marc."** matched -> resolved,
>      rank 0 -> **and resolved threads were never rendered, so it was invisible AND uncorrectable.**
>      Narrowed to ack word + optional name + optional short closing clause.
>   2. **`_RECORDER_BOT` had no word boundary** - "Bread AI", "Thread Assistant", "Spread Support"
>      all matched and went to Cleanup at rank 5. Anchored with a word boundary.
>   3. **"colleague replied" fired on a colleague's FIRST message.** The old test was only "internal
>      and last inbound is not me", so a colleague opening a thread with a direct question ("can you
>      get me the mileage sheet today?") was labelled "colleague replied - verify it covers what was
>      asked of you" and sent to the model instead of straight to the reply lane. Missing precondition
>      was `msg_count > 1`. **The eval harness caught this on its first run.**
> - **Also fixed by the restructure:** tier-1 closure now runs BEFORE the tier-2 ticket rule, so a
>   closed DataNet ticket resolves deterministically instead of burning a Haiku call and depending
>   on the model getting it right.
> - **EVAL HARNESS (`backend/tests/`) - the missing safety net.** `test_triage.py` + 21 fixture cases
>   in `fixtures/cases.json` (every "don't relearn these" case from the 2026-08-21 note, expressed as
>   data) plus 7 invariant checks. Dependency-free: `collect_signals`/`decide` are pure, so no
>   Postgres, no Graph, no model, no tokens. **Run it: `cd backend && python tests/test_triage.py`.**
>   `test_suggester.py` covers the full correction -> suggestion -> rule loop against a SQLite
>   in-memory DB injected in place of `database` (NEVER touches prod). Both suites pass (21/21 + 7,
>   and 12/12). **When a worker reports a wrong verdict, add it to `cases.json` with the lane it
>   belonged in** - corrections become regression tests instead of one-off regex edits.
> - **PER-USER RULES + THE LEARNING LOOP (Ethan's design, 2026-09-02):**
>   - **Correction = moving a thread between lanes.** Row-level "move" control (CornerUpRight icon)
>     -> panel with the other lanes + optional one-tap WHY chips (Not a task / Not my area / Already
>     handled / Misread the sender / This IS a task). `POST /api/email/threads/{id}/reclassify`.
>   - **`triage_feedback` stores a SNAPSHOT, not just the action** - the signal set, `decided_by`,
>     predicted state/rank, sender, domain, subject. The action alone cannot be turned into a rule;
>     without the signals a suggester can only pattern-match on sender and produces shallow rules.
>     No FK to email_threads on purpose: threads prune at 90 days, the lesson shouldn't.
>   - **Two rule kinds:** `hard` (deterministic, matched in `decide()`, free + instant + re-runnable)
>     and `soft` (plain-language judgment line injected into the Haiku prompt AFTER the company
>     instructions so a user's own instruction wins a disagreement).
>   - **"About your job" paragraph** (`user_prefs.triage_profile`) rides into the prompt. For the
>     judgment path this outperforms a stack of rules - "I'm IT admin, DataNet tickets are my actual
>     work" flips a large share of one person's verdicts on its own.
>   - **SUGGESTER: 3+ corrections sharing a pattern -> a proposed rule, approval ALWAYS required.**
>     One correction is a one-off, two is a coincidence. Four candidate shapes, most specific first:
>     sender, whole domain (only when 2+ different senders at it), **"the rule X keeps being wrong,
>     turn it off"** (the highest-value one - retires a bad default for one person instead of leaving
>     them to fight it thread by thread), and signal-across-3+-senders. Rejected patterns are
>     remembered and NEVER re-proposed. "Already handled" is excluded from generalizing at all - it
>     is a statement about that thread's history, not about the sender.
>   - **Rules tab** ("Triage rules" button beside the filter chips, `frontend/src/EmailRules.jsx`):
>     suggestions first, then your rules (with hit counts + "never matched" flags + delete/toggle),
>     then About your job, then ALL built-in rules grouped by tier with toggles - structural ones
>     shown but locked, because a rule you cannot see is a rule you cannot trust.
>   - **Re-run on save:** hard-rule edits trigger a FREE deterministic replay over stored threads
>     (`_retriage(run_model=False)`, no Graph, no tokens) and the UI reports "N threads re-sorted".
>     `POST /api/email/retriage?model=true` also re-judges the ambiguous remainder (~1-2 cents for a
>     30-day mailbox) - needed for soft rules to take effect on existing threads. Two safeguards: a
>     thread the user moved by hand (`decided_by == "user:manual-move"`) is NEVER re-decided, and a
>     rules-only pass leaves a thread alone rather than stranding it in state "model" (not a lane).
> - **NEW: "Handled" lane** (collapsed by default). Resolved/waiting threads were counted but never
>   rendered, so the engine's worst failure - a real ask silently hidden - generated ZERO feedback.
>   Now recoverable and correctable. Their absence from the lanes above is still the feature.
> - **Files:** NEW `backend/triage_engine.py`, `backend/triage_feedback.py`, `backend/tests/`
>   (test_triage.py, test_suggester.py, fixtures/cases.json), `frontend/src/EmailRules.jsx`.
>   CHANGED `backend/email_triage.py` (cascade -> `_triage_thread` + `_user_rules` +
>   `ensure_rule_set` + `_bump_rule_hits`; model prompt takes per-user guidance and the per-thread
>   hint; `_overview` gains the handled lane), `models.py` (TriageRule / TriageFeedback /
>   TriageSuggestion + `email_threads.signals_json`/`decided_by` + `user_prefs.triage_rules_json`/
>   `triage_profile`), `schemas.py` (ThreadReclassifyIn / TriageRuleIn / TriageProfileIn),
>   `main.py` (DDL + router), `frontend/src/api.js` (9 helpers), `frontend/src/Email.jsx`.
> - **Verified:** both test suites pass; all backend modules import clean; 9 new routes present;
>   `npm run build` clean (1761 modules, 496 KB). **NOT verified against a live mailbox** - no
>   uvicorn was started (Ethan's own dev servers may own :8000/:5173) and the local `.env` points at
>   PRODUCTION Postgres. Nothing was run against prod.
> - **NEXT (in order):** (1) Ethan reviews + commits + deploys (acr build + containerapp update).
>   (2) Feed the worker feedback batch into `fixtures/cases.json` as labelled cases and re-run the
>   scorer - that is what turns their corrections into permanent regression tests. (3) On first
>   deploy, existing mailboxes auto-seed the legacy tier-2 set (verify in the Rules tab), new ones
>   get tier 1 only. (4) Still pending from before: Mail.Send + Container Apps Job = digest phase 2
>   complete; then phase 3 (autodrafts + move-to-trash, needs Mail.ReadWrite), meeting-minutes tab,
>   product catalog, mileage purpose field.

> **Resume note (2026-09-01) - Email refinements + phase-2 digest content SHIPPED (rev starbot--emailv2, commit 1d9f945). Digest still not SENDING (Mail.Send).**
> - **Live:** commit `1d9f945` on image `starbot:email-refinements` (build `ds1g`, 62s), revision
>   `starbot--emailv2` at 100% traffic. Rollback target `starbot--newdbpass` on `starbot:shared-boards`
>   kept Active at 0%. Cron `business-hours` scale rule survived the update (verified via `containerapp
>   show`). Identity check confirms new code: `/api/email/digest/preview` returns 401 (new route auth-
>   gated, not 404). Health 200 in 1s warm.
> - **What's live for the team:**
>   - Email tab: filter chips (All / Reply / FYI / Cleanup / Dismissed), search bar, **sort toggle
>     (Importance/Newest/Oldest)** \[added today\], dismiss + **bulk-dismiss w/ sticky action bar**,
>     importance dot on rank >= 65
>   - **Cross-thread reply detection (added today):** if user has sent to sender-X in ANY other thread
>     after our thread's last inbound, demote to fyi with reason "you may have replied in another thread
>     — verify". Cheap DB scan via `_build_outbound_map()` (precomputed per sync, no Graph, no tokens).
>     Requires trail records with `to` field — old records skipped silently, new syncs backfill going
>     forward. Ethan confirmed today it correctly demoted his real 30-day false-positive case.
>   - Triage engine refinements: ticket-system rule (DataNet ticket updates -> fyi unless direct
>     question), recorder-bot filter (Read.ai / Fathom / Otter -> cleanup), cold outreach -> fyi (still
>     visible, not tasked), robot-sender demotion (support@, no-reply, contact@), ack fast-path ("got it,
>     thanks!" -> resolved), statement-answered routing, model prompt tightened (defaults to fyi on
>     uncertainty), fail-SAFE not fail-open (model batch failure -> fyi, was needs_reply)
>   - Waiting-on-them lane REMOVED (Ethan: "if I already replied it's not a task"). "N handled" split
>     into "N you replied · N closed on their own" for transparency.
>   - Starbot: `list_ranked_emails` + `get_email_thread` tools (22 total). Chat can now answer "what
>     needs my reply?" from stored verdicts instead of dumping the inbox.
>   - **Digest phase-2 CONTENT live** at `/api/email/digest/preview{,.json,.txt}` — Salam can see his own
>     rendered digest in browser without any send infrastructure. Coming-up section (tomorrow through
>     end-of-week calendar) added; delegated Graph token best-effort fetch, degrades to empty section on
>     stale MSAL cache.
> - **What's NOT live yet:** `send_digest()` stub raises RuntimeError until app-level `Mail.Send` is
>   granted + service mailbox `starbot@starflooringandremodeling.com` exists + ApplicationAccessPolicy
>   restricts scope + Container Apps Job provisioned. Docstring on the stub has the 4-step enablement
>   in the traceback. `run_daily_digests(dry_run=True)` runs the whole pipeline against opted-in users
>   and logs subjects/byte counts without sending — useful for pre-flight testing.
> - **Local dev gotchas hit today (both are re-runs of documented ones):**
>   1. Local sign-in 500'd because SESSION_SECRET wasn't in `.env` — `--reload` restarted uvicorn on a
>      file save between login-start and callback, state-cookie signed with old ephemeral secret couldn't
>      verify against new one. Fix is permanent: `SESSION_SECRET=any-long-random-string` in
>      `backend/.env`.
>   2. Orphaned socket after `Stop-Process` on uvicorn (multiprocessing child inherited listening
>      handle). Documented in Notion Failure Modes now with correct sequence: netstat check ->
>      `Get-CimInstance Win32_Process | Where CommandLine -match 'multiprocessing-fork' | ForEach-Object
>      { Stop-Process -Id $_.ProcessId -Force }` -> reverify -> restart. Do NOT pipe Get-CimInstance
>      straight to Stop-Process — property-name binding fails and PowerShell looks up by `.Name`.
> - **Notion doc structure (2026-08-30):** Star Bot page is now a lean TOC with 9 subpages: Overview,
>   Architecture, Authentication & Identity, Deployment, Database, Costs, Local Development, Failure
>   Modes, Handoff Checklist. Handoff Checklist covers everything that needs to transfer when Ethan
>   leaves. DB admin password was in the OLD Notion page — rotated + stripped from Notion + stored in
>   company vault (Ethan, 2026-08-30). Intune policy for MOTW/Excel Protected View **blocked** —
>   DataNet-scoped, Ethan can't do; users will keep hitting Enable Editing.
> - **Feedback loop now working:** phase-1 tab in production means Salam actually uses it, generates
>   the tuning data for the next iteration. Digest preview means design can iterate on real content
>   before send goes live. Every triage rule added in the last two weeks came from a wrong verdict on
>   Ethan's actual inbox.
> - **Next up (Ethan's plan):** enable Mail.Send + provision Container Apps Job = phase 2 complete.
>   Then phase 3 (autodrafts + move-to-trash on Cleanup, both need Mail.ReadWrite). Then meeting-minutes
>   tab (planning note above), then product catalog (planning note above), then mileage purpose field
>   (planning note above).

> **Resume note (2026-08-25) - Digest built (phase 2 CONTENT + preview). Not yet sending; Mail.Send still to grant Monday.**
> - **What shipped today (working tree, not committed):** `backend/digest.py` — build_digest() +
>   render_html() + render_text() + `/api/email/digest/preview{,.json,.txt}` endpoints. Reads only
>   from the DB (email_threads, contacts, accounts, projects) for the read-only sections plus a
>   BEST-EFFORT delegated Graph calendar call for a new "Coming up" section (tomorrow through
>   +6 days, day-grouped, drops today by design because Star Mail's "your day" covers that).
> - **Design decisions worth keeping:**
>   1. **Table layout + inline styles** in render_html — Outlook 2016+ desktop has terrible CSS
>      support; class selectors and <style> blocks are unreliable. Brand palette (INK/MIST/SEA/
>      TIDE) baked into inline styles.
>   2. **Empty digests DON'T send.** run_daily_digests skips users whose sections are all empty
>      and logs "empty-skipped". A blank digest teaches archive-on-sight for future ones.
>   3. **Best-effort Graph token** for calendar. If m365.get_graph_token() throws (stale MSAL
>      cache, revoked consent), the calendar section is skipped but the rest of the digest still
>      goes out. Cron uses `try: get_graph_token` and falls back to None. Never make one section's
>      failure block the whole digest.
>   4. **Preview endpoint uses signed-in user's session cookie** to grab their token. Renders
>      THEIR digest, live, in browser — how we iterated on the design.
>   5. **run_daily_digests(dry_run=True)** default. Container-job entrypoint. Flip to False after
>      Mail.Send lands + Container Apps Job is provisioned.
>   6. **send_digest() written but raises RuntimeError** with the 4-step enablement instructions
>      until app_token is passed. Documented in the exception message so a Monday-morning "what
>      does this need again" question is answered in the traceback.
> - **The "Coming up" section (Ethan's ask 2026-08-25): "I need to be somewhere tomorrow but it
>   isn't in my todo because it's not today. However it's important to know today because it
>   changes what I do today."** Digest now pulls tomorrow-through-end-of-week Graph calendarView,
>   groups by day (Tomorrow / Weekday / Weekday+Date), shows time (12h format, `9:00a`), title,
>   trimmed location. Skipping today is DELIBERATE — do NOT re-add. Sample rendered against
>   Ethan's real calendar: "Tomorrow — Aug 26 / 11:00a End Neon DB / 1:00p Meeting with the City
>   for Building Plans (Mission Valley)". Works.
> - **Wired:** `main.py` imports digest, `include_router(digest.router)`. No new frontend for
>   digest itself (preview is a browser tab, not an app tab).
> - **Not yet done (Monday):**
>   1. Create `starbot@starflooringandremodeling.com` shared mailbox in Exchange.
>   2. Entra: Mail.Send APPLICATION permission on app `066b737b` + admin consent.
>   3. `New-ApplicationAccessPolicy` scoping Mail.Send to the shared mailbox only. Copy the exact
>      PowerShell from the send_digest docstring.
>   4. Container Apps Job resource: same image, cron `30 14 * * 1-5` (7:30am Pacific = 14:30 UTC),
>      entrypoint = `python -c "import digest; digest.run_daily_digests(app_token=<msal_cc>,
>      dry_run=False)"`. MSAL client-credentials acquires the app token at start of run.
>   5. Rollout to Salam + Ethan first for a week; expand after tuning.
> - **MOTW / Excel Protected View fix (Ethan's ask 2026-08-25, related to mileage report).** Not
>   a file encoding issue — Windows attaches Mark of the Web to browser downloads, Excel Protected
>   Views them, which BLOCKS AUTO-CALCULATION until Enable Editing. **Fix at Ethan's scale:
>   Intune Site to Zone Assignment List, scoped to the FQDN `starbot.starflooringandremodeling.
>   com` (value 1 = Local Intranet), NOT the parent domain** — trusting the parent inherits every
>   subdomain including any rogue one (DNS still hosts at securedservers.info, which has NXDOMAIN'd
>   twice). Local Intranet zone also enables Integrated Windows Auth by default — turn IWA off for
>   this zone in the same policy set since starbot uses Entra OAuth exclusively. Optional harden:
>   HSTS with includeSubDomains on the app response headers. **BLOCKED: Ethan can't create Intune
>   policies as admin — that scope sits with IT (DataNet). Not worth escalating; users just hit
>   Enable Editing each time. Revisit only if the Products tab (labeled photos over Blob) makes
>   this annoyance daily instead of weekly.**
> - **Also added (planning notes at top of this file):** MEETING MINUTES tab (per-user, Teams
>   transcripts + Read.ai webhook + manual paste), MILEAGE purpose-of-travel column on manual
>   trip entry, PRODUCT CATALOG tab with Blob-backed images and client-side canvas label overlay.
>   All deferred until digest is sending.
> - **Random-context note (2026-08-25):** confirmed Notion MCP connected as
>   `ethantfudge@gmail.com` (personal), NOT `efudge1952@sdsu.edu`. Reauthorize if Notion access
>   under SDSU is wanted later.

> **Resume note (2026-08-21) - EMAIL TRIAGE PHASE 1 BUILT + validated on real mail. Not committed/deployed.**
> - **What it is:** a new **Email tab** (`#email`, "Star Mail", Inbox icon, sits after Tasks) that ranks
>   the signed-in user's inbox at THREAD level into four lanes - Needs your reply / Waiting on them /
>   Worth knowing / Cleanup candidates - with the REASON each thread ranked shown on the row (the
>   audit line is the trust mechanism). **Resolved threads are deliberately absent** ("N resolved
>   themselves - not shown"): their absence IS the feature. Full build plan lives at the artifact
>   https://claude.ai/code/artifact/8d749a77-ab0e-4778-b31b-eba87433452c (5 phases; this is phase 1).
>   Runs entirely on existing Mail.Read - nothing sent, written back, or deleted.
> - **Files:** `backend/email_triage.py` (engine + router `/api/email/{overview,sync,prefs}`),
>   delta helpers in `graph.py` (`delta_messages`, `list_mail_folders`), 4 new tables in models.py
>   (`email_threads`, `email_sync_state`, `user_prefs`, `glossary` - created additively, ALREADY exist
>   in prod), `EmailPrefIn` in schemas.py, `frontend/src/Email.jsx`, api.js helpers, App.jsx wiring
>   (8 spots). Glossary seeds 9 entries via `_seed_on_first_run` (DataNet=IT vendor, RollMaster=ERP...).
> - **Pipeline (cost order):** Graph delta on inbox+sentitems (SENT ITEMS ARE REQUIRED - "you spoke
>   last" is invisible from the inbox alone) -> mechanical bulk filter (no-reply senders, unsubscribe
>   copy, notification-style subjects; rules, never a model) -> thread state from conversationId shape
>   -> deterministic scoring (To-vs-Cc, flagged, hit-list domain match, internal, age) -> Haiku
>   (`TRIAGE_MODEL`, default claude-haiku-4-5) batched x15, metadata only + glossary, ONLY on the
>   ambiguous remainder -> verdict stored once, keyed by last_message_id. 90-day retention prunes at
>   sync. NO BODIES STORED EVER (~1.2KB/row). `EMAIL_BACKFILL_DAYS` env, default 30.
> - **Measured on Ethan's real mailbox:** 330 msgs/30d -> 169 threads -> ~28 needs_reply / 29 waiting /
>   ~55 fyi / 34 bulk / 15+ resolved; ~60-70 model verdicts on backfill (~1-2 cents). First sync ~80s;
>   incremental sync = `0 changed` in ~19s. THE DataNet acceptance case passes: closed tickets ->
>   resolved, informational ticket updates -> fyi, ticket updates with a real question -> needs_reply
>   at modest rank. All cold marketing (Abacus/Verisk/Superhuman/Neon) -> fyi with blunt reasons.
> - **Triage rules that took real tuning (don't relearn these):** (1) toMe alone is NOT a strong
>   signal - that's how LinkedIn codes topped the lane; it needs a second human signal (named/asks/
>   account/internal/flagged). (2) ROBOT senders (support@|no-reply|contact@|sales@|"assistant"...)
>   defeat name/question signals -> model judges. (3) COLD first contact (msg_count==1, unknown
>   external domain, not flagged) -> model, never straight to the lane - that's where marketing lives.
>   (4) "Got it, thanks!" ack as last inbound (short, no '?', msg_count>1) -> resolved fast-path.
>   (5) They-replied-to-you-with-a-STATEMENT (answered your ask, no question back) -> model with the
>   hint "check if anything is still asked of you" - Olga's "that will be enough ;)" case. (6) Signals
>   must read the FRESH part of bodyPreview (`_fresh()` splits at ______/From:/On...wrote:) - quoted
>   history contains stale '?'s and the snippet displayed is the fresh part too. (7) Colleague-replied
>   settles a thread ONLY when user was Cc; on To or named -> model ("does their reply cover me?").
> - **Model-batch gotcha that bit:** 25 verdicts x max_tokens=1500 = truncated JSON = parse fail =
>   whole batch "(model unavailable)". Now batch=15, max_tokens=4000, reasons capped at 12 words,
>   fail-open rank clamped to 45 so junk never crowns the lane. Fail-open = needs_reply (never drop
>   mail silently).
> - **UI details:** first visit auto-runs the backfill with an honest "takes about a minute" banner;
>   digest toggle (writes user_prefs.digest_enabled, default OFF, labeled "coming soon" - phase 2
>   honors it); lanes collapsible w/ localStorage (`starEmailCollapsed`); rows open in Outlook via
>   webLink (this tab is NOT a mail client); account-matched threads show a hit-list chip; [sparkles]
>   icon = model-judged. Cleanup lane is read-only until Mail.ReadWrite (phase 3).
> - **NOT DONE:** not committed, not deployed (Ethan pushes; deploy = acr build + containerapp update).
>   Phase 2 (digest email) needs app-level Mail.Send + ApplicationAccessPolicy; phase 3 (drafts +
>   move-to-trash) needs Mail.ReadWrite + Mail.Send - Ethan said he'll grant in Entra. Known small
>   gaps: mail filed to folders by Outlook RULES never hits the inbox delta (open decision, plan doc);
>   a from-me-to-me edge case can land in needs_reply with a blank subject (model usually sorts it);
>   second-sync ~19s is mostly the folder-list call - could skip when delta returns 0 changes.

> **Resume note (2026-08-20) - Hit List II imported into Accounts (120 rows); Projects tab BUILT + SHIPPED (rev starbot--projects).**
> - **Hit list migration DONE.** The real **Hit List II (management companies)** CSV is now in the shared
>   Accounts table: **115 inserted, 120 total** (was the 5 pilot seeds). Source lives IN the project now:
>   `Downloads/PersonalCRM/Star Hit List II(Management Companies).csv` (Ethan pulled a clean copy from
>   SharePoint 2026-08-20). Import was dedup'd by **normalized name** (lowercase, strip periods/commas,
>   collapse whitespace) so the 5 curated seeds were SKIPPED, not overwritten - CONAM kept its
>   `rep='RJ Coons'` / `updated_by='Ethan Fudge'` edit and R.A. Snyder kept its 2 contacts. Address built
>   as `Address 1, Address 2, City, CA ZIP` (the `c/o` lines ride along in Address 2); thousands-commas
>   stripped from Total Units. Only `Hitzke` has no units (blank in source). Rows stamped
>   `updated_by='import:hitlist-II'`. **Backup before the write:**
>   `Downloads/PersonalCRM/accounts-backup-20260819-123049.json` (rollback point; the dry-run wrote a
>   second, identical snapshot at -123022).
> - **The OLD flooring-department hit list is DEAD** (Ethan, 2026-08-20) - no longer in use, do NOT import
>   `Star Hit List(flooring department).csv`. The prior "clean it up later" plan is cancelled.
> - **Known data nit (left as-is, Ethan's call pending):** reps came in verbatim, so `leighann` (1 row,
>   lowercase) vs `Leighann` (13) will fragment her "My accounts" filter, which keys off first name.
>   Slash-combos (`Rudy/Leighann`, `Salam/Leighann`) are fine - the UI splits on `/,&`. `RJ Coons` is a
>   real pilot edit, not a typo; leave it.
> - **NEW: Projects tab (`#projects`) - the PM team's commercial job board.** 6th tab. Purpose (Ethan):
>   project managers tracking LARGE commercial jobs (schools, restaurants) so they "feel on top of what
>   they're doing" walking into a weekly meeting. Deliberately lean - NOT a mini-Procore.
>   **Separate from Accounts by design: Accounts = SALES hit list (mgmt companies), Projects = PM work
>   (arrives via GCs/owners). No FK between them, and no Project->Account link** (Ethan: "that list is
>   completely separate from the project managers").
> - **Projects model:** `projects` (name, client [GC/owner, free text], site_address, project_type
>   school|restaurant|retail|multifamily|office|other, `stage`, pm [free text + datalist, like Accounts'
>   rep], contract_value, start_date, target_date, material, sq_ft, description, updated_at/updated_by)
>   -> many `project_interactions` (dated job log, records `by`). Shared company-wide (session-cookie
>   auth via `m365.get_session_user`), same as Accounts. Tables created by the normal additive
>   `create_all` in `_ensure_schema()` - **no migration needed**; they already exist in prod (14 tables now).
> - **Stages:** `in_progress` (default + "MAIN" lane, listed first) -> `punch_list` -> `awarded` ->
>   `bidding` -> `complete` -> `lost`. Display order is meeting-order (what's running / wrapping /
>   starting / chasing / closed), NOT pipeline order. Groups are collapsible with state persisted to
>   localStorage (`starProjectsCollapsed`); in_progress/punch_list/awarded open by default. Empty
>   non-main stages hide entirely; the in_progress lane always renders so a fresh board isn't blank.
> - **Files:** `backend/projects.py` (list/create/get/update/delete + `POST /{id}/log`, mirrors
>   accounts.py), `Project`/`ProjectInteraction` in models.py, `ProjectIn`/`ProjectLogIn` in schemas.py
>   (caps mirror AccountIn; `stage`+`projectType` COERCED to known sets so the board can't grow a
>   phantom column), `frontend/src/Projects.jsx`, api.js helpers, and the wiring spots in App.jsx
>   (icon import, component import, hash-init, hash-sync, eyebrow "Commercial jobs", title
>   "Star Projects", tab button `HardHat`, render).
> - **UI details worth keeping:** target-date **urgency** badge (overdue / due today / <=7d) that only
>   fires for LIVE stages - a finished job past its target isn't late. Dates are parsed from parts, NOT
>   `new Date("2026-08-20")`, which is midnight UTC and renders a day early in Pacific. One-click
>   **Move stage** row on the detail view (the edit a PM makes constantly, so it skips the form).
>   Double-submit guard on the form (the 2026-08-11 CRM bug). Browser Back closes detail/form -> board.
> - **Verified, not assumed:** all 6 endpoints in the OpenAPI schema; full CRUD smoke test against PROD
>   (create -> log note stamped `Ethan Fudge` -> stage change preserving the log -> bogus stage coerced
>   -> negative contractValue 422 -> cascade delete); `npm run build` clean (1759 modules); and a visual
>   pass on the real board + detail view with 5 realistic demo rows. **All demo rows deleted afterward -
>   `projects` and `project_interactions` are EMPTY, accounts still 120.**
> - **SHIPPED + committed.** Commit `a168690` (Ethan pushed it himself, per usual). Deployed 2026-08-20:
>   `az acr build --registry cadd18599bd0acr --image starbot:projects-tab .` (Run ID `ds1e`, 59s, digest
>   `sha256:272af72f...`; base images python 3.12-slim + node 20-slim) then
>   `az containerapp update -n starbot -g starbot --image cadd18599bd0acr.azurecr.io/starbot:projects-tab
>   --revision-suffix projects`. **Live revision = `starbot--projects`** (Healthy, 1 replica, 100%
>   traffic). Previous revision **`starbot--azuredb` kept Active at 0% traffic = the rollback target**
>   (roll back by pointing `--image` back at `starbot:tasks-layout`).
> - **Post-deploy verification (all passed):** the KEDA `business-hours` cron scale rule SURVIVED the
>   image update - confirmed via `containerapp show` (timezone America/Los_Angeles, start `30 7 * * 1-5`,
>   end `30 15 * * 1-5`, desiredReplicas 1, min 0 / max 10). `/api/health` 200 on BOTH the custom domain
>   and the azurecontainerapps.io host (~1s, warm). **Identity check that actually proves the new build is
>   serving: `/api/projects` returns 401, NOT 404** - the route exists and is auth-gated (a 404 would mean
>   the old image was still live; see gotcha #3 above for why "health 200" alone proves nothing). Live JS
>   bundle (`/assets/index-Co0khUUX.js`, 457 KB) greps clean for "Commercial jobs", "Star Projects",
>   "Punch list", "starProjectsCollapsed", and "ranked by properties" - so both the new tab AND the
>   Accounts table rework shipped.
> - **No schema work on boot:** `projects` + `project_interactions` already existed in prod (created
>   additively by `_ensure_schema()` during local testing), so the new revision just started serving.

> - **File uploads DEFERRED (decision made 2026-08-20).** Ethan flagged flooring plans that run 200+
>   pages; the real example in `Downloads/` is **103 MB** (`2026-01-30  Architectural Plans.pdf`), with a
>   hand-carved **30 MB** `_flooring.pdf` extract. Conclusion: **do NOT put these in Postgres** (card
>   blobs were removed for exactly this reason and prod is a B1ms/32 GiB server) - the design would be
>   **Azure Blob Storage + direct browser upload/download via short-lived SAS**, so a 100 MB file never
>   streams through the container. Ethan's call for now: **"dropping architectural plans is probably best
>   to be avoided"** - keep Projects light, no attachments. Natural phase-2 if it ever comes back:
>   auto-extract the flooring sheets from a full architectural set (they already do it by hand).
> - **Reference apps considered for the Projects design** (for future scope talks): Procore (commercial
>   standard, far too heavy - borrow only its object model), Buildertrend/CoConstruct/JobTread (GC/
>   remodeler tools - closest fit, what this is modeled on), Knowify (trade-contractor), monday/Asana
>   (source of the stage-board pattern). Money stays thin on purpose - QuickBooks (QBO connector) is the
>   book of record.
> - **Local-dev gotchas found this run (both cost real time):** (1) `uvicorn` failing to bind
>   (`WinError 10048`) still logs "Application startup complete" from the dying process, so a STALE
>   server keeps serving and your new env vars appear to be ignored - grep the log for `10048` and kill
>   the PID from `netstat -ano`, don't trust `pkill`. (2) There is **no local auth bypass** -
>   `m365.get_session_user` demands a signed cookie. To view the UI locally, start uvicorn with an
>   explicit `SESSION_SECRET` (it is NOT in `backend/.env`, so it is ephemeral per-process otherwise) and
>   mint `jwt.encode({'sub': <user_id>, 'exp': ...}, SECRET, 'HS256')` into the `starbot_session` cookie.

> **Follow-up (2026-08-20, later) - favicon added; starbot can now read/update the SHARED Accounts + Projects boards. SHIPPED (rev starbot--sharedboards).**
> - **Favicon (first one this app has ever had).** `frontend/public/favicon.svg` - a brand-red rounded
>   plate with a WHITE 5-point star (matches the star in the app header). White-on-red on purpose: a bare
>   red star disappears against a dark browser tab strip, and a filled shape reads better than a glyph at
>   16px. Geometry verified programmatically (10 points, alternating radii 9.20/3.70, even 36 degree gaps,
>   all inside the plate). Linked from `frontend/index.html` plus `<meta name="theme-color" content="#922525">`.
>   **Why this path works:** Vite copies `public/*` to the dist ROOT, the Dockerfile does
>   `COPY --from=frontend /fe/dist ./static`, and `serve_spa` in main.py serves any real file under
>   ./static before falling back to index.html - so `/favicon.svg` resolves with the right content-type
>   and needs NO new route. `public/` is not in `.dockerignore`, so it reaches the build context.
>   (Not done: a `.ico` fallback for pre-2019 browsers, and an `apple-touch-icon` PNG for iOS
>   home-screen installs. SVG favicons cover current Chrome/Edge/Firefox/Safari.)
> - **Starbot tool surface: 12 -> 20 tools.** It could previously only see the PRIVATE per-user CRM; it
>   now also reaches the two company-wide boards. New tools: `search_accounts`, `get_account`,
>   `log_account_note`, `set_account_status`, `list_projects`, `get_project`, `log_project_note`,
>   `set_project_stage`.
> - **Where the logic lives:** new `op_*` functions at the bottom of `backend/accounts.py` and
>   `backend/projects.py` (NOT in chat.py), so the domain logic sits with its module and the Claude
>   connector can reuse it later. `chat.py` now does `import accounts` / `import projects` - verified
>   NO import cycle (neither module imports chat or mcp_server; both only pull m365/telemetry/database/
>   models/schemas). Stage/status validation imports the single source of truth from schemas
>   (`_STAGES as VALID_STAGES`, `_STATUSES`) instead of re-declaring the sets.
> - **Two deliberate design choices, both different from the CRM ops:**
>   1. **NO user scoping.** Accounts and Projects are shared team assets, so every signed-in user sees
>      every row - same as the tabs. (The CRM ops in mcp_server.py stay strictly user-scoped.)
>   2. **Search returns COMPACT rows, not serialize().** serialize() carries every contact and every log
>      line; 120 of those would dump the entire hit list into the model's context and blow the input
>      budget. Search gives a summary row + id (caps: accounts 15 default / 40 max, projects 25 / 60) and
>      reports `matched` / `returned` / `truncated` so the model KNOWS the list is cut. Detail comes from
>      `get_account` / `get_project` for one id. Both list ops eager-load (`selectinload`) for the same
>      N+1 reason as the HTTP endpoints.
> - **Write surface is deliberately NARROW: log a note, set status/stage. NO create and NO delete from
>   chat.** Creating shared rows from a chat model is how you get duplicates, and duplicate cleanup
>   already cost a manual pass in Aug (Bob's 66 -> 42). The system prompt tells the model it cannot
>   create/delete and to point the user at the tab instead. Unlike the HTTP layer (which COERCES a bad
>   status/stage, because a form should never hard-fail), the op layer REJECTS unknown values with an
>   error listing the valid ones, so a wrong guess comes back correctable.
> - **System prompt updated** - the old text claimed everything was "always scoped to this signed-in
>   user", which is no longer true. Now separates PRIVATE (email/calendar/files/todos/CRM) from the two
>   SHARED boards, and adds two rules: (a) shared-board writes are team-visible with no undo in chat, so
>   confirm the right record when a name is ambiguous and only write when clearly asked; (b) keep the
>   boards straight - a school or restaurant job is a PROJECT, not an account.
> - **Tested against live prod data (reads AND writes):** search by query/rep/status (rep matching splits
>   on "/" so `rep=rudy` correctly matched 20 incl. "Rudy/Leighann"), get_account returned R.A. Snyder's
>   2 contacts, truncation flags correct, and all three error paths return usable messages (bad id, bad
>   status, bad stage). Writes were exercised then **fully reverted**: the account test snapshotted
>   status/updated_at/updated_by + log ids, logged a note, flipped status, then deleted the interaction
>   and restored the stamps (verified `fully restored: True`); the project test used a throwaway row and
>   deleted it. **Prod is unchanged: accounts still 120, projects/project_interactions still 0.**
> - **SHIPPED.** Commit `00adebf` (Ethan pushed). Deployed 2026-08-20:
>   `acr build ... --image starbot:shared-boards .` (Run ID `ds1f`, 57s, digest `sha256:d89dd05e...`)
>   then `containerapp update ... --revision-suffix sharedboards`. **Live revision =
>   `starbot--sharedboards`** at 100% traffic. Previous revision `starbot--projects` kept Active at 0%
>   as the rollback target (roll back by pointing `--image` at `starbot:projects-tab`).
> - **Post-deploy verification:** cron `business-hours` scale rule survived again (checked via
>   `containerapp show`: America/Los_Angeles, 7:30am-3:30pm Mon-Fri, desiredReplicas 1, min 0 / max 10).
>   `/favicon.svg` returns **200 with content-type `image/svg+xml`**, 543 bytes matching source, and the
>   served HTML carries both the icon link and the theme-color meta - so the `public/` -> dist -> static
>   -> `serve_spa` path works end to end with no new route. `/api/projects` and `/api/accounts` both 401
>   (present and auth-gated). Container logs show a CLEAN boot: "Application startup complete" plus
>   "StreamableHTTP session manager started" (connector unaffected), no import errors.
> - **Why the clean boot proves the new backend shipped:** `main.py` imports `chat`, and `chat.py` now
>   imports `accounts` + `projects`. A bad import there would crash-loop the app instead of reaching
>   startup-complete, so a healthy boot IS the evidence that the 8 new shared-board tools loaded in the
>   container. (The favicon is the frontend's identity marker; both come from the same image.)
> - **Still open:** the Claude connector (`mcp_server.py`) does NOT expose the shared boards - starbot
>   chat only, per Ethan's ask. Adding them later is small since the op_* functions are connector-shaped.


> **Follow-up same day (2026-08-20) - Accounts table reshaped, N+1 fixed, rep casing normalized, dump deleted.**
> - **Accounts spreadsheet columns are now: Account | Rep | Phone | Props | Units | Status | Updated.**
>   Dropped **Contact** (the import brought almost no contacts, so the column was ~96% "—" and the one
>   populated row made the whole table look lopsided - Ethan) and dropped **Website** (a
>   click-to-research action, not a scan-and-decide datum). Both still live on the DETAIL view, which is
>   where contacts/emails/notes belong: "keep the contact names and emails within the account itself, as
>   in you would click on it to see the notes and what people are there" (Ethan). Added **Props**
>   (`numProperties`) as a sortable numeric column immediately BEFORE Units, matching the old hit list's
>   layout. `colSpan` 8->7, table `minWidth` 820->760, and the numeric sort branch now serves both keys
>   (`sort.key === "props" ? a.numProperties : a.totalUnits`) with nulls always last.
> - **PERF BUG FOUND + FIXED: `/api/accounts` was taking 29 SECONDS.** Classic N+1 - `list_accounts` did
>   a bare `db.query(Account).all()` and `serialize()` then touched `a.contacts` and `a.interactions` on
>   every row, so 120 accounts = **241 queries**. Invisible during the 5-row pilot (11 queries), brutal
>   the moment the real hit list landed. Fix: `.options(selectinload(Account.contacts),
>   selectinload(Account.interactions))` -> **3 queries flat, 29.3s -> 1.0s** measured off-Azure, byte-for-byte
>   identical payload (47,121 bytes). Same treatment applied pre-emptively to `projects.py`
>   (`selectinload(Project.interactions)`). **Lesson: any list endpoint whose serialize() walks a
>   relationship needs eager loading before its table grows.** Note the 29s was measured from a LOCAL
>   client to Azure Postgres (~120ms/round-trip); from inside the container (same region) the same N+1
>   would have been ~1s - slow but easy to miss, which is exactly why it survived.
> - **Rep casing normalized (cosmetic only).** `leighann` -> `Leighann` on Trilogy Real Estate Management
>   (1 row). **CORRECTION to the note above: this was NEVER breaking the "My accounts" filter.**
>   `repTokens()` already lowercases both sides and `firstNameOf()` lowercases the signed-in name, so
>   `leighann`, `Leighann`, `Rudy/Leighann` and `Salam/Leighann` all matched already - verified by
>   replaying the exact filter logic. The fix is display consistency in the Rep column, nothing more.
>   Script normalizes each token to the most COMMON capitalization across all accounts (so it self-heals
>   future imports) and preserves `/ , &` separators; it deliberately does NOT touch
>   `updated_at`/`updated_by`, since a data tidy shouldn't overwrite who really last touched the row.
>   `RJ Coons` left alone - a real pilot edit, not a typo.
> - **`star_crm.dump` DELETED** from the repo root (123 KB, untracked, `PGDMP` custom-format). Safe: the
>   migration was verified table-by-table and the rollback path is **Neon** (kept until ~2026-08-26), not
>   this file. Confirmed the live DB healthy first - 14 tables / **1,444 rows** (was 1,304 at migration;
>   +115 accounts + new events accounts for the delta), `accounts` = 120, `projects` /
>   `project_interactions` = 0. **A second copy still sits at `C:\\Users\\ethan\\star_crm.dump` (114 KB) -
>   NOT touched, outside the project; delete at will.**

> - **Contact badge added to the Accounts table (2026-08-20).** With contacts moved to the detail view,
>   the list had no signal for which accounts actually have a person on file - so the name cell now
>   carries a small avatar chip (`UserRound` glyph in a SEA-tinted pill) whenever `contacts.length > 0`,
>   showing the COUNT only when there is more than one, plus a `title` tooltip listing "Name - Role" per
>   contact. Verified against the real 120 rows: exactly 1 badge (R.A. Snyder, shows "2", tooltip
>   "Belinda Torres - AP / Billing" + "S. Mojica - Coordinator").
> - **Local-dev gotcha #3: CHECK WHETHER A DEV SERVER IS ALREADY RUNNING BEFORE YOU START ONE.** Ethan
>   often has his own `uvicorn` + `vite` up with a browser tab open on localhost to watch changes live.
>   If you start uvicorn on top of that, yours silently dies with `WinError 10048` while HIS keeps
>   serving, and every symptom points the wrong way: `/api/health` returns ok (his process), the log says
>   "Application startup complete" (yours, just before it exits), and your freshly-set `SESSION_SECRET`
>   appears to be ignored so auth 401s forever. Worse, his server also logs "SESSION_SECRET not set"
>   (it is not in `backend/.env`), so the log cannot tell you whose process you are looking at. **Do this
>   first:** `netstat -ano | findstr ":8000 "` and ASK before killing anything - do not assume a listener
>   on 8000/5173 is yours. (Learned the hard way 2026-08-20: several rounds of confusion, and Claude
>   force-killed Ethan's dev servers and broke his open browser tab.)
> - **Secondary complication, real but not the headline: a socket can outlive the process that created
>   it.** On Windows a child process inherits open handles, sockets included, and the kernel keeps the
>   socket alive while ANY handle remains. So `taskkill /PID <uvicorn>` can report success while
>   `netstat` still shows that now-dead PID LISTENING - netstat attributes a socket to its CREATOR, not
>   to whoever currently holds a handle. Here uvicorn 21420 had a `multiprocessing.spawn ...
>   parent_pid=21420` child (34912) holding the inherited socket; killing 21420 did nothing, killing
>   34912 freed the port instantly. Find it with PowerShell
>   `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select ProcessId,CommandLine` and look
>   for the `--multiprocessing-fork` child whose parent_pid is the dead PID. (Which dependency spawns
>   that worker was never traced; the child ran the GLOBAL python, consistent with multiprocessing using
>   `sys._base_executable` from inside a venv.)
> - **Two smaller traps from the same session:** (a) grepping the log for `10048` ~8s after launch
>   returns 0 because the error has not flushed yet - wait ~12s before trusting a clean grep; an early
>   clean grep is NOT evidence of a successful bind. (b) `/api/health` only proves SOMETHING is
>   listening, not that it is your build - verify identity instead, e.g. hit a route that only exists in
>   the new code, or notice (as happened here) that auth failing with a secret you know is correct is the
>   old process telling you it is old.

> - **Also worth knowing for shell work in this repo:** patching files via `python -c "..."` inside Bash
>   breaks on BACKTICKS (bash does command substitution inside double quotes) and on long heredocs
>   (`ENAMETOOLONG` on spawn). Write the replacement text to a scratchpad file first, then splice it with
>   a short python script that reads that file - that path is reliable for both markdown notes and JSX.

> **Resume note (2026-08-19) - DB migrated Neon -> Azure Postgres; prod now on Azure, Neon kept as rollback.**
> - **Migration DONE.** Prod DB is now **Azure Database for PostgreSQL Flexible Server** `star-crm-pg` (PG **18.4**, Burstable **B1ms**, 32 GiB Premium SSD, westus3, RG `starbot`, ~**$16.09/mo** - HA OFF keeps it there; that's the real portal price, under the old $20.48 estimate). FQDN `star-crm-pg.postgres.database.azure.com`, db `star_crm`, admin `staradmin`. Firewall: allow-Azure-services (0.0.0.0) + Ethan's client IP.
> - **How:** `pg_dump -Fc --no-owner --no-privileges` from Neon's **DIRECT** host (drop `-pooler`; the pooled endpoint is PgBouncer and breaks pg_dump) via the `postgres:18` Docker image -> `pg_restore` into `star_crm`. Verified **all 11 tables exact parity** vs live Neon (1,304 rows incl. events 724). Client tools MUST be PG 18 to match the server - the Docker image guarantees that.
> - **Cutover:** swapped the container secret **`database-url`** to `postgresql+psycopg://staradmin:***@star-crm-pg.postgres.database.azure.com:5432/star_crm?sslmode=require`, then `az containerapp update --revision-suffix azuredb` to roll a new revision (secret changes are inert until a revision restarts). App boots clean on Azure (rev `starbot--azuredb`). `DATABASE_URL` env is a `secretref:database-url` pointer, so NO env change was needed - only the secret value + a fresh revision.
> - **Neon KEPT as rollback until ~2026-08-26.** Do NOT delete before then. Rollback = set `database-url` back to the Neon string + roll a revision. Local `backend/.env` `DATABASE_URL` also repointed to Azure (still LIVE prod - ethan profile only, never Bob).
> - **Gotchas (reconfirmed this run):** `pg_dump`/`pg_restore` binary output via PowerShell `>` gets UTF-16-corrupted - use WSL `>` or `pg_dump -f`. WSL "permission denied on docker.sock" = user not in the `docker` group (`sudo usermod -aG docker $USER` + reopen the shell). `az containerapp update` ECHOES empty scale-rule metadata (serialization artifact); `az containerapp show` confirms it persisted.

> **Resume note (2026-08-11) - cold-start fixed via native KEDA cron scale rule; CRM double-submit bug fixed + Bob's duplicate contacts cleaned.**
> - **Cold start (the "30s-3min to wake on first tab" annoyance) SOLVED with an Azure-native KEDA `cron` scale rule - NOT the cron-job.org external pinger the 2026-07-21 note planned.** A `cron` scale rule holds ONE replica warm during work hours; off-hours + weekends it still scales to zero (`minReplicas` stays 0). No external dependency, no request-log noise. Live on rev **starbot--0000043** (scale-config only, no image change). Rule `business-hours`: `timezone=America/Los_Angeles`, `start=30 7 * * 1-5` (7:30am Mon-Fri), `end=30 15 * * 1-5` (3:30pm), `desiredReplicas=1`; `maxReplicas=10`. Verified 1 warm replica at 10:13am PT.
> - **Cost ~$1-3/mo** (0.5 vCPU / 1 GiB, ~173 warm-hrs/mo, mostly inside Azure's free monthly compute grant) vs ~$11-15 for always-on `minReplicas=1`. The wake itself is pure Azure platform (schedule replica + image PULL + Python import of anthropic/mcp/pillow/openpyxl); the app's own uvicorn startup is ~0.2s and the DB is NOT involved. Lazy-importing the heavy libs would only trim the import slice - PARKED (off-hours cold starts rarely matter now the workday is warm).
> - **Apply/CLI:** `az containerapp update -n starbot -g starbot --min-replicas 0 --max-replicas 10 --scale-rule-name business-hours --scale-rule-type cron --scale-rule-metadata "timezone=America/Los_Angeles" "start=30 7 * * 1-5" "end=30 15 * * 1-5" "desiredReplicas=1"`. Portal: Container App -> Scaling -> add rule -> Custom -> type `cron`. **Gotcha:** the `update` command's ECHOED JSON shows EMPTY rule metadata (a serialization quirk of reading the mutation's return object); `az containerapp show` confirms the values persisted fine. Verify the resource, not the command echo.
> - **CRM double-submit bug FIXED (revs 40->42):** the "Save contact" button had no in-flight guard, so on a slow POST an impatient re-click fired `createContact` again (backend has no dedup) -> N identical rows. Root cause of Bob's dupes (every exact-dup group was created within a 1-3s span). Fix: a `busy` state in `ContactForm` (`frontend/src/App.jsx`) that `await`s the save and disables the button, mirroring the Accounts/Mileage forms.
> - **Bob's duplicate contacts CLEANED (66 -> 42):** backed up first (`Downloads/PersonalCRM/bob-contacts-backup-<ts>.json`, 66 contacts + 29 interactions), then Pass 1 deleted 20 exact-dup extras (keep oldest; zero interactions attached, pure deletes) -> 46, then Pass 2 merged 3 non-exact clusters (Mitch Stewart/Shaw 4->1; Jimmy Martinez + Brent Randle: kept the June rows that carry notes, set email -> firstname.lastname@sharp.com and category -> healthcare, dropped the Aug re-adds) -> 42. Only Bob was affected (Salam 10, Ethan 5, LeighAnn 1 had none).

> **Resume note (2026-08-11) — Tasks tab rebuilt as the live "your day" aggregator.**
> - **What it is:** the Tasks tab (`#tasks`) is no longer the kanban — it's a READ-ONLY aggregator
>   of the signed-in user's own M365 + CRM data (the Jul-30 design/mock, built for real). Three lanes
>   + a full-width manual list, and NOTHING writes to M365. NO new Graph scopes (existing Mail.Read +
>   Calendars.Read cover it); no admin consent.
> - **Lanes:** **Today's meetings** (calendar via `graph.list_calendar_events`, today→+6 days in
>   Pacific; frontend groups upcoming / "Earlier today" / "Coming up this week"). **Replies needed** =
>   Outlook FLAGGED emails (`graph.list_flagged_messages`, `$filter=flag/flagStatus eq 'flagged'`);
>   read-only — each row is "Open in Outlook" (webLink), NO checkbox; reply+unflag in Outlook → drops
>   off next Refresh; an **X dismiss** is persisted per-user (`dismissed_emails` table) so it stays
>   gone even if the flag remains. **People to follow up** = CRM `next_action`/`next_due` on the
>   SESSION user's own contacts; check = complete (logs a "Done:" touch on the contact + clears the
>   action, session-scoped so it never hits the X-User-Id profile).
> - **Manual "Your tasks"** = the existing `todos` table (the bot's add_todo still lands here), now a
>   FULL-WIDTH section UNDER the lanes (2-col grid), not a side rail. Safety nets (Ethan: "done tasks
>   were dropping into nothing"): checking a task moves it to a collapsible **Completed (N)** tray
>   (reopen by clicking it, or Clear completed); deleting pops a 6s **Undo** toast that re-creates it.
>   NO in-progress column.
> - **Backend:** new `backend/tasks.py` router — `GET /api/tasks/agenda` (per-lane Graph failures
>   degrade to an empty lane, not a 500), `POST /api/tasks/dismiss`, `POST /api/tasks/followup/{id}/
>   complete`. `DismissedEmail` model + unique index `ix_dismissed_user_msg`. `graph.list_flagged_
>   messages` added (no `$orderby` alongside the filter — sorts client-side). Frontend: `Tasks.jsx`
>   fully rewritten; Refresh button + "Updated HH:MM"; header eyebrow "Task board" → **"Your day"**.
> - **Deployed:** revs **41→42** (images tasks-aggregator / tasks-layout); live = `starbot--0000042`.
>   Ethan confirmed connectors work + calendar syncs instantly. **UNCOMMITTED as of this note** (revs
>   41-42 not yet in git; last push was `d3eaf08`).
> - **DNS incident (2026-08-11):** the custom domain intermittently NXDOMAIN'd "on cold starts." Root
>   cause = the whole `starflooringandremodeling.com` zone SERVFAILing at its nameservers
>   `ns1/ns2.securedservers.info` (DataNet/Greenman) — confirmed via 8.8.8.8 + 1.1.1.1 failing for the
>   zone AND `www` (company website down too), while Azure DNS + our hostname binding stayed fine. NOT
>   us, NOT Azure, NOT the container cold-start (that's a slow load, never NXDOMAIN); the "cold start"
>   tie is just the client's DNS cache expiring and re-querying the flaky NS. GoDaddy is only the
>   REGISTRAR (green = registration OK); DNS is hosted at securedservers.info. Recovered on its own
>   (2nd occurrence; 1st was 2026-07-09). **Do NOT reactively switch NS or use GoDaddy forwarding**
>   (would drop MX/email + records that live on securedservers.info). Durable fix = move the zone's
>   DNS to Cloudflare/Azure DNS (export records first, then switch NS at GoDaddy). Optional app-side
>   resilience: make the OAuth redirect follow the request host so the azurecontainerapps.io URL is a
>   real signed-in fallback (today `m365.REDIRECT_URI` is pinned to `PUBLIC_BASE_URL` = custom domain).

> **Resume note (2026-08-04) — shared Accounts tab (the "hit list") shipped as a pilot.**
> - **What it is:** a 5th tab, **Accounts** (`#accounts`, `frontend/src/Accounts.jsx` +
>   `backend/accounts.py`), a COMPANY-WIDE shared sales prospecting list — every signed-in user
>   sees + edits every account (session-cookie auth, NOT user-scoped). Distinct from the personal
>   CRM board.
> - **Model (3 new tables):** `accounts` (name, `rep` free-text, `status`
>   prospect|contacted|active|sold|dead|cod, website, phone, `addresses_json`/`emails_json` = JSON
>   string lists so one account holds several, num_properties, total_units, notes, `updated_at`/
>   `updated_by`) → many `account_contacts` (name/role/email/phone/address) → many
>   `account_interactions` (dated "log note" history, records `by`). create_all makes them;
>   `seed_accounts()` seeds 5 real Hit List II mgmt-co rows on first run (CONAM 10,744 units …
>   Whittington 866).
> - **API (`backend/accounts.py`):** list/create/get/update/delete + `POST /{id}/log` (append a
>   dated note, stamps updated_by). serialize() parses JSON lists → arrays, snake→camel, nests
>   contacts + `log`. Update REPLACES contacts wholesale (form owns the list; delete-orphan
>   cascade cleans up). Registered in main.py; seed called from `_seed_on_first_run`.
> - **Validation caps (both layers):** Pydantic `Field(max_length=…)` (name 200, phone 40,
>   website 300, email 254 [RFC], notes 5000) + list caps (addresses/emails 25, contacts 100) +
>   `status` coerced to the known set; frontend `maxLength` mirrors. Numbers ge 0. Guards the
>   SHARED table against one person's giant paste. NOTE: all data uses the SQLAlchemy ORM
>   (parameterized) — no SQL-injection surface; only raw SQL anywhere is static DDL in
>   `_ensure_schema`.
> - **UI (Accounts.jsx):** table RANKED BY UNITS with clickable sort headers (units/name/updated),
>   saved-view chips **All / My accounts** (rep matches signed-in first name; splits on /,& so
>   "Salam / Leighann" shows for both) **/ Unassigned** (no rep), search. Row click → read-first
>   **DETAIL** view (not the editor) with the **Activity** log + a **Log note** box and quick
>   actions (Log note, + Add email, + Add contact, Edit notes); **Edit** button → full form. Rep =
>   free-text input + `<datalist>` of user names (type-and-autofill like mileage; allows
>   custom/multi-rep). **Attach a personal contact** copies a private CRM contact onto the shared
>   account (team-visible). Browser **Back** closes an open account → list (same as the board's
>   overlay handling; in-page back matches). Long names wrap (18rem cap).
> - **Personal CRM also got:** a **healthcare** category (teal); a **Sort** dropdown (Due date /
>   Newest added [created_at] / Name); **"Log a touch" renamed → "Log note"** everywhere;
>   **"Board" tab renamed → "CRM"** (Salam dislikes "board"; shipped rev 31).
> - **Deployed:** revs **32→35** (images accounts-pilot / accounts-hardening / accounts-detail /
>   accounts-nav); live = `starbot--0000035`.
> - **Bugs fixed en route:** repeatable email rows were re-filled by the BROWSER, not React —
>   `autoComplete="off"` on the account-form inputs. A list input was a component-defined-in-render
>   (`key` prop swallowed + input focus lost each keystroke) → converted to a render function.
> - **Accounts NEXT / open:** import the real data AFTER cleanup — flooring hit list (~1000 rows)
>   is dated + messy (dupes, ALL-CAPS legacy accounting rows, misplaced fields); Ethan: base off
>   **Hit List II (mgmt companies)** only for now. Flooring/Windows department split DEFERRED.
>   Source of the mgmt-co list TBD (Ethan checking). CSVs in `Downloads/`: `Star Hit List
>   II(Management Companies).csv`, `Star Hit List(flooring department).csv`.

> - **Refinements (revs 36→39, live `starbot--0000039`):** the personal CRM list is now a
>   SPREADSHEET table (name/company/role/email/phone/next-action/category; the Next-action cell
>   clamps to 2 lines, other free-text columns truncate; shared `ROW_LINE = #e4dfd3` row separator
>   used by BOTH the CRM and Accounts tables). Card scan now AUTO-CLASSIFIES the category
>   (Sharp/Kaiser/Scripps → healthcare; the Haiku prompt in `cards.py` returns `category` + typed
>   `phones`, validated server-side). Contacts hold MULTIPLE typed phone numbers
>   {type: cell/work/home/other} in `contacts.phones_json` (additive migration; the primary number
>   is still mirrored into `phone` for the table + connector; helpers `_clean_phones_payload` /
>   `_contact_phones` in main.py). Front/desktop card-camera PREVIEW is mirrored (`scaleX(-1)`) for
>   natural framing; the CAPTURE is un-mirrored so text never reverses, and the mobile rear cam is
>   left alone (keyed on `getVideoTracks()[0].getSettings().facingMode !== "environment"`). Header
>   restructured: Scan card / Add contact sit ABOVE the tab bar, tabs on their own row so the
>   growing tab list never crowds them.

> **Resume note (2026-07-30) — chat UX fixes + Board→CRM + live webcam card scan shipped; Tasks aggregator DESIGNED.**
> - **Chat UX (LIVE, rev `starbot--0000030` / image `ui-chunked`):** streaming paints in
>   ~90ms chunks not per-token (buffer in `Chat.jsx` `send()`); mid-stream scroll no longer
>   yanks to bottom (`pinnedRef` only autoscrolls within 80px of bottom); fenced code blocks
>   (email drafts) now WRAP (`.starbot-md pre` → `white-space:pre-wrap;overflow-wrap:anywhere`
>   + `min-w-0` on the chat column); "Checking sign-in…" flash on every tab switch is gone —
>   `api.authMe()` is cached (`getCachedMe`) and App/Chat/Tasks/Mileage init `me` from it.
>   (The 30s cold-start itself is still Azure scale-to-zero; keep-warm ping is parked.)
> - **Board → CRM + webcam card scan (LIVE, rev `starbot--0000031` / image `card-camera`):**
>   tab reads "CRM" now (Salam dislikes "board"; eyebrow "Relationship board" →
>   "Relationships", loading → "Loading contacts…"; internal `view==="board"` + hash
>   unchanged; the **"Task board"** eyebrow left until the Tasks redesign ships). Card scan is
>   now a live **webcam** capture — `CardCamera` in `App.jsx`, `getUserMedia({video:{facingMode:
>   "environment"…}})` (rear cam on phones, default on desktop, NO picker — Ethan), framing
>   guide → Capture → freeze → Use/Retake → canvas `toBlob` JPEG → SAME `/api/contacts/scan-card`
>   (blob appended with a filename; `api.scanCard` tweak). Nothing written to disk (DB already
>   stored no card image). File picker kept as an auto-fallback when the cam is missing/blocked.
>   No backend change.
> - **Tasks tab = DESIGNED, not built.** Reframe: stop being a manual PM checklist; make it a
>   read-only **aggregator**. Keep the name "Tasks". Lanes: **Meetings** (calendar read),
>   **Replies needed** = Outlook FLAGGED mail (read-only; each row an "Open in Outlook" link,
>   NO checkbox — can't clear the flag; it's the seam for the future **autodraft** feature,
>   which needs `Mail.ReadWrite`), **People** = CRM `next_action`/`next_due` (check = complete
>   the CRM action), manual **rail** = existing `todos` table (bot's `add_todo` still lands
>   here). Decisions (Ethan): read-only, **NO push-to-Outlook, NO new Graph scopes**; a
>   **Refresh** button for "flagged while open"; **priority dropped** → time-based urgency
>   (overdue/today/week) + one optional star; passed meetings **collapse to "Earlier today"**
>   not deleted; checked items → a **"Done today"** tray that clears overnight. Clickable mock:
>   `scratchpad/starbot-tasks-mock.html` (artifact b744a642). **Next session:** build the
>   aggregation (flagged-mail + calendar via existing `Mail.Read`/`Calendars.Read`, follow-ups
>   from our DB) + rewrite `Tasks.jsx`.
> - **Demo analytics (Tue 2026-07-28, 9–11am PT):** Entra sign-ins = **7 distinct users**
>   (kim, ethan, salam, rj, leighann, rudy, amanda); Container App **636 requests** (peak 403
>   in the 9:30–10:00 bucket), **replicas stayed at 1**; Claude console ~1.5–2M tokens/day,
>   near the $1/day cap. Pull cmd: `az monitor metrics list --resource <id> --namespace
>   "microsoft.app/containerapps" --metric Requests …` (flag is `--namespace`, NOT
>   `--metric-namespace`). Sign-ins via `az rest` → Graph `auditLogs/signIns` filtered by
>   `appId 066b737b-…` + window. The `events` telemetry table is queryable by `created_at`
>   (naive UTC) for ANY window; `/api/stats` only does days-back aggregate + is auth-gated.
> - **Parked:** UI refresh (wider layout + serif labels) previewed, NOT shipped
>   (`scratchpad/starbot-ui-refresh.html`); keep-warm ping; DB move Neon→Azure (Ethan drives
>   it himself with Claude's step-by-step direction, does his own git).

> **Resume note (2026-07-23) — starbot chat can search the web.**
> - Added Anthropic's **server-side web search** to the chat agent loop
>   (`backend/chat.py`) for current/general/referential questions the user's M365
>   data can't answer. Tool: `WEB_SEARCH_TOOL = {type: web_search_20250305, name:
>   web_search, max_uses: 5}` — the BASIC variant on purpose (the dynamic-filtering
>   `_20260209` needs an Opus/Sonnet tier; prod `CHAT_MODEL=claude-haiku-4-5`).
>   `max_uses:5` caps spend (~$10/1000 searches). Passed as `_API_TOOLS = TOOLS +
>   [WEB_SEARCH_TOOL]`; it never hits `_run_tool` (Anthropic executes it inline).
> - **Loop changes for a server-side tool:** the stream now iterates raw events
>   (not `text_stream`) so a search surfaces a `{type:tool,name:web_search}` chip
>   mid-answer; `pause_turn` is handled (re-send to resume, no client tool_result);
>   `_clean_blocks` now PRESERVES `server_tool_use` + `web_search_tool_result`
>   (via `model_dump`) and text `citations` — needed so a paused turn resumes and
>   next-turn replay stays consistent. Frontend: `web_search` chip = Globe icon.
> - **Dependency bump: `anthropic` 0.40.0 → 0.118.0** (0.40 predates the web-search
>   block types — the SDK couldn't parse `server_tool_use`/`web_search_tool_result`).
>   `pip check` clean against pinned httpx/mcp/fastapi. Verified live on Haiku 4.5:
>   search runs, streams, cites, and the cleaned history replays cleanly. Low-risk
>   smoke-test after deploy: card scan + mileage calendar extraction still use
>   `messages.create` (stable API across the bump).
>
> **Resume note (2026-07-21) — mileage report pipeline SHIPPED; cold-start + concurrency + DB findings.**
> - **Mileage stages 1-3 all live** (revision `starbot--0000027`; details in the mileage
>   notes below). The calendar → day-stamp → one-click calculate → HR-format `.xlsx`
>   workflow is complete and deployed. Rate ground truth is **$0.7250** (HR sheet), stored
>   4-decimal. Report subtab: month-picker OR free dates (31-day cap, reversed dates
>   auto-swap), "Pull & calculate", checkbox day review, "Add from log", "Generate
>   spreadsheet". Zero-mile days auto-unchecked; office-address calendar events no longer
>   day-stamped as drives.
> - **Cold-start investigation (the "30s to wake" demo annoyance).** Root cause is NOT the
>   DB and NOT our code: the Container App has `minReplicas: null` (=0, scales to zero),
>   and the app's OWN startup is ~0.2s (uvicorn "Application startup complete" 215ms after
>   "Waiting for application startup"). The 30s is pure Azure platform cold-start:
>   scheduling a replica + pulling the image + importing the Python dep tree
>   (anthropic/mcp/pillow/openpyxl/sqlalchemy). Nothing in our code to optimize.
> - **DECISION (Ethan): business-hours cron pinger, NOT a DB migration and NOT minReplicas=1.**
>   Plan: a free external cron (cron-job.org) GETs `https://starbot.starflooringandremodeling.com/`
>   every 4 min, Mon-Fri **7:30am-3:30pm America/Los_Angeles**, keeping ONE replica warm
>   during work hours; scales to zero off-hours (cold start is fine then). Ping `/` (static,
>   NO DB) on purpose — hitting a DB endpoint would keep Neon awake all day and blow its
>   free compute-hour allowance. Est. cost ~$1-2/mo at 0.5vCPU/1GiB, ~40hr/wk, mostly idle
>   (brushes Azure's free monthly compute grant). minReplicas STAYS 0; nothing changed on
>   Azure — ping-only. **Ethan is setting up the cron-job.org account (Claude can't create
>   accounts); verify after by checking the revision replica count during the window.**
>   Rejected: minReplicas=1 (~$10-15/mo, always-instant but new standing cost); pinging as
>   a "free" trick (keeping a replica warm costs the same however triggered).
> - **DB migration reasoning CHANGED.** Open item #7's original driver (card-image BYTEA
>   blobs filling Neon's 0.5GB) is GONE — blobs were removed July 2026, DB is now all text
>   (<100MB for years). Remaining reason to move off Neon is OWNERSHIP only (Neon is on a
>   personal account; Azure Postgres Flexible Server ~$20.48/mo on StarSubscription cuts the
>   personal tie for handoff). NOT urgent; nothing at risk on Neon. The 2026-07-09 DB
>   decision note still holds if/when it happens (create server → pg_dump/restore → swap
>   secret → keep Neon a week; password no `&`/`@`).
> - **Concurrency analysis (for the record).** Route handlers are sync `def` → Starlette
>   runs them in a ~40-thread pool (concurrent, NOT serialized). `database.py` uses default
>   QueuePool (5 + 10 overflow = 15 conns/replica) with `pool_pre_ping=True`; Container Apps
>   scales out to `maxReplicas=10`; Neon pooled (PgBouncer) endpoint absorbs clients;
>   Postgres readers don't block readers. First limit anyone would hit = 15 concurrent
>   DB-touching requests per replica (then brief pool wait), but a small-company internal
>   tool is orders of magnitude below that. Azure consumption bursts out then shrinks — no
>   standing cost from load. Growth knob = `pool_size`/`max_overflow` + Neon tier, not FastAPI.
>
> **Mileage day-stamping (2026-07-20) — stage 1 of the calendar → autofill → report flow.**
> Target workflow (Ethan): pull calendar → parse which addresses belong to which dates →
> one click autofills a day's miles → export in the Marc-format mileage log (reference:
> `Downloads/Business-Vehicle-Mileage-marc week of 6-1-26 to 6-5-26.pdf` — columns Date of
> Travel / Purpose of Travel (address) / odometer start+end / total miles, header with emp
> name+ID+dept, rate, totals). Built in stages; stage 1 (this) = day-stamping.
> - New `place_visits` table: one row per user+place+DATE (unique index
>   `ix_place_visits_user_place_date` makes re-scans idempotent). Haiku scan prompt now
>   returns `{date, label, address}` — address+date pairs, deduped per date.
> - Scan behavior change: a known place visited again still records a visit (previously
>   known addresses were skipped entirely). Endpoints: GET/DELETE `/api/mileage/visits`.
> - UI: "Scanned days" day-group cards in the rail — **Load day** fills the stops AND the
>   new **Trip date** field in the entry form (trips now carry the travel day, not the
>   save-click day; needed for the day-grouped export). Places list captioned
>   "Address book".
> - UI cleanup (2026-07-20): mileage page has SUBTABS ("Trip entry" / "Log (N)", same
>   segmented-control style as the app header) — the log lives on its own page now.
>   Wording: "legs" → "stops" everywhere user-facing (lots of Spanish speakers; Ethan).
>   Bugfix found during the rename: the log summary always dropped the last destination
>   assuming it was the return drive — wrong for non-round-trips; stops are now derived
>   by detecting whether the last drive ends where the trip began (`stopsOf`), and the
>   expanded view marks the return drive "(return)".
> - RATE ground truth (2026-07-21, Ethan): **$0.7250/mi** — matches HR's template
>   (`Downloads/PersonalCRM/Mileage Tracker 2026.xlsx`, cell G4) and Marc's PDF. Code
>   default changed 0.70 → 0.725 (env `MILEAGE_RATE` still overrides). Fixed a real bug
>   with it: save/serialize rounded rate to 2 decimals, which would have stored 0.725 as
>   0.72 — now 4 decimals; frontend shows 3 decimals when they matter (`fmtRate`), rate
>   input step 0.0005. **Mixed rates in one report window: flag it** (header rate shows
>   'varies', reimbursement sums per-trip dollars) — rates should stay consistent.
> - EXPORT sample approved visually (CSV draft), then rebuilt as a styled workbook:
>   `Downloads/PersonalCRM/Star Mileage Report 2026 - SAMPLE.xlsx` — a copy of HR's
>   template with the SAME styling, restructured: ODOMETER START/END → ROUTE FROM/TO
>   (addresses, wrapped, 30-wide), day-grouped rows + bold DAY TOTAL rows, grand total
>   `=SUMIF(E:E,"DAY TOTAL")` so legs never double-count, G5=F39, G6=G4*G5. Template
>   quirks REMOVED on Ethan's request: freeze panes at A37 (rows 1-36 stayed on screen
>   — the "annoying sticky header") and the oversized banner rows (shrunk). Implement
>   stage-3 export to match THIS file. Final format feedback (Ethan, approved): EMP ID
>   and DEPT stay BLANK (hand-written if HR wants them; name auto-fills from the
>   signed-in user); every row fully visible — explicit row heights sized to wrapped
>   content (openpyxl files don't get Excel auto-fit), no shrink-to-fit; print = landscape
>   at TRUE 100% scale (fitToPage off), column widths trimmed (C24/D27/E27) so the grid
>   fits a letter page without scaling.
> - **STAGES 2+3 SHIPPED (2026-07-21): the Report subtab.** Pipeline (Ethan-approved):
>   month picker OR free dates (both offered; 31-day cap shared with scan via
>   `_normalize_window`, reversed dates auto-swap) → "Pull & calculate" =
>   `POST /api/mileage/report/preview` (scans calendar via shared `_scan_window`, keeps
>   logged days AS-IS — no recalc/no extra Maps spend — route-calculates visit-only days
>   as 'pending'; per-day failures don't sink the preview) → checkbox day list with
>   purposes/miles/dollars + mixed-rate warning → "Generate spreadsheet" saves pending
>   days via the normal trips endpoint (LOG IS SOURCE OF TRUTH; reports regenerate
>   identically) then `POST /api/mileage/report/generate` streams the xlsx.
>   `backend/mileage_report.py` fills the VENDORED template
>   `backend/assets/mileage_report_template.xlsx` (HR's file + approved rework baked in;
>   grid overflow past row 38 copies row-10 styles; grand total floats, SUMIF on DAY
>   TOTAL rows; mixed rates → header VARIES + per-day "@ $x/mi" notes + literal $ sum).
>   openpyxl==3.1.5 added. Filename: "Mileage Report - {name} - {June 2026|range}.xlsx".
>   Haiku scan max_tokens 1500→4000 (month-wide scans truncated the JSON). **openpyxl
>   sheet-view gotcha:** freeze_panes=None leaves orphaned pane selection records →
>   Excel "repairs" the file; fix = reset sheet_view.selection to A1 (baked into the
>   vendored template).
> - ~~Remaining~~ ORIGINAL plan for reference: (2) one-click day autofill, (3) export — **format DECIDED
>   2026-07-20, NOT a Marc clone. NO odometer columns** (Ethan: unrealistic self-reporting,
>   invites lying, and calculated distance already excludes gas stops / wrong turns we
>   shouldn't pay for; legal — IRS substantiation = date, from/to destinations, exact
>   miles, business purpose per trip; odometer readings only at tax-year start/end).
>   Rows = from→to legs with full addresses, grouped by day with day subtotals and a
>   monthly total — reads easier for HR. Purpose column = the visit label, generated as
>   a **grounded hybrid** (decided with Ethan 2026-07-20 after trying verbatim): the
>   Outlook event title when it's clear, composed from the event's own content when the
>   title is vague/mistyped/blank, never invented details. (4) maybe per-employee
>   header info.
>
> **Resume note (2026-07-15) — connector server-side COMPLETE; only the claude.ai add remains.**
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
> - **CONNECTOR LIVE + VERIFIED END-TO-END (2026-07-15, same day):** claude.ai
>   connected after the consent fix — container logs show Anthropic's initialize/
>   tools-list 200s at 16:53Z. Then a full local replication of Claude's exact
>   flow (auth-code + PKCE via the registered localhost:8000 callback, token
>   exchange WITH the RFC 8707 `resource` param, live MCP calls with the
>   delegated token): initialize 200 → tools/list 200 (all 6 tools) →
>   `search_contacts` 200 returning ETHAN's board (identity→profile mapping
>   correct; v2 token aud = client GUID, scp = access_as_user). App-only tokens
>   correctly rejected 403 insufficient_scope. Roadmap item #2 is DONE.
>   Optional hardening spotted in logs: Claude also probes the RFC 9728
>   path-inserted form `/.well-known/oauth-protected-resource/mcp` at the ROOT
>   (gets 404, falls back fine) — serving that alias would help other MCP
>   clients that don't fall back.
> - **Connector expanded to 10 tools (2026-07-15 evening, rev 21):** todo CRUD
>   added — `list_todos` / `add_todo` / `update_todo` / `delete_todo` wrap the
>   same chat.py op_* functions the in-app bot uses (`import chat` is LOCAL
>   inside build_mcp_app; chat.py imports mcp_server at module level, so a
>   top-level import would be circular). E2E verified: full add→list→done→
>   delete round-trip through the live connector. Decision (Ethan): NO email/
>   calendar tools in our connector — claude.ai's built-in Microsoft 365
>   connector covers that in the same chat. Test-parsing note: FastMCP returns
>   list results as one SSE text block PER item, not a single JSON array.
> - **Usage telemetry LIVE (rev 20):** `events` table + fire-and-forget
>   `telemetry.log_event` hooks (chat messages/tools, connector tools, CRM +
>   todo + mileage mutations, sign-ins) + `/api/stats` aggregate endpoint
>   (sign-in gated, counts only, `?days=N`). Baselines accrue from 2026-07-15;
>   feeds the monthly value report for Salam. Status brief for the cost meeting:
>   `Downloads/PersonalCRM/Starbot-Status-Brief-2026-07.docx` (one page; vendor
>   cost is a fill-in blank). az CLI fix: `-X utf8` flag (NOT PYTHONUTF8 env —
>   `-I` ignores it) stops the cp1252 ✓ crash in acr build log streaming.
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
