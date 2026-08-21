"""Email triage: thread-level ranking for the Email tab (phase 1).

The pipeline, in cost order — each stage exists to keep mail out of the next:

  1. FETCH   Graph delta on inbox + sentitems (incremental after first run).
             Sent items are what make thread state possible: "you spoke last"
             cannot be seen from the inbox alone.
  2. FILTER  Mechanical bulk detection (no-reply senders, unsubscribe copy).
             Rule-based, never a model. Bulk threads feed the cleanup lane.
  3. THREAD  Group by conversationId and decide state from thread SHAPE:
             you spoke last -> waiting on them; closure language -> resolved;
             colleague replied -> handled, UNLESS you were on To or named,
             which demotes-not-drops and asks the model. This stage is where
             the "resolved ticket presented as needing a reply" failure dies.
  4. SCORE   Deterministic signals: To vs Cc, flagged, sender domain matching
             a shared account, internal sender, folder, days waiting.
  5. MODEL   Haiku, batched, metadata only, ONLY on the ambiguous remainder.
             A company glossary rides in the prompt — accuracy here is a
             context problem, not a model-size problem.
  6. STORE   One verdict per thread, keyed by the latest message id so nothing
             is re-triaged until the thread actually changes. Rows past 90
             days are pruned at sync time. NO BODIES ARE EVER STORED.

Everything runs on Mail.Read — nothing is sent, written back, or deleted.
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import graph
import m365
import telemetry
from database import get_db
from models import Account, EmailSyncState, EmailThread, GlossaryEntry, User, UserPref
from schemas import EmailPrefIn

log = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/email", tags=["email"])

TRIAGE_MODEL = os.getenv("TRIAGE_MODEL", "claude-haiku-4-5")
BACKFILL_DAYS = int(os.getenv("EMAIL_BACKFILL_DAYS", "30"))
RETENTION_DAYS = 90
_TRAIL_CAP = 30          # per-thread message records kept in messages_json
_SNIPPET_CAP = 300
_MODEL_BATCH = 15  # 15 verdicts x ~200 output tokens fits max_tokens with room

_client: anthropic.Anthropic | None = None


def _anthropic() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- Stage 2: mechanical bulk filter (rules, never a model) -------------------
_BULK_SENDERS = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|notifications?|notify|newsletter|"
    r"news|mailer(-daemon)?|postmaster|alerts?|updates|marketing|promo(tions)?|"
    r"automated|noresponse|bounce)[@.+_-]", re.I)
_BULK_SUBJECT = re.compile(
    r"(verification code|security code|verify your .{0,24}(email|account|identity)|"
    r"password reset|quarantine digest|confirm your (email|subscription)|"
    r"welcome to|get started with|free trial|webinar|your receipt|your invoice is ready)", re.I)
_BULK_COPY = re.compile(
    r"(unsubscribe|view (this email )?in (your )?browser|manage (your )?preferences|"
    r"email preferences|stop receiving these)", re.I)

# --- Stage 3: closure language (conservative on purpose — a false "resolved"
# hides real work, so only unambiguous phrasings match) ------------------------
_CLOSURE = re.compile(
    r"(has been (resolved|closed|completed)|ticket .{0,25}(closed|resolved)|"
    r"case (is )?closed|marked as (resolved|complete)|your (request|issue) (was|has been) (resolved|closed)|"
    r"no (further )?action (is )?(needed|required)|this is an automated confirmation)", re.I)
_OOO = re.compile(r"(out of (the )?office|automatic reply|auto-?reply)", re.I)

# Generic mailbox domains that must never be used to match a shared account.
_GENERIC_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com",
    "icloud.com", "msn.com", "live.com", "att.net", "cox.net", "sbcglobal.net",
}


_ACK = re.compile(
    r"^(got it|sounds good|perfect|great|thank(s| you)|ok(ay)?|will do|"
    r"no problem|awesome|received|noted)[^?]{0,80}$", re.I)


def _fresh(text: str) -> str:
    """The unquoted part of a bodyPreview: everything before the reply-header
    junk ('________', 'From: ...', 'On ... wrote:'). Signals like 'asks a
    question' must read THIS, not the quoted history underneath it."""
    head = re.split(r"_{5,}|From:\s|On .{5,60} wrote:", text or "", maxsplit=1)[0]
    return head.strip()


