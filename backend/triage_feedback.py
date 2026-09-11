"""The learning loop: corrections in, per-user rules out.

Three moving parts, in the order a user experiences them:

  1. RECLASSIFY  The user drags a thread into the lane it belonged in. That
                 click writes a TriageFeedback row carrying a SNAPSHOT of the
                 signals the engine saw and the rule that fired. The action
                 alone would be useless — "moved to FYI" cannot be turned into
                 a rule, but "moved to FYI, sender was support@datanet.com,
                 tier2:ticket-update fired, ticket+robot_sender were true" can.

  2. SUGGEST     When 3+ corrections share a pattern, propose a rule. Three is
                 the threshold because one correction is a one-off and two is
                 a coincidence. Suggestions are NEVER auto-applied: approval is
                 the trust gate. A rule the user tapped Add on feels like
                 training; a rule that silently reshapes their inbox is how
                 people stop believing the tab.

  3. RE-RUN      Hard rules are deterministic, so a rule change replays over
                 every stored thread with no Graph call and no tokens. The
                 user saves a rule and the tab visibly re-sorts. That immediate
                 response is the difference between a feedback loop that feels
                 like learning and one that feels like paperwork.

The suggester also proposes DISABLING a company rule that keeps being wrong,
which is the highest-value suggestion we can make: it retires a bad default
for one person instead of leaving them to fight it thread by thread.
"""
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import email_triage
import m365
import telemetry
import triage_engine
from database import get_db
from models import (EmailThread, TriageFeedback, TriageRule, TriageSuggestion,
                    User, UserPref)
from schemas import ThreadReclassifyIn, TriageProfileIn, TriageRuleIn

log = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/email", tags=["email-feedback"])

# 3 corrections sharing a pattern = a rule worth proposing. One is a one-off,
# two is a coincidence.
SUGGEST_THRESHOLD = 3

# Corrections whose chip implies the user is telling us about the SENDER
# rather than about this one message. These generalize; "already handled"
# does not (it is about that thread's history, not the sender).
_GENERALIZING_CHIPS = {"not_relevant", "not_a_task", "wrong_sender_read", "is_a_task", None, ""}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- Reclassify: the correction ------------------------------------------------

