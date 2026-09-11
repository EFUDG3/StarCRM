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
import triage_engine
from database import get_db
from models import (Account, EmailSyncState, EmailThread, GlossaryEntry,
                    TriageFeedback, TriageRule, TriageSuggestion, User, UserPref)
from schemas import EmailPrefIn, TriageProfileIn, TriageRuleIn, ThreadReclassifyIn

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
    """Snippet extraction — ONE implementation, in triage_engine.

    It was duplicated here, which is how the two copies would have drifted
    the moment either was tuned. The signature-stripping and bare-forward
    fallback logic belongs with the signals that read it.
    """
    return triage_engine.fresh(text)


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
    # Settle which company rules this user gets BEFORE any triage runs, so an
    # existing mailbox never gets silently re-sorted by a changed default.
    ensure_rule_set(db, user)
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
    # This user's rules, read once per sync. Hard rules are evaluated inside
    # the engine; the guidance text rides into the model prompt.
    hard_rules, active_ids, guidance = _user_rules(db, user)
    ambiguous: list[EmailThread] = []
    for t in touched.values():
        _triage_thread(t, user, domains, outbound_map=outbound_map,
                       user_rules=hard_rules, active_rule_ids=active_ids)
        if t.state == "model":
            ambiguous.append(t)
    if ambiguous:
        _triage_model(db, user, ambiguous, guidance=guidance)
    _bump_rule_hits(db, user, list(touched.values()))

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


def ensure_rule_set(db: Session, user: User) -> str:
    """Decide, ONCE per user, which company rules they start with.

    The rule that matters here: **a mailbox that already has triaged mail keeps
    the rules that produced it; a brand-new mailbox starts clean.**

    Every tier-2 judgment rule was tuned against one IT-admin inbox in Aug
    2026. Handing those to a salesperson as defaults gives them someone else's
    idea of what matters — demoting cold outreach is right for IT and wrong for
    sales, whose job it is. But silently re-sorting an inbox that has been
    running on those rules for three weeks is its own broken promise. Keying on
    "does this user already have threads" satisfies both without hardcoding
    anyone's email address.

    Returns "legacy", "fresh", or "existing" for the log line."""
    pref = db.get(UserPref, user.id)
    if pref is None:
        pref = UserPref(user_id=user.id)
        db.add(pref)
    if pref.triage_rules_json:
        return "existing"

    has_history = (
        db.query(EmailThread.id)
        .filter(EmailThread.user_id == user.id)
        .first()
        is not None
    )
    if has_history:
        pref.triage_rules_json = json.dumps(sorted(triage_engine.LEGACY_TUNED_RULE_IDS))
        mode = "legacy"
    else:
        pref.triage_rules_json = json.dumps(sorted(triage_engine.DEFAULT_ACTIVE_RULE_IDS))
        mode = "fresh"
    db.commit()
    log.info("triage rule set initialized (%s) for %s", mode, user.email)
    return mode


def _user_rules(db: Session, user: User) -> tuple[list, set[str], str]:
    """This user's rules, split the way the engine needs them:
    (hard rules, active company-rule ids, prompt guidance text).

    A NULL `triage_rules_json` means the inbox was never initialized, so only
    tier-1 mechanical rules apply. That is deliberate: the judgment rules in
    tier 2 were tuned against one IT-admin mailbox, and inheriting someone
    else's idea of what matters is exactly the failure this redesign fixes."""
    rules = (
        db.query(TriageRule)
        .filter(TriageRule.user_id == user.id, TriageRule.active == True)  # noqa: E712
        .order_by(TriageRule.created_at)
        .all()
    )
    hard = [r for r in rules if r.kind == "hard"]
    soft = [r for r in rules if r.kind == "soft"]

    pref = db.get(UserPref, user.id)
    if pref is not None and pref.triage_rules_json:
        try:
            active = set(json.loads(pref.triage_rules_json))
        except Exception:
            active = set(triage_engine.DEFAULT_ACTIVE_RULE_IDS)
    else:
        active = set(triage_engine.DEFAULT_ACTIVE_RULE_IDS)

    guidance_bits: list[str] = []
    if pref is not None and (pref.triage_profile or "").strip():
        guidance_bits.append(f"About this user's job: {pref.triage_profile.strip()}")
    for r in soft:
        line = (r.text or "").strip()
        if line:
            guidance_bits.append(f"- {line}")
    return hard, active, "\n".join(guidance_bits)


