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
    r"account (successfully )?created|welcome to|get started with|free trial|"
    r"webinar|your receipt|your invoice is ready|quick setup before your zoom)", re.I)
_BULK_COPY = re.compile(
    r"(unsubscribe|view (this email )?in (your )?browser|manage (your )?preferences|"
    r"email preferences|stop receiving these)", re.I)
# Ticket-system auto-updates: DataNet, Zendesk, ServiceNow etc. wrap every
# note in "A Response to your Ticket #..." + "## Please type your reply above
# this line ##". Almost all are FYI — a status update, not a to-do. If the
# note is actually a question for THIS user, the model can promote it back.
_TICKET_SUBJECT = re.compile(
    r"(^| )(a response to your ticket|re: ticket #|new comment on ticket|"
    r"ticket #\d|\[?ticket:? ?#?\d)", re.I)
# Meeting-recording bots (Read.ai / Fathom / Otter / Grain) — recurring
# setup nags, never a real reply candidate.
_RECORDER_BOT = re.compile(
    r"(read( ai| assistant| support)|fathom|otter\.ai|grain\.co)", re.I)

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
    to_raw = m.get("toRecipients") or []
    to = [_addr(r) for r in to_raw]
    cc = [_addr(r) for r in m.get("ccRecipients") or []]
    to_first = (to_raw[0].get("emailAddress") if to_raw else {}) or {}
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
        # All recipient addresses (lowercase) so cross-thread reply detection
        # can ask "did I email person X since date Y" without a Graph call.
        # Kept short — most emails have <5 recipients; cap for storage sanity.
        "to": to[:10],
        "toFirstName": to_first.get("name", ""),
        "toFirstEmail": (to_first.get("address", "") or "").lower(),
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
    # Precompute cross-thread outbound history so the triage rule doesn't
    # rescan every user's mail per candidate. Map: recipient_addr ->
    # sorted list of ISO timestamps of outbound messages to them (across
    # ALL of this user's threads). Read once per sync.
    outbound_map = _build_outbound_map(db, user)
    ambiguous: list[EmailThread] = []
    for t in touched.values():
        _triage_deterministic(t, user, domains, outbound_map=outbound_map)
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
    trail.append({k: c.get(k) for k in
                  ("id", "dir", "from", "name", "at", "toMe", "ccMe", "toInt", "to", "flag")})
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
    # A new message CLEARS a stale dismiss (dismissing "Manatal welcome"
    # doesn't dismiss all future Manatal replies — only the state at time
    # of dismiss). Compares against the id we stamped when the user
    # clicked X; if a later message came in, resurface the thread.
    if t.dismissed_at_msg_id and last["id"] != t.dismissed_at_msg_id:
        t.dismissed_at = None
        t.dismissed_at_msg_id = ""
    # Thread-level display fields come from the newest INBOUND message.
    if c["dir"] == "in" and c["at"] >= (t.last_at or ""):
        t.subject = c["subj"] or t.subject
        t.snippet = c["prev"] or t.snippet
        t.sender_name = c["name"]
        t.sender_email = c["from"]
        t.folder = c["folder"] or t.folder
        t.web_link = c["link"] or t.web_link
    # Outbound-only fallback: threads you started, where no inbound reply
    # exists yet, previously rendered "Unknown sender" with a dead link.
    # Populate from the To-recipient of the earliest outbound message so
    # rows are meaningful and the sent-item webLink opens in Outlook.
    if not t.sender_email and c["dir"] == "out":
        if c.get("toFirstEmail"):
            t.sender_name = c.get("toFirstName") or c["toFirstEmail"]
            t.sender_email = c["toFirstEmail"]
        if c.get("link") and not t.web_link:
            t.web_link = c["link"]
        if c.get("prev") and not t.snippet:
            t.snippet = c["prev"]


# --- Stages 3+4: deterministic state + score ---------------------------------
def _build_outbound_map(db: Session, user: User) -> dict[str, list[str]]:
    """Precompute: for this user, all outbound recipients across ALL threads,
    mapped to sorted timestamps. Read once per sync; used by cross-thread
    reply detection so a needs_reply candidate can ask "did I email this
    person in ANOTHER thread since the last inbound?" without a Graph call
    or an N**2 scan.

    Sender address (lowercase) -> [ISO timestamps of my sends to them].
    Legacy trail rows that predate the "to" field are skipped silently."""
    out: dict[str, list[str]] = {}
    rows = (
        db.query(EmailThread)
        .filter(EmailThread.user_id == user.id)
        .all()
    )
    for row in rows:
        try:
            trail = json.loads(row.messages_json or "[]")
        except Exception:
            continue
        for r in trail:
            if r.get("dir") != "out":
                continue
            for addr in (r.get("to") or []):
                if not addr:
                    continue
                out.setdefault(addr.lower(), []).append(r.get("at", ""))
    for addrs in out.values():
        addrs.sort()
    return out