def _addr(entry: dict) -> str:
    return ((entry or {}).get("emailAddress") or {}).get("address", "").lower()


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in (email or "") else ""


def _account_domain_map(db: Session) -> dict[str, tuple[str, str]]:
    """domain -> (account_id, account_name) from the shared hit list. Built
    from account emails + website hosts. Domain match is the ONLY automatic
    account association — fuzzy name matching is where wrong links come from."""
    out: dict[str, tuple[str, str]] = {}
    for a in db.query(Account).all():
        doms: set[str] = set()
        try:
            for em in json.loads(a.emails_json or "[]"):
                d = _domain(str(em))
                if d:
                    doms.add(d)
        except Exception:
            pass
        site = (a.website or "").strip().lower()
        if site:
            host = re.sub(r"^https?://", "", site).split("/")[0]
            host = host[4:] if host.startswith("www.") else host
            if "." in host:
                doms.add(host)
        for c in a.contacts:
            d = _domain(c.email or "")
            if d:
                doms.add(d)
        for d in doms:
            if d and d not in _GENERIC_DOMAINS:
                out[d] = (a.id, a.name)
    return out


# --- Stage 1: delta sync ------------------------------------------------------
def _compact(m: dict, me: str, folders: dict[str, str], direction: str) -> dict:
    """One Graph message reduced to the ~120 bytes thread state needs."""
    frm = _addr(m.get("from") or {})
    to = [_addr(r) for r in m.get("toRecipients") or []]
    cc = [_addr(r) for r in m.get("ccRecipients") or []]
    return {
        "id": m.get("id", ""),
        "conv": m.get("conversationId", ""),
        "dir": "out" if (direction == "out" or frm == me) else "in",
        "from": frm,
        "name": ((m.get("from") or {}).get("emailAddress") or {}).get("name", ""),
        "at": m.get("receivedDateTime", ""),
        "toMe": me in to,
        "ccMe": me in cc,
        "toInt": [t for t in to if _domain(t) == _domain(me) and t != me],
        "flag": ((m.get("flag") or {}).get("flagStatus") == "flagged"),
        "folder": folders.get(m.get("parentFolderId", ""), ""),
        "subj": (m.get("subject") or "")[:200],
        "prev": _fresh(m.get("bodyPreview") or "")[:_SNIPPET_CAP],
        "link": m.get("webLink", ""),
    }


def sync_user(db: Session, user: User, token: str) -> dict:
    """One full sync + triage round for a user. Incremental after first run."""
    me = (user.email or "").lower()
    state = db.get(EmailSyncState, user.id)
    if state is None:
        state = EmailSyncState(user_id=user.id)
        db.add(state)

    folders = graph.list_mail_folders(token)
    since = (_now() - timedelta(days=BACKFILL_DAYS)).strftime("%Y-%m-%d")

    changed: list[dict] = []
    removed_ids: set[str] = set()
    inbox, state.inbox_delta = graph.delta_messages(
        token, "inbox", delta_link=state.inbox_delta or "", since=since)
    sent, state.sent_delta = graph.delta_messages(
        token, "sentitems", delta_link=state.sent_delta or "", since=since)
    for m, direction in [(m, "in") for m in inbox] + [(m, "out") for m in sent]:
        if m.get("removed"):
            removed_ids.add(m["id"])
        elif m.get("conversationId"):
            changed.append(_compact(m, me, folders, direction))

    # Merge changes into thread rows.
    touched: dict[str, EmailThread] = {}
    for c in changed:
        conv = c.pop("conv", "")
        if not conv:
            continue
        t = touched.get(conv) or (
            db.query(EmailThread)
            .filter(EmailThread.user_id == user.id,
                    EmailThread.conversation_id == conv)
            .first()
        )
        if t is None:
            t = EmailThread(user_id=user.id, conversation_id=conv,
                            messages_json="[]", first_at=c["at"])
            db.add(t)
        _merge_message(t, c)
        touched[conv] = t

    if removed_ids:
        # A removed message (deleted / moved out of the folder) drops out of
        # its thread trail; the thread re-states from what remains.
        for t in db.query(EmailThread).filter(EmailThread.user_id == user.id).all():
            trail = json.loads(t.messages_json or "[]")
            kept = [r for r in trail if r["id"] not in removed_ids]
            if len(kept) != len(trail):
                t.messages_json = json.dumps(kept)
                t.msg_count = len(kept)
                touched[t.conversation_id] = t

    # Stages 2-5 on every touched thread.
    domains = _account_domain_map(db)
    ambiguous: list[EmailThread] = []
    for t in touched.values():
        _triage_deterministic(t, user, domains)
        if t.state == "model":
            ambiguous.append(t)
    if ambiguous:
        _triage_model(db, user, ambiguous)

    # Retention: prune what the tab will never show again.
    cutoff = (_now() - timedelta(days=RETENTION_DAYS)).isoformat()
    pruned = (
        db.query(EmailThread)
        .filter(EmailThread.user_id == user.id, EmailThread.last_at < cutoff)
        .delete(synchronize_session=False)
    )

    state.last_sync = _now()
    db.commit()
    telemetry.log_event(user.id, "email", "sync",
                        f"changed={len(changed)} threads={len(touched)} pruned={pruned}")
    return {"changed": len(changed), "threads": len(touched), "pruned": pruned}