def _bump_rule_hits(db: Session, user: User, threads: list[EmailThread]) -> None:
    """Count how often each user rule actually fired.

    Not decoration: hit counts separate rules doing real work from one-off
    annoyances someone typed once, they drive "this rule has never fired,
    delete it?" prompts, and they answer "what has the bot learned" with a
    number instead of a claim."""
    hits: dict[str, int] = {}
    for t in threads:
        by = t.decided_by or ""
        if by.startswith("user:"):
            hits[by[5:]] = hits.get(by[5:], 0) + 1
    if not hits:
        return
    for rule in db.query(TriageRule).filter(
            TriageRule.user_id == user.id, TriageRule.id.in_(list(hits))).all():
        rule.hit_count = (rule.hit_count or 0) + hits[rule.id]
        rule.last_hit_at = _now()


def _triage_thread(t: EmailThread, user: User, domains: dict,
                   outbound_map: dict[str, list[str]] | None = None,
                   user_rules: list | None = None,
                   active_rule_ids: set[str] | None = None) -> None:
    """Observe, then decide. Both halves live in triage_engine; this function
    only moves data between the thread row and the engine.

    Replaces the old `_triage_deterministic` cascade of 12 early returns,
    where rule ORDER silently decided outcomes and a wrong verdict could not
    be traced to the rule that produced it."""
    trail = json.loads(t.messages_json or "[]")
    signals = triage_engine.collect_signals(
        trail=trail,
        subject=t.subject or "",
        snippet=t.snippet or "",
        sender_name=t.sender_name or "",
        sender_email=t.sender_email or "",
        is_flagged=bool(t.is_flagged),
        msg_count=int(t.msg_count or 0),
        last_at=t.last_at or "",
        user_name=user.name or "",
        user_email=user.email or "",
        account_domains=domains,
        outbound_map=outbound_map,
    )
    verdict = triage_engine.decide(
        signals,
        sender_email=t.sender_email or "",
        subject=t.subject or "",
        user_rules=user_rules,
        active_rule_ids=active_rule_ids,
    )

    # The account association is a signal, so it comes back from the engine
    # rather than being recomputed here.
    t.account_id = signals.get("account_id")
    t.account_name = signals.get("account_name") or ""

    # MANUAL HOLD. If the user hand-moved this thread and no new message has
    # landed since, their verdict stands and the engine's is discarded. The
    # signals ARE refreshed above/below so a later correction is recorded
    # against current facts, but the lane is theirs.
    #
    # Without this, re-reading the mailbox (a full resync, or any sync that
    # re-touches the thread) silently reverts every correction a user made —
    # which is the fastest possible way to destroy trust in the whole loop.
    if (t.decided_by == "user:manual-move" and t.manual_msg_id
            and t.manual_msg_id == (t.last_message_id or "")):
        t.signals_json = json.dumps(
            {k: v for k, v in signals.items() if v not in (False, "", None)})
        t.triaged_at = _now()
        t._model_hint = ""
        return

    t.state = verdict.state
    t.rank = verdict.rank
    t.category = verdict.category
    t.reason = verdict.reason
    t.decided_by = verdict.decided_by
    t.signals_json = json.dumps(
        {k: v for k, v in signals.items() if v not in (False, "", None)})
    t.model_used = False
    t.triaged_at = _now()
    # Stashed for the model stage; not a column, just in-request state.
    t._model_hint = verdict.model_hint


# --- Stage 5: Haiku on the ambiguous remainder --------------------------------
def _glossary_block(db: Session) -> str:
    rows = db.query(GlossaryEntry).all()
    if not rows:
        return ""
    lines = "\n".join(f"- {g.term}: {g.meaning}" for g in rows[:40])
    return f"\nCompany context (Star Flooring & Remodeling, San Diego):\n{lines}\n"