def _replied_in_another_thread(sender: str, since: str,
                               outbound_map: dict[str, list[str]]) -> bool:
    """True if user sent something to `sender` after `since`, per the
    precomputed map. Simple linear scan — recipient lists are short
    (typically <20 sends per address over 90 days)."""
    if not sender:
        return False
    for at in outbound_map.get(sender.lower(), []):
        if at > since:
            return True
    return False


def _triage_deterministic(t: EmailThread, user: User, domains: dict,
                          outbound_map: dict[str, list[str]] | None = None) -> None:
    trail = json.loads(t.messages_json or "[]")
    if not trail:
        t.state, t.rank, t.reason = "resolved", 0, "thread emptied"
        return
    last = trail[-1]
    me_first = (user.name or "").split()[0].lower()
    me_email = (user.email or "").lower()
    my_domain = _domain(me_email)

    # Account match on the latest inbound sender's domain.
    inbound = [r for r in trail if r["dir"] == "in"]
    sender_dom = _domain(inbound[-1]["from"]) if inbound else ""
    acct = domains.get(sender_dom)
    t.account_id, t.account_name = (acct if acct else (None, ""))
    internal = sender_dom == my_domain and bool(sender_dom)

    t.model_used = False
    t.triaged_at = _now()

    # Self-to-self (note-to-self from phone, drafts landing in inbox). Never
    # a task. Blank subject + own address on both ends = drop cleanly.
    if inbound and inbound[-1]["from"] == me_email and (
            not t.subject or "sent from my iphone" in (t.snippet or "").lower()):
        t.state, t.rank, t.category = "resolved", 0, "other"
        t.reason = "note to yourself"
        return

    # Recorder-bot nags (Read.ai, Fathom, Otter, Grain). Recurring setup
    # reminders — never a real reply candidate.
    if _RECORDER_BOT.search((t.sender_name or "") + " " + (t.sender_email or "")):
        t.state, t.rank, t.category = "bulk", 5, "notification"
        t.reason = "meeting-recording bot"
        return

    # Ticket-system auto-updates (DataNet, Zendesk, etc.) default to FYI —
    # the model can promote back to needs_reply if the note asks something
    # specific of THIS user.
    if inbound and _TICKET_SUBJECT.search(t.subject or ""):
        t.state = "model"
        t.category = "notification"
        t.reason = "ticket update — promote to needs-reply only if a specific question is asked of you"
        return

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
        # Cross-thread reply check: user may have already answered by starting
        # a new thread. Cheap DB scan of their own outbound history, no model,
        # no Graph. Demotes to fyi (soft — still visible, just not treated as
        # a task); the model can still promote back if a specific ask remains.
        if outbound_map and t.sender_email and _replied_in_another_thread(
                t.sender_email, t.last_at or "", outbound_map):
            t.state = "fyi"
            t.rank = min(score, 35)
            t.category = "customer" if acct else ("internal" if internal else "other")
            t.reason = "you may have replied in another thread — verify"
            return
        t.state, t.rank = "needs_reply", min(score, 100)
        t.reason = ", ".join(reasons) or "direct message to you"
    elif cold:
        # Cold outreach still MATTERS — sales pitches and first contacts bring
        # business — but they belong in "worth knowing", not the reply lane.
        # The model still gets to look; it just won't default to needs_reply.
        t.state = "model"
        t.rank = min(score, 40)
        t.reason = "cold outreach — worth knowing, promote only if it asks something specific"
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
            "For each thread pick ONE state:\n"
            '- "needs_reply": THIS user is CLEARLY expected to send a specific '
            "answer soon — a direct question TO them, an approval they must "
            "give, or an item they were asked to provide. Positive evidence "
            "required.\n"
            '- "fyi" (WORTH KNOWING): status updates, ticket notes without a '
            "specific ask, sales pitches, cold outreach, account confirmations, "
            "recording bots, anything that COULD get a reply but doesn't require "
            "one. Sales pitches and first contacts belong here — they bring "
            "business but they are not a to-do.\n"
            '- "resolved": the loop is genuinely closed.\n'
            "RULE: when in doubt between needs_reply and fyi, choose fyi. The "
            "cost of a false needs_reply (noise in the reply lane) is much "
            "higher than a false fyi (still visible, just lower).\n"
            "For 'ticket update' hints: default to fyi unless the note contains "
            "a specific question directed at the user. For 'colleague replied' "
            "hints: fyi unless the colleague clearly did NOT cover the user's "
            "part. For 'cold outreach' hints: fyi unless the pitch asks a "
            "specific decision or scheduling of the user.\n"
            "Also return:\n"
            "- importance: 1-5 (5 = urgent business).\n"
            '- category: "customer", "vendor", "internal", "notification", or "other".\n'
            "- reason: ONE short factual line, max 12 words, no speculation.\n\n"
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
                    # Fail SAFE, not open: unknown -> "worth knowing", not the
                    # reply lane. Better to under-rank than to bury real work
                    # under model-unavailable placeholders.
                    t.state = "fyi"
                    t.rank = min(t.rank, 30)
                    t.reason = (t.reason + " (model unavailable)").strip()
            continue
        for n, t in enumerate(batch, 1):
            v = verdicts.get(n)
            if not v:
                # No verdict for this row = default to fyi, same reasoning.
                t.state = "fyi"
                t.rank = min(t.rank, 30)
                continue
            st = str(v.get("state", "")).strip().lower()
            t.state = st if st in ("needs_reply", "fyi", "resolved") else "fyi"
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
    # Outbound-only rows still need a real display name — the merge step fills
    # sender_name from the first To recipient, so this reads it directly.
    sender_name = t.sender_name or ""
    sender_email = t.sender_email or ""
    display_sender = sender_name or sender_email or "(no recipient)"
    return {
        "id": t.id,
        "subject": t.subject or "(no subject)",
        "senderName": display_sender,
        "senderEmail": sender_email,
        "outboundOnly": t.last_direction == "out" and t.msg_count > 0
                        and not any(True for r in json.loads(t.messages_json or "[]") if r["dir"] == "in"),
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
        "dismissed": bool(t.dismissed_at),
    }


