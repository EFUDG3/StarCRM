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

> **Resume note (2026-08-20, LATEST) - Hit List II imported into Accounts (120 rows); Projects tab BUILT (not yet deployed/committed).**
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
> - **NOT DONE:** not committed to git, not deployed (no new revision). Deploy is the usual
>   `az acr build` + `az containerapp update`; the tab appears as soon as the image ships.
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