def _merge_message(t: EmailThread, c: dict) -> None:
    """Fold one compact message record into a thread row (dedupe, sort, cap)."""
    trail = json.loads(t.messages_json or "[]")
    trail = [r for r in trail if r["id"] != c["id"]]
    trail.append({k: c[k] for k in
                  ("id", "dir", "from", "name", "at", "toMe", "ccMe", "toInt", "flag")})
    trail.sort(key=lambda r: r["at"])
    trail = trail[-_TRAIL_CAP:]
    t.messages_json = json.dumps(trail)
    t.msg_count = len(trail)
    t.first_at = min(t.first_at or c["at"], trail[0]["at"])
    last = trail[-1]
    t.last_at = last["at"]
    t.last_direction = last["dir"]
    t.last_message_id = last["id"]
    t.is_flagged = any(r.get("flag") for r in trail)
    if not t.subject and c["subj"]:
        t.subject = c["subj"]  # outbound-only threads still get a title
    # Thread-level display fields come from the newest INBOUND message.
    if c["dir"] == "in" and c["at"] >= (t.last_at or ""):
        t.subject = c["subj"] or t.subject
        t.snippet = c["prev"] or t.snippet
        t.sender_name = c["name"]
        t.sender_email = c["from"]
        t.folder = c["folder"] or t.folder
        t.web_link = c["link"] or t.web_link