def _overview(db: Session, user: User) -> dict:
    rows = (
        db.query(EmailThread)
        .filter(EmailThread.user_id == user.id)
        .all()
    )
    # "Waiting on them" is gone by design: once you've replied, it isn't a
    # task worth eye time. The classification still lives in the DB (useful
    # data), but never appears in the tab. Dismissed threads land in their
    # own bucket so the user can un-hide from a mistake.
    lanes = {"needsReply": [], "fyi": [], "cleanup": [], "dismissed": []}
    # Split the "not shown" counter into its two real meanings so the
    # footnote can say WHICH kind of "handled" each number is.
    resolved = 0  # closed themselves — ack, closure language, ooo, self-notes
    waiting = 0   # you spoke last; ball's in their court
    for t in rows:
        if t.dismissed_at:
            lanes["dismissed"].append(t)
            continue
        if t.state == "needs_reply":
            lanes["needsReply"].append(t)
        elif t.state == "fyi":
            lanes["fyi"].append(t)
        elif t.state == "bulk":
            lanes["cleanup"].append(t)
        elif t.state == "resolved":
            resolved += 1
        elif t.state == "waiting":
            waiting += 1
    lanes["needsReply"].sort(key=lambda t: (-t.rank, t.last_at))
    for k in ("fyi", "cleanup", "dismissed"):
        lanes[k].sort(key=lambda t: t.last_at or "", reverse=True)

    state = db.get(EmailSyncState, user.id)
    pref = db.get(UserPref, user.id)
    return {
        "lastSync": state.last_sync.isoformat() if state and state.last_sync else "",
        "resolvedCount": resolved + waiting,  # backward-compat total
        "resolvedClosed": resolved,
        "resolvedWaiting": waiting,
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


@router.post("/threads/{thread_id}/dismiss")
def dismiss_thread(
    thread_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Hide a thread from the lanes (moves it into the Dismissed bucket).
    Stamps the current last_message_id — if a NEW message lands after this
    the dismiss auto-clears at the next sync and the thread re-surfaces.
    Reversible via /undismiss; nothing is deleted from Outlook or the DB."""
    t = db.query(EmailThread).filter(
        EmailThread.id == thread_id, EmailThread.user_id == user.id
    ).first()
    if t is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    t.dismissed_at = _now()
    t.dismissed_at_msg_id = t.last_message_id or ""
    db.commit()
    telemetry.log_event(user.id, "email", "dismiss")
    return {"ok": True, "dismissed": True}


@router.post("/threads/{thread_id}/undismiss")
def undismiss_thread(
    thread_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Restore a dismissed thread to its normal lane. Backs out an accidental
    dismiss without waiting for a new message."""
    t = db.query(EmailThread).filter(
        EmailThread.id == thread_id, EmailThread.user_id == user.id
    ).first()
    if t is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    t.dismissed_at = None
    t.dismissed_at_msg_id = ""
    db.commit()
    telemetry.log_event(user.id, "email", "undismiss")
    return {"ok": True, "dismissed": False}


# --- Ops for starbot chat (read-only) ----------------------------------------
#
# Same design as the accounts/projects ops: the domain logic sits here with
# the module, so chat.py can just wire it up. Read-only on purpose — writing
# from chat (dismiss, promote to needs_reply, etc.) is a phase-3 conversation.
# User-scoped: every query filters by the signed-in user's id. Bodies are
# still never in the DB, so the trail is metadata-only.

_STARBOT_STATES = {"needs_reply", "fyi", "cleanup", "dismissed", "all"}


def op_list_ranked_emails(
    db: Session, user: User, state: str = "needs_reply", limit: int = 15
) -> dict:
    """Read starbot's own view of the ranked inbox. `state` picks a lane
    (needs_reply / fyi / cleanup / dismissed / all). Rows are compact — no
    bodies, no per-message trail — so the model gets a scannable list, not a
    dump. `matched` vs `returned` tells it when the list was cut."""
    s = (state or "needs_reply").strip().lower()
    if s not in _STARBOT_STATES:
        return {"error": f"Unknown state '{state}'. Valid: {sorted(_STARBOT_STATES)}"}
    cap = max(1, min(int(limit or 15), 40))
    q = db.query(EmailThread).filter(EmailThread.user_id == user.id)
    if s == "dismissed":
        rows = [t for t in q.all() if t.dismissed_at]
    elif s == "cleanup":
        rows = [t for t in q.filter(EmailThread.state == "bulk").all() if not t.dismissed_at]
    elif s == "all":
        rows = [t for t in q.all() if not t.dismissed_at]
    else:
        db_state = "needs_reply" if s == "needs_reply" else s
        rows = [t for t in q.filter(EmailThread.state == db_state).all() if not t.dismissed_at]
    matched = len(rows)
    if s == "needs_reply":
        rows.sort(key=lambda t: (-t.rank, t.last_at or ""))
    else:
        rows.sort(key=lambda t: t.last_at or "", reverse=True)
    out = []
    for t in rows[:cap]:
        out.append({
            "id": t.id,
            "state": t.state,
            "rank": t.rank,
            "subject": t.subject or "(no subject)",
            "from": f"{t.sender_name or '?'} <{t.sender_email or '?'}>",
            "account": t.account_name or "",
            "category": t.category or "",
            "snippet": (t.snippet or "")[:180],
            "reason": t.reason or "",
            "msgCount": t.msg_count,
            "lastAt": t.last_at or "",
            "flagged": t.is_flagged,
        })
    return {"matched": matched, "returned": len(out), "truncated": matched > len(out),
            "threads": out}


def op_get_email_thread(db: Session, user: User, thread_id: str) -> dict:
    """One triaged thread in full: verdict plus the metadata-only per-message
    trail (id, direction, sender, timestamp, to/cc-me flags). NO bodies —
    starbot must call the existing read_email tool with a Graph message id
    from the trail if it needs the actual content."""
    t = db.query(EmailThread).filter(
        EmailThread.id == thread_id, EmailThread.user_id == user.id
    ).first()
    if t is None:
        return {"error": f"No thread with id {thread_id}"}
    trail = json.loads(t.messages_json or "[]")
    return {
        "id": t.id,
        "conversationId": t.conversation_id,
        "state": t.state,
        "rank": t.rank,
        "category": t.category or "",
        "reason": t.reason or "",
        "subject": t.subject or "(no subject)",
        "from": f"{t.sender_name or '?'} <{t.sender_email or '?'}>",
        "account": t.account_name or "",
        "folder": t.folder or "",
        "webLink": t.web_link or "",
        "firstAt": t.first_at or "",
        "lastAt": t.last_at or "",
        "msgCount": t.msg_count,
        "flagged": t.is_flagged,
        "dismissed": bool(t.dismissed_at),
        "trail": [
            {"messageId": r["id"], "dir": r["dir"], "from": r["from"],
             "name": r.get("name", ""), "at": r["at"],
             "toMe": r.get("toMe", False), "ccMe": r.get("ccMe", False)}
            for r in trail
        ],
    }


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