def _triage_model(db: Session, user: User, threads: list[EmailThread],
                  guidance: str = "") -> None:
    """Batch-classify the threads rules couldn't settle. Metadata only — no
    bodies. On any failure the deterministic verdict stands (fail open into
    the visible lane, never silently drop mail)."""
    gloss = _glossary_block(db)
    for i in range(0, len(threads), _MODEL_BATCH):
        batch = threads[i:i + _MODEL_BATCH]
        items = "\n".join(
            f'{n}. from: {t.sender_name or "?"} <{t.sender_email or "?"}> | '
            f'subject: {(t.subject or "(no subject)")[:100]} | '
            f'hint: {getattr(t, "_model_hint", "") or t.reason or ""} | '
            f'snippet: {(t.snippet or "")[:200]}'
            for n, t in enumerate(batch, 1)
        )
        # Per-user guidance goes AFTER the company instructions so that a
        # user's own standing instruction wins a disagreement — same
        # precedence as hard rules beating company rules in the engine.
        user_block = (
            "\nThis user's own standing instructions — these OVERRIDE the "
            f"general guidance above when they conflict:\n{guidance}\n"
            if guidance.strip() else "")
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
            "Each row carries a `hint` from the rules engine saying why it "
            "reached you. Treat the hint as the specific question to answer "
            "about that row, not as a verdict already reached.\n"
            f"{user_block}"
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
                    t.decided_by = "model:unavailable"
            continue
        for n, t in enumerate(batch, 1):
            v = verdicts.get(n)
            if not v:
                # No verdict for this row = default to fyi, same reasoning.
                t.state = "fyi"
                t.rank = min(t.rank, 30)
                t.decided_by = "model:no-verdict"
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
            # Keep the rule that DEFERRED to the model in the audit trail —
            # "model" alone would lose which rule sent it here, and that is
            # usually the thing a correction needs to fix.
            t.decided_by = f"model(via {t.decided_by})" if t.decided_by else "model"


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
        # The rule that produced this verdict, surfaced so a user can see
        # WHICH rule to correct rather than just that something was wrong.
        "decidedBy": t.decided_by or "",
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
    # "handled" is new (2026-09-02) and exists to close a recovery gap: a
    # thread wrongly marked resolved used to be invisible AND uncorrectable,
    # so the engine's worst failure class — a real ask silently hidden —
    # generated no feedback at all. Collapsed by default in the UI; its
    # absence from the lanes above is still the feature.
    lanes = {"needsReply": [], "fyi": [], "cleanup": [], "dismissed": [],
             "handled": []}
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
            lanes["handled"].append(t)
        elif t.state == "waiting":
            waiting += 1
            lanes["handled"].append(t)
    lanes["needsReply"].sort(key=lambda t: (-t.rank, t.last_at))
    for k in ("fyi", "cleanup", "dismissed", "handled"):
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


@router.post("/resync")
def run_full_resync(
    user: User = Depends(m365.get_session_user), db: Session = Depends(get_db)
) -> dict:
    """Throw away the delta tokens and re-read the whole backfill window.

    WHY THIS EXISTS AS AN ENDPOINT rather than a hand-run SQL statement: some
    fixes change how a message is PARSED, not how it is judged — the
    signature-stripping change to `fresh()` is the first, and there will be
    more. Stored snippets are built at sync time, so `retriage` (which reads
    the stored snippet) cannot fix them, and Graph delta will never hand back a
    message that has not changed. The only route is to re-fetch.

    Doing that per mailbox in psql does not scale past the three pilot users
    and is not something a non-technical user can be asked to do, so it lives
    here: one authenticated call, no database access.

    Cost: a full backfill (~80s and 1-2 cents of Haiku on a 30-day mailbox),
    versus ~19s for a normal incremental Refresh. Not something to run
    casually. Manual corrections are preserved — see the manual hold in
    `_triage_thread`.
    """
    state = db.get(EmailSyncState, user.id)
    if state is not None:
        state.inbox_delta = ""
        state.sent_delta = ""
        db.commit()
    token = m365.get_graph_token(user, db)
    try:
        stats = sync_user(db, user, token)
    except graph.GraphError as err:
        raise HTTPException(status_code=err.status if err.status == 401 else 502,
                            detail=str(err))
    out = _overview(db, user)
    out["syncStats"] = stats
    out["fullResync"] = True
    telemetry.log_event(user.id, "email", "resync",
                        f"changed={stats.get('changed')} threads={stats.get('threads')}")
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