@router.post("/threads/{thread_id}/reclassify")
def reclassify_thread(
    thread_id: str,
    payload: ThreadReclassifyIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Move a thread to the lane the user says it belongs in, and record WHY
    the engine was wrong.

    The correction outlives the thread on purpose (TriageFeedback has no FK to
    email_threads): threads are pruned at 90 days, but the lesson should not
    be. Returns any suggestion this correction just unlocked so the UI can
    surface the card immediately, while the user still remembers the thread."""
    t = db.query(EmailThread).filter(
        EmailThread.id == thread_id, EmailThread.user_id == user.id
    ).first()
    if t is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    target = payload.state
    if target == t.state:
        return {"ok": True, "state": t.state, "unchanged": True}

    try:
        signals = json.loads(t.signals_json or "{}")
    except Exception:
        signals = {}

    fb = TriageFeedback(
        user_id=user.id,
        thread_id=t.id,
        conversation_id=t.conversation_id or "",
        predicted_state=t.state,
        predicted_rank=t.rank or 0,
        decided_by=t.decided_by or "",
        model_used=bool(t.model_used),
        reason=t.reason or "",
        corrected_state=target,
        why=payload.why or "",
        signals_json=t.signals_json or "{}",
        sender_email=t.sender_email or "",
        sender_domain=signals.get("sender_domain", "") or "",
        subject=(t.subject or "")[:300],
    )
    db.add(fb)

    # Apply the user's verdict to the thread. Rank is nudged to the middle of
    # the target lane rather than preserved — the old rank came from a verdict
    # the user just told us was wrong.
    t.state = target
    t.rank = {"needs_reply": 75, "fyi": 30, "resolved": 0, "bulk": 5}[target]
    t.reason = "you moved this here"
    t.decided_by = "user:manual-move"
    # Stamp the message this verdict was made against, so the hold survives a
    # re-sync but releases when a genuinely new message arrives.
    t.manual_msg_id = t.last_message_id or ""
    t.model_used = False
    # A manual move clears a dismiss — the user is filing it, not hiding it.
    t.dismissed_at = None
    t.dismissed_at_msg_id = ""
    db.commit()

    suggestions = _run_suggester(db, user)
    telemetry.log_event(user.id, "email", "reclassify",
                        f"{fb.predicted_state}->{target} by={fb.decided_by} why={fb.why}")
    return {
        "ok": True,
        "state": t.state,
        "suggestions": [_serialize_suggestion(s) for s in suggestions],
    }


# --- The suggester --------------------------------------------------------------

def _existing_rule_patterns(db: Session, user: User) -> set[tuple[str, str]]:
    return {
        (r.scope, (r.pattern or "").lower())
        for r in db.query(TriageRule).filter(TriageRule.user_id == user.id).all()
    }


def _rejected_patterns(db: Session, user: User) -> set[tuple[str, str]]:
    """Patterns the user already said no to. The suggester must never propose
    these again — a suggestion that keeps coming back reads as nagging, and
    the whole mechanism depends on the user believing Add is their choice."""
    return {
        (s.scope, (s.pattern or "").lower())
        for s in db.query(TriageSuggestion).filter(
            TriageSuggestion.user_id == user.id,
            TriageSuggestion.status == "rejected").all()
    }


def _run_suggester(db: Session, user: User) -> list[TriageSuggestion]:
    """Group open corrections into candidate rules. Pure counting, no model
    call — the patterns we look for are few and explicit, and a deterministic
    suggester is one the user can predict.

    Four candidate shapes, most specific first:
      sender  + corrected_state   -> "mail from X goes to Y"
      domain  + corrected_state   -> "mail from anyone at X goes to Y"
      company rule keeps misfiring -> "turn off rule R"
      signal  + corrected_state   -> "threads where <signal> go to Y"
    """
    open_fb = (
        db.query(TriageFeedback)
        .filter(TriageFeedback.user_id == user.id, TriageFeedback.status == "open")
        .all()
    )
    if len(open_fb) < SUGGEST_THRESHOLD:
        return []

    taken = _existing_rule_patterns(db, user) | _rejected_patterns(db, user)
    made: list[TriageSuggestion] = []

    by_sender: dict[tuple[str, str], list[TriageFeedback]] = defaultdict(list)
    by_domain: dict[tuple[str, str], list[TriageFeedback]] = defaultdict(list)
    by_rule: dict[tuple[str, str], list[TriageFeedback]] = defaultdict(list)
    by_signal: dict[tuple[str, str], list[TriageFeedback]] = defaultdict(list)

    for fb in open_fb:
        generalizes = (fb.why or "") in _GENERALIZING_CHIPS
        if fb.sender_email and generalizes:
            by_sender[(fb.sender_email.lower(), fb.corrected_state)].append(fb)
        if (fb.sender_domain and generalizes
                and fb.sender_domain not in triage_engine.GENERIC_DOMAINS):
            by_domain[(fb.sender_domain.lower(), fb.corrected_state)].append(fb)
        # Strip the "model(via ...)" wrapper so a rule that deferred to the
        # model still gets the blame it earned for routing the thread there.
        by = fb.decided_by or ""
        if by.startswith("model(via ") and by.endswith(")"):
            by = by[len("model(via "):-1]
        if by.startswith(("tier1:", "tier2:")):
            by_rule[(by, fb.corrected_state)].append(fb)
        # Signals are gated by the same chip rule as senders: "already
        # handled" is a statement about THIS thread's history, so it must not
        # become a standing rule of any shape.
        if generalizes:
            try:
                sig = json.loads(fb.signals_json or "{}")
            except Exception:
                sig = {}
            for name, val in sig.items():
                if val is True and name in triage_engine.SIGNAL_LABELS:
                    by_signal[(name, fb.corrected_state)].append(fb)

    def add(scope, pattern, action, target, rationale, evidence, kind="hard", text=""):
        if (scope, str(pattern).lower()) in taken:
            return
        if len(evidence) < SUGGEST_THRESHOLD:
            return
        dupe = db.query(TriageSuggestion).filter(
            TriageSuggestion.user_id == user.id,
            TriageSuggestion.scope == scope,
            TriageSuggestion.pattern == str(pattern),
            TriageSuggestion.action == action,
        ).first()
        if dupe is not None:
            # Refresh the evidence count so the card stays honest as more
            # corrections land, but never resurrect a rejected suggestion.
            if dupe.status == "pending":
                dupe.evidence_count = len(evidence)
                dupe.evidence_json = json.dumps([f.id for f in evidence])
                dupe.rationale = rationale
            return
        s = TriageSuggestion(
            user_id=user.id, kind=kind, scope=scope, pattern=str(pattern),
            action=action, target_state=target or "", text=text,
            rationale=rationale, evidence_count=len(evidence),
            evidence_json=json.dumps([f.id for f in evidence]),
        )
        db.add(s)
        made.append(s)
        taken.add((scope, str(pattern).lower()))

    lane = {"needs_reply": "Needs your reply", "fyi": "Worth knowing",
            "resolved": "Handled", "bulk": "Cleanup"}

    # 1. Same sender, same destination. The most specific and most trustworthy.
    for (sender, state), fbs in sorted(by_sender.items(), key=lambda kv: -len(kv[1])):
        add("sender", sender, "force_state", state,
            f"You moved {len(fbs)} emails from {sender} to {lane.get(state, state)}.",
            fbs)

    # 2. Whole domain — only when the corrections span MORE THAN ONE sender at
    #    that domain, otherwise the sender rule above already covers it and a
    #    domain rule would over-reach.
    for (dom, state), fbs in sorted(by_domain.items(), key=lambda kv: -len(kv[1])):
        if len({(f.sender_email or "").lower() for f in fbs}) < 2:
            continue
        add("domain", dom, "force_state", state,
            f"You moved {len(fbs)} emails from {len({f.sender_email for f in fbs})} "
            f"different people at {dom} to {lane.get(state, state)}.",
            fbs)

    # 3. A company rule that keeps being wrong. This is the highest-value
    #    suggestion available: it retires a bad default for this person
    #    instead of leaving them to fight it one thread at a time.
    for (rule_id, state), fbs in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
        cr = triage_engine.RULES_BY_ID.get(rule_id)
        if cr is None:
            continue
        add("company_rule", rule_id, "disable", state,
            f'The rule "{cr.label}" sent {len(fbs)} threads to the wrong place '
            f"(you moved them to {lane.get(state, state)}).",
            fbs)

    # 4. A signal that consistently means something different for this user.
    #    Requires spread across senders — otherwise it is really a sender rule.
    for (name, state), fbs in sorted(by_signal.items(), key=lambda kv: -len(kv[1])):
        if len({(f.sender_email or "").lower() for f in fbs}) < 3:
            continue
        add("signal", name, "force_state", state,
            f"{len(fbs)} threads where {triage_engine.SIGNAL_LABELS[name]} "
            f"belonged in {lane.get(state, state)}.",
            fbs)

    db.commit()
    if made:
        telemetry.log_event(user.id, "email", "suggest", f"n={len(made)}")
    return made


def _serialize_suggestion(s: TriageSuggestion) -> dict:
    out = {
        "id": s.id,
        "kind": s.kind,
        "scope": s.scope,
        "pattern": s.pattern,
        "action": s.action,
        "targetState": s.target_state or "",
        "text": s.text or "",
        "rationale": s.rationale or "",
        "evidenceCount": s.evidence_count,
        "status": s.status,
    }
    if s.scope == "company_rule":
        cr = triage_engine.RULES_BY_ID.get(s.pattern)
        out["describe"] = f'Turn off: "{cr.label}"' if cr else f"Turn off {s.pattern}"
    else:
        out["describe"] = triage_engine.describe_user_rule(
            _FakeRule(s.scope, s.pattern, s.action, s.target_state))
    return out


class _FakeRule:
    """Minimal duck-type so a pending suggestion can be rendered with the same
    plain-language formatter as a saved rule. The user should read the exact
    same sentence before and after they tap Add."""

    def __init__(self, scope, pattern, action, target_state):
        self.scope, self.pattern = scope, pattern
        self.action, self.target_state = action, target_state
        self.note = ""


# --- Rules tab ------------------------------------------------------------------

def _serialize_rule(r: TriageRule) -> dict:
    return {
        "id": r.id,
        "kind": r.kind,
        "scope": r.scope,
        "pattern": r.pattern,
        "action": r.action,
        "targetState": r.target_state or "",
        "text": r.text or "",
        "note": r.note or "",
        "source": r.source,
        "active": bool(r.active),
        "hitCount": r.hit_count or 0,
        "lastHitAt": r.last_hit_at.isoformat() if r.last_hit_at else "",
        "describe": (r.text or "").strip() if r.kind == "soft"
                    else triage_engine.describe_user_rule(r),
    }


@router.get("/rules")
def get_rules(
    user: User = Depends(m365.get_session_user), db: Session = Depends(get_db)
) -> dict:
    """Everything the Rules tab renders: the user's own rules, the company
    rules with their on/off state, pending suggestions, and the correction
    count behind them.

    Company rules are listed even when the user cannot edit them (structural),
    because a rule you cannot see is a rule you cannot trust."""
    _, active_ids, _ = email_triage._user_rules(db, user)
    pref = db.get(UserPref, user.id)
    rules = (
        db.query(TriageRule)
        .filter(TriageRule.user_id == user.id)
        .order_by(TriageRule.created_at.desc())
        .all()
    )
    sugg = (
        db.query(TriageSuggestion)
        .filter(TriageSuggestion.user_id == user.id,
                TriageSuggestion.status == "pending")
        .order_by(TriageSuggestion.evidence_count.desc())
        .all()
    )
    company = [
        {
            "id": r.id,
            "tier": r.tier,
            "label": r.label,
            "why": r.why,
            "state": r.state,
            "editable": r.tier >= 1,
            "active": r.tier < 0 or r.id in active_ids,
        }
        for r in triage_engine.ALL_COMPANY_RULES
    ]
    corrections = (
        db.query(TriageFeedback)
        .filter(TriageFeedback.user_id == user.id)
        .count()
    )
    return {
        "rules": [_serialize_rule(r) for r in rules],
        "companyRules": company,
        "suggestions": [_serialize_suggestion(s) for s in sugg],
        "profile": (pref.triage_profile if pref else "") or "",
        "correctionCount": corrections,
        "suggestThreshold": SUGGEST_THRESHOLD,
        "signals": triage_engine.SIGNAL_LABELS,
    }


@router.post("/rules")
def create_rule(
    payload: TriageRuleIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Add a rule, then immediately re-run the deterministic pass so the user
    SEES it take effect. A rule that only applies to future mail gives no
    feedback that it worked."""
    if payload.kind == "soft":
        if not (payload.text or "").strip():
            raise HTTPException(status_code=400, detail="Soft rules need text")
        r = TriageRule(user_id=user.id, kind="soft", scope="signal", pattern="",
                       action="demote", text=payload.text.strip(),
                       note=(payload.note or "").strip(), source="manual")
    else:
        if not payload.scope or not (payload.pattern or "").strip():
            raise HTTPException(status_code=400, detail="Hard rules need scope and pattern")
        if payload.scope == "signal" and payload.pattern not in triage_engine.SIGNAL_LABELS:
            raise HTTPException(status_code=400, detail=f"Unknown signal '{payload.pattern}'")
        action = payload.action or "force_state"
        if action == "force_state" and not payload.targetState:
            raise HTTPException(status_code=400, detail="force_state needs targetState")
        r = TriageRule(
            user_id=user.id, kind="hard", scope=payload.scope,
            pattern=payload.pattern.strip().lower(), action=action,
            target_state=payload.targetState or "",
            note=(payload.note or "").strip(), source="manual",
        )
    db.add(r)
    db.commit()
    stats = _retriage(db, user, run_model=False)
    telemetry.log_event(user.id, "email", "rule-add", f"{r.kind}:{r.scope}:{r.pattern}")
    return {"rule": _serialize_rule(r), "retriage": stats}


@router.put("/rules/{rule_id}")
def update_rule(
    rule_id: str,
    payload: TriageRuleIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    r = db.query(TriageRule).filter(
        TriageRule.id == rule_id, TriageRule.user_id == user.id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    if payload.active is not None:
        r.active = bool(payload.active)
    if payload.pattern is not None:
        r.pattern = payload.pattern.strip().lower()
    if payload.targetState is not None:
        r.target_state = payload.targetState
    if payload.action is not None:
        r.action = payload.action
    if payload.text is not None:
        r.text = payload.text.strip()
    if payload.note is not None:
        r.note = payload.note.strip()
    db.commit()
    stats = _retriage(db, user, run_model=False)
    return {"rule": _serialize_rule(r), "retriage": stats}


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    r = db.query(TriageRule).filter(
        TriageRule.id == rule_id, TriageRule.user_id == user.id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(r)
    db.commit()
    stats = _retriage(db, user, run_model=False)
    telemetry.log_event(user.id, "email", "rule-delete")
    return {"ok": True, "retriage": stats}


@router.put("/triage-profile")
def set_profile(
    payload: TriageProfileIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """The 'about my job' paragraph and the set of company rules this user
    keeps switched on. `companyRules` is the full desired list.

    Structural rules are never in the list — they are facts about thread
    shape, not preferences, and letting someone switch off "you sent the last
    message" would only produce nonsense."""
    pref = db.get(UserPref, user.id)
    if pref is None:
        pref = UserPref(user_id=user.id)
        db.add(pref)
    if payload.profile is not None:
        pref.triage_profile = payload.profile.strip()
    if payload.companyRules is not None:
        valid = {r.id for r in triage_engine.TIER1_RULES + triage_engine.TIER2_RULES}
        pref.triage_rules_json = json.dumps(
            sorted(set(payload.companyRules) & valid))
    db.commit()
    stats = _retriage(db, user, run_model=False)
    telemetry.log_event(user.id, "email", "triage-profile")
    return {"ok": True, "retriage": stats}


# --- Suggestion approval --------------------------------------------------------

@router.post("/suggestions/{sugg_id}/accept")
def accept_suggestion(
    sugg_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Turn a suggestion into a real rule. The corrections behind it are marked
    'ruled' so they stop feeding the suggester — otherwise the same pattern
    would be proposed forever."""
    s = db.query(TriageSuggestion).filter(
        TriageSuggestion.id == sugg_id, TriageSuggestion.user_id == user.id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    if s.status != "pending":
        raise HTTPException(status_code=409, detail=f"Already {s.status}")

    if s.scope == "company_rule":
        # Accepting means switching a company rule OFF for this user.
        _, active_ids, _ = email_triage._user_rules(db, user)
        pref = db.get(UserPref, user.id)
        if pref is None:
            pref = UserPref(user_id=user.id)
            db.add(pref)
        pref.triage_rules_json = json.dumps(sorted(active_ids - {s.pattern}))
        created = None
    else:
        created = TriageRule(
            user_id=user.id, kind=s.kind, scope=s.scope, pattern=s.pattern,
            action=s.action, target_state=s.target_state or "", text=s.text or "",
            note="", source="suggested",
        )
        db.add(created)

    s.status = "accepted"
    _close_evidence(db, s, "ruled")
    db.commit()
    stats = _retriage(db, user, run_model=False)
    telemetry.log_event(user.id, "email", "suggest-accept", f"{s.scope}:{s.pattern}")
    return {
        "ok": True,
        "rule": _serialize_rule(created) if created is not None else None,
        "retriage": stats,
    }


@router.post("/suggestions/{sugg_id}/reject")
def reject_suggestion(
    sugg_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Decline a suggestion. The pattern is remembered as rejected so it is
    never proposed again, and its corrections are marked 'dismissed' so they
    do not re-trigger it through a different shape."""
    s = db.query(TriageSuggestion).filter(
        TriageSuggestion.id == sugg_id, TriageSuggestion.user_id == user.id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    s.status = "rejected"
    _close_evidence(db, s, "dismissed")
    db.commit()
    telemetry.log_event(user.id, "email", "suggest-reject", f"{s.scope}:{s.pattern}")
    return {"ok": True}


def _close_evidence(db: Session, s: TriageSuggestion, status: str) -> None:
    try:
        ids = json.loads(s.evidence_json or "[]")
    except Exception:
        return
    if not ids:
        return
    for fb in db.query(TriageFeedback).filter(TriageFeedback.id.in_(ids)).all():
        fb.status = status


# --- Company-wide rule telemetry -----------------------------------------------

@router.get("/corrections/summary")
def corrections_summary(
    days: int = 30,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Which rules are getting it wrong, company-wide.

    AGGREGATE COUNTS ONLY — no senders, no subjects, no message content, no
    user identities. Email is per-user private by design, so this deliberately
    returns nothing that could reconstruct anyone's mail; what it does return
    is the rule-level signal needed to tune COMPANY rules, which no per-user
    view can give you.

    `netMisfires` is the number that matters: a rule with 12 corrections
    against it is a bad default, and the fix belongs in the registry rather
    than in twelve people's personal rule lists.
    """
    since = _now() - timedelta(days=max(1, min(days, 365)))
    rows = (
        db.query(TriageFeedback)
        .filter(TriageFeedback.created_at >= since)
        .all()
    )

    by_rule: dict[str, dict] = {}
    transitions: dict[str, int] = {}
    chips: dict[str, int] = {}
    for fb in rows:
        by = fb.decided_by or "(none)"
        # Credit the rule that ROUTED the thread, not the model it deferred to.
        if by.startswith("model(via ") and by.endswith(")"):
            by = by[len("model(via "):-1]
        slot = by_rule.setdefault(by, {"rule": by, "corrections": 0, "to": {}})
        slot["corrections"] += 1
        slot["to"][fb.corrected_state] = slot["to"].get(fb.corrected_state, 0) + 1
        key = f"{fb.predicted_state} -> {fb.corrected_state}"
        transitions[key] = transitions.get(key, 0) + 1
        if fb.why:
            chips[fb.why] = chips.get(fb.why, 0) + 1

    ranked = sorted(by_rule.values(), key=lambda r: -r["corrections"])
    for r in ranked:
        cr = triage_engine.RULES_BY_ID.get(r["rule"])
        r["label"] = cr.label if cr else r["rule"]
        r["tier"] = cr.tier if cr else None
    return {
        "days": days,
        "totalCorrections": len(rows),
        "distinctMailboxes": len({fb.user_id for fb in rows}),
        "byRule": ranked[:40],
        "transitions": dict(sorted(transitions.items(), key=lambda kv: -kv[1])),
        "reasons": dict(sorted(chips.items(), key=lambda kv: -kv[1])),
    }


# --- Re-run ---------------------------------------------------------------------

def _retriage(db: Session, user: User, *, run_model: bool = False) -> dict:
    """Replay the engine over every stored thread.

    Free when `run_model` is False: no Graph call, no tokens, just signals and
    rules. This is what makes a rule edit feel instant.

    IMPORTANT: threads whose new verdict is "model" are LEFT ALONE on a
    rules-only pass. "model" is not a lane — rendering it would blank the row.
    So a rules-only re-run can move a thread INTO a lane but never strands one
    waiting for judgment it is not going to get."""
    hard_rules, active_ids, guidance = email_triage._user_rules(db, user)
    domains = email_triage._account_domain_map(db)
    outbound_map = email_triage._build_outbound_map(db, user)
    rows = db.query(EmailThread).filter(EmailThread.user_id == user.id).all()

    changed = 0
    ambiguous: list[EmailThread] = []
    for t in rows:
        # A thread the user moved by hand is THEIRS. Re-running rules must not
        # silently undo a correction — that is the fastest way to lose trust
        # in the whole mechanism.
        if t.decided_by == "user:manual-move":
            continue
        before = (t.state, t.rank)
        prior = (t.state, t.rank, t.category, t.reason, t.decided_by, t.model_used)
        email_triage._triage_thread(
            t, user, domains, outbound_map=outbound_map,
            user_rules=hard_rules, active_rule_ids=active_ids)
        if t.state == "model":
            if run_model:
                ambiguous.append(t)
            else:
                # Restore the previous verdict wholesale.
                (t.state, t.rank, t.category, t.reason,
                 t.decided_by, t.model_used) = prior
        if (t.state, t.rank) != before:
            changed += 1

    if ambiguous and run_model:
        email_triage._triage_model(db, user, ambiguous, guidance=guidance)
    email_triage._bump_rule_hits(db, user, rows)
    db.commit()
    return {"threads": len(rows), "changed": changed,
            "modelCalls": len(ambiguous) if run_model else 0}


@router.post("/retriage")
def run_retriage(
    model: bool = False,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """Re-run triage over stored mail with the current rules.

    `?model=false` (default) is free and instant — deterministic rules only.
    `?model=true` also re-judges the ambiguous remainder, which costs roughly
    a cent or two for a 30-day mailbox and is what soft rules need in order to
    take effect on existing threads."""
    stats = _retriage(db, user, run_model=bool(model))
    out = email_triage._overview(db, user)
    out["retriage"] = stats
    return out


# The one-time legacy rule seed lives in email_triage.ensure_rule_set(), which
# sync_user calls — putting it here would make the import cycle real
# (triage_feedback -> email_triage already).