# --- Stages 3+4: deterministic state + score ---------------------------------
def _triage_deterministic(t: EmailThread, user: User, domains: dict) -> None:
    trail = json.loads(t.messages_json or "[]")
    if not trail:
        t.state, t.rank, t.reason = "resolved", 0, "thread emptied"
        return
    last = trail[-1]
    me_first = (user.name or "").split()[0].lower()
    my_domain = _domain((user.email or "").lower())

    # Account match on the latest inbound sender's domain.
    inbound = [r for r in trail if r["dir"] == "in"]
    sender_dom = _domain(inbound[-1]["from"]) if inbound else ""
    acct = domains.get(sender_dom)
    t.account_id, t.account_name = (acct if acct else (None, ""))
    internal = sender_dom == my_domain and bool(sender_dom)

    t.model_used = False
    t.triaged_at = _now()

    # Bulk? (judged on the thread's inbound face, not the trail)
    if inbound and not acct:
        local = t.sender_email.split("@")[0] if t.sender_email else ""
        if (_BULK_SENDERS.match(local + "@") or _BULK_COPY.search(t.snippet or "")
                or _BULK_SUBJECT.search(t.subject or "")):
            t.state, t.rank = "bulk", 5
            t.category = "notification"
            t.reason = ("bulk sender" if _BULK_SENDERS.match(local + "@")
                        else "notification-style subject" if _BULK_SUBJECT.search(t.subject or "")
                        else "unsubscribe copy")
            return

    if last["dir"] == "out":
        t.state, t.rank = "waiting", 20
        t.category = "customer" if acct else ("internal" if internal else "other")
        t.reason = "you replied last — waiting on them"
        return

    # Last message is inbound.
    if _OOO.search(t.subject or "") or _OOO.search(t.snippet or ""):
        t.state, t.rank, t.category = "fyi", 10, "notification"
        t.reason = "auto-reply / out of office"
        return
    if _CLOSURE.search(t.subject or "") or _CLOSURE.search(t.snippet or ""):
        t.state, t.rank = "resolved", 0
        t.category = "customer" if acct else "other"
        t.reason = "closure language — thread looks settled"
        return
    # "Got it, thanks!" as the last word = a social close, not a task. Only
    # fires when the fresh text is a short acknowledgment with no question —
    # and only when you'd already replied (msg_count > 1), so a bare "thanks"
    # opener can't hide a real ask.
    if t.msg_count > 1 and _ACK.match(t.snippet or "") and "?" not in (t.snippet or ""):
        t.state, t.rank = "resolved", 0
        t.category = "customer" if acct else ("internal" if internal else "other")
        t.reason = "they acknowledged — nothing left to answer"
        return

    named = bool(me_first) and bool(
        re.search(rf"\b{re.escape(me_first)}\b", (t.snippet or "").lower()))

    # Colleague spoke last (internal, not me): settled only if I was Cc-level.
    if internal and last["from"] != (user.email or "").lower():
        if last.get("toMe") or named:
            t.state = "model"   # demote-not-drop: ask "does their reply cover me?"
            t.reason = "colleague replied — verify it covers what was asked of you"
            return
        t.state, t.rank, t.category = "fyi", 15, "internal"
        t.reason = "a colleague replied — you were copied"
        return

    # Score the needs-reply candidates.
    score = 55
    reasons = []
    if last.get("toMe"):
        score += 10; reasons.append("addressed to you")
    if t.is_flagged:
        score += 15; reasons.append("you flagged it")
    if acct:
        score += 15; reasons.append(f"hit-list account: {t.account_name}")
    if internal:
        score += 5; reasons.append("internal")
    days = _days_since(t.last_at)
    if days >= 2:
        score += min(days * 2, 14); reasons.append(f"waiting {days}d")
    if "?" in (t.snippet or ""):
        reasons.append("asks a question")

    t.category = "customer" if acct else ("internal" if internal else "other")
    asks = "?" in (t.snippet or "")
    # Automated senders defeat the human signals: marketing mail puts your name
    # in every greeting and support acks end in "did we help?". If the sender
    # reads as a robot and isn't a known account or a colleague, Haiku judges it.
    robot = bool(re.search(r"(support|customer service|customer care|helpdesk|team$|"
                           r"notifications?|accounts?@|hello@|hi@|contact@|sales@|info@|"
                           r"hey@|growth@|assistant)",
                           (t.sender_name or "") + " " + (t.sender_email or ""), re.I))
    # Cold first contact from an unknown external domain is where marketing
    # lives ("Abacus AI", "Verisk"): no history with us, not on the hit list,
    # not a colleague. A real human first-contact survives the model pass with
    # a proper reason; a pitch gets classified as the pitch it is.
    cold = t.msg_count == 1 and not acct and not internal and not t.is_flagged
    # "Addressed to you" alone is how verification codes and vendor promos top
    # the lane — toMe needs a second human signal, otherwise Haiku judges it.
    # They replied to you last with a statement, not a question: often that IS
    # the end of the thread (they answered what you asked). The model judges.
    answered = t.msg_count > 1 and not asks and any(
        r["dir"] == "out" for r in trail[:-1])
    strong = ((t.is_flagged or named or bool(acct) or internal
               or (last.get("toMe") and asks))
              and not (robot and not acct and not internal)
              and not cold and not answered)
    if strong:
        t.state, t.rank = "needs_reply", min(score, 100)
        t.reason = ", ".join(reasons) or "direct message to you"
    else:
        t.state = "model"   # unclear ask -> let Haiku judge
        t.rank = min(score, 100)
        t.reason = ("they replied to your message — check if anything is still asked of you"
                    if answered else "unclear whether this needs you")


def _days_since(iso: str) -> int:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).replace(tzinfo=None)
        return max(0, (_now() - dt).days)
    except Exception:
        return 0


# --- Stage 5: Haiku on the ambiguous remainder --------------------------------
def _glossary_block(db: Session) -> str:
    rows = db.query(GlossaryEntry).all()
    if not rows:
        return ""
    lines = "\n".join(f"- {g.term}: {g.meaning}" for g in rows[:40])
    return f"\nCompany context (Star Flooring & Remodeling, San Diego):\n{lines}\n"


def _triage_model(db: Session, user: User, threads: list[EmailThread]) -> None:
    """Batch-classify the threads rules couldn't settle. Metadata only — no
    bodies. On any failure the deterministic verdict stands (fail open into
    the visible lane, never silently drop mail)."""
    gloss = _glossary_block(db)
    for i in range(0, len(threads), _MODEL_BATCH):
        batch = threads[i:i + _MODEL_BATCH]
        items = "\n".join(
            f'{n}. from: {t.sender_name or "?"} <{t.sender_email or "?"}> | '
            f'subject: {(t.subject or "(no subject)")[:100]} | '
            f'hint: {t.reason or ""} | snippet: {(t.snippet or "")[:200]}'
            for n, t in enumerate(batch, 1)
        )
        prompt = (
            f"You are triaging {user.name}'s work inbox at a flooring company.{gloss}\n"
            "For each thread below decide:\n"
            '- state: "needs_reply" (they are still expected to respond), '
            '"fyi" (informational / already handled by someone else), or '
            '"resolved" (the loop is closed).\n'
            "- importance: 1-5 (5 = urgent business).\n"
            '- category: "customer", "vendor", "internal", "notification", or "other".\n'
            "- reason: ONE short factual line, max 12 words, no speculation.\n"
            "A hint of 'colleague replied' means: judge whether the colleague's reply "
            "covers what was asked of THIS user; if unclear, prefer needs_reply.\n\n"
            f"{items}\n\n"
            'Reply with ONLY a JSON array: [{"n":1,"state":"…","importance":3,'
            '"category":"…","reason":"…"}, …]'
        )
        try:
            resp = _anthropic().messages.create(
                model=TRIAGE_MODEL, max_tokens=4000, temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            data = json.loads(text[text.index("["):text.rindex("]") + 1])
            verdicts = {int(v["n"]): v for v in data if "n" in v}
        except Exception as err:
            log.warning("email triage model batch failed (%s); deterministic verdicts stand", err)
            for t in batch:
                if t.state == "model":
                    t.state = "needs_reply"  # fail open into the visible lane
                    t.rank = min(t.rank, 45)  # but never crowning it above sure things
                    t.reason = (t.reason + " (model unavailable)").strip()
            continue
        for n, t in enumerate(batch, 1):
            v = verdicts.get(n)
            if not v:
                t.state = "needs_reply"
                continue
            st = str(v.get("state", "")).strip().lower()
            t.state = st if st in ("needs_reply", "fyi", "resolved") else "needs_reply"
            imp = v.get("importance")
            if isinstance(imp, (int, float)):
                t.rank = max(0, min(100, int(40 + (imp - 3) * 15)))
            cat = str(v.get("category", "")).strip().lower()
            if cat in ("customer", "vendor", "internal", "notification", "other"):
                t.category = cat
            reason = str(v.get("reason", "")).strip()[:280]
            if reason:
                t.reason = reason
            t.model_used = True


# --- Router -------------------------------------------------------------------
def _serialize(t: EmailThread) -> dict:
    return {
        "id": t.id,
        "subject": t.subject or "(no subject)",
        "senderName": t.sender_name,
        "senderEmail": t.sender_email,
        "snippet": t.snippet or "",
        "folder": t.folder or "",
        "webLink": t.web_link or "",
        "state": t.state,
        "rank": t.rank,
        "category": t.category or "other",
        "reason": t.reason or "",
        "modelUsed": t.model_used,
        "accountId": t.account_id,
        "accountName": t.account_name or "",
        "flagged": t.is_flagged,
        "msgCount": t.msg_count,
        "lastAt": t.last_at or "",
        "lastDirection": t.last_direction,
    }


def _overview(db: Session, user: User) -> dict:
    rows = (
        db.query(EmailThread)
        .filter(EmailThread.user_id == user.id)
        .all()
    )
    lanes = {"needsReply": [], "waiting": [], "fyi": [], "cleanup": []}
    resolved = 0
    for t in rows:
        if t.state == "needs_reply":
            lanes["needsReply"].append(t)
        elif t.state == "waiting":
            lanes["waiting"].append(t)
        elif t.state == "fyi":
            lanes["fyi"].append(t)
        elif t.state == "bulk":
            lanes["cleanup"].append(t)
        elif t.state == "resolved":
            resolved += 1
    lanes["needsReply"].sort(key=lambda t: (-t.rank, t.last_at))
    for k in ("waiting", "fyi", "cleanup"):
        lanes[k].sort(key=lambda t: t.last_at or "", reverse=True)

    state = db.get(EmailSyncState, user.id)
    pref = db.get(UserPref, user.id)
    return {
        "lastSync": state.last_sync.isoformat() if state and state.last_sync else "",
        "resolvedCount": resolved,
        "digestEnabled": bool(pref.digest_enabled) if pref else False,
        "digestHour": pref.digest_hour if pref else 7,
        "lanes": {k: [_serialize(t) for t in v] for k, v in lanes.items()},
    }


@router.get("/overview")
def get_overview(
    user: User = Depends(m365.get_session_user), db: Session = Depends(get_db)
) -> dict:
    """Stored verdicts, grouped by lane. Cheap — no Graph calls, no model."""
    return _overview(db, user)


@router.post("/sync")
def run_sync(
    user: User = Depends(m365.get_session_user), db: Session = Depends(get_db)
) -> dict:
    """Delta-sync this user's mailbox, triage what changed, return the
    refreshed overview. First run backfills a bounded window; every run after
    is incremental, so this is fast enough to sit behind a Refresh button."""
    token = m365.get_graph_token(user, db)
    try:
        stats = sync_user(db, user, token)
    except graph.GraphError as err:
        raise HTTPException(status_code=err.status if err.status == 401 else 502,
                            detail=str(err))
    out = _overview(db, user)
    out["syncStats"] = stats
    return out


@router.put("/prefs")
def set_prefs(
    payload: EmailPrefIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    pref = db.get(UserPref, user.id)
    if pref is None:
        pref = UserPref(user_id=user.id)
        db.add(pref)
    if payload.digestEnabled is not None:
        pref.digest_enabled = bool(payload.digestEnabled)
    if payload.digestHour is not None:
        pref.digest_hour = max(5, min(int(payload.digestHour), 12))
    db.commit()
    telemetry.log_event(user.id, "email", "prefs",
                        f"digest={'on' if pref.digest_enabled else 'off'}")
    return {"digestEnabled": pref.digest_enabled, "digestHour": pref.digest_hour}


# --- First-run glossary seed ---------------------------------------------------
SEED_GLOSSARY = [
    ("Star Flooring & Remodeling", "us — San Diego flooring, remodeling, and flood restoration company"),
    ("Starbot", "our internal AI assistant app (this system)"),
    ("RollMaster", "our ERP — used all day for jobs, invoicing, and accounting"),
    ("Kudu Pro", "an ERP we are evaluating as a RollMaster replacement"),
    ("DataNet", "outside IT vendor (also called Greenman IT); handles tickets and hosts company DNS"),
    ("Hit List", "our shared list of property-management companies the sales team prospects"),
    ("Salam", "the owner of Star Flooring"),
    ("COI", "certificate of insurance — customers routinely request these"),
    ("take off / takeoff", "measuring a job from plans to estimate materials"),
]


def seed_glossary(db: Session) -> None:
    """Seed the triage glossary on an empty table (idempotent)."""
    if db.query(GlossaryEntry).count() > 0:
        return
    for term, meaning in SEED_GLOSSARY:
        db.add(GlossaryEntry(term=term, meaning=meaning))
    db.commit()
