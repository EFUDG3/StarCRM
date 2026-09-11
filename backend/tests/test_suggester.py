"""Integration check for the correction -> suggestion -> rule loop.

Uses a SQLite in-memory DB injected in place of `database`, so it NEVER
touches the live Azure Postgres that backend/.env points at. Run it:

    cd backend && python tests/test_suggester.py

What it proves, in order: the 3-correction threshold holds; a correction
produces both a sender rule AND a "turn off the rule that keeps misfiring"
suggestion; re-running makes no duplicates; accepting creates a real rule and
closes its evidence; that rule then actually decides the sender's next email;
a rejected pattern is never proposed again; and an "already handled" chip
never generalizes into a standing rule.
"""
import json
import os
import sys
import types

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

# --- Stub `database` with SQLite BEFORE anything imports it -------------------
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

fake = types.ModuleType("database")
engine = create_engine("sqlite://", future=True)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
fake.engine, fake.Base, fake.SessionLocal = engine, Base, SessionLocal


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


fake.get_db = get_db
fake.DATABASE_URL = "sqlite://"
sys.modules["database"] = fake

os.environ.setdefault("ANTHROPIC_API_KEY", "not-used-in-this-test")

import models  # noqa: E402
import triage_engine  # noqa: E402
import triage_feedback  # noqa: E402

Base.metadata.create_all(bind=engine)
db = SessionLocal()

user = models.User(id="utest", name="Ethan Fudge", email="ethan@starflooringandremodeling.com")
db.add(user)
db.commit()

FAILS = []


def check(label, cond, detail=""):
    print(("  pass  " if cond else "  FAIL  ") + label + (f"\n        {detail}" if not cond and detail else ""))
    if not cond:
        FAILS.append(label)


def add_feedback(sender, state, decided_by, signals, why="", subject="Sub"):
    fb = models.TriageFeedback(
        user_id=user.id, thread_id="t" + sender + state + str(len(sender)),
        predicted_state="needs_reply", predicted_rank=70, decided_by=decided_by,
        reason="test", corrected_state=state, why=why,
        signals_json=json.dumps(signals),
        sender_email=sender, sender_domain=sender.split("@")[1], subject=subject,
    )
    db.add(fb)
    db.commit()
    return fb


print("=" * 68)
print("Suggester integration check (SQLite, throwaway)")
print("=" * 68)

# --- 1. Below threshold: no suggestion --------------------------------------
add_feedback("support@datanet.example.com", "fyi", "tier2:ticket-update", {"ticket": True, "robot_sender": True})
add_feedback("support@datanet.example.com", "fyi", "tier2:ticket-update", {"ticket": True, "robot_sender": True})
made = triage_feedback._run_suggester(db, user)
check("2 corrections produce no suggestion", len(made) == 0, f"got {len(made)}")

# --- 2. At threshold: sender rule appears ------------------------------------
add_feedback("support@datanet.example.com", "fyi", "tier2:ticket-update", {"ticket": True, "robot_sender": True})
made = triage_feedback._run_suggester(db, user)
kinds = {(s.scope, s.pattern) for s in made}
check("3rd correction produces a sender suggestion",
      ("sender", "support@datanet.example.com") in kinds, f"got {kinds}")
check("also suggests disabling the misfiring company rule",
      ("company_rule", "tier2:ticket-update") in kinds, f"got {kinds}")

sender_sugg = next(s for s in made if s.scope == "sender")
print(f"        rationale: {sender_sugg.rationale}")
print(f"        describe : {triage_feedback._serialize_suggestion(sender_sugg)['describe']}")
rule_sugg = next(s for s in made if s.scope == "company_rule")
print(f"        rationale: {rule_sugg.rationale}")
print(f"        describe : {triage_feedback._serialize_suggestion(rule_sugg)['describe']}")

# --- 3. Idempotent: re-running does not duplicate ---------------------------
again = triage_feedback._run_suggester(db, user)
check("re-running the suggester makes no duplicates", len(again) == 0, f"got {len(again)}")

# --- 4. Accepting creates a rule and closes the evidence --------------------
res = triage_feedback.accept_suggestion(sender_sugg.id, user=user, db=db)
rules = db.query(models.TriageRule).filter(models.TriageRule.user_id == user.id).all()
check("accepting a suggestion creates exactly one rule", len(rules) == 1, f"got {len(rules)}")
check("the rule is marked as suggested", rules and rules[0].source == "suggested")
check("its evidence is closed so it stops re-suggesting",
      all(f.status == "ruled" for f in db.query(models.TriageFeedback)
          .filter(models.TriageFeedback.sender_email == "support@datanet.example.com").all()))
print(f"        rule reads: {triage_feedback._serialize_rule(rules[0])['describe']}")

# --- 5. The accepted rule actually decides a thread -------------------------
hard, active, guidance = None, None, None
hard = [r for r in rules if r.kind == "hard"]
sig = triage_engine.collect_signals(
    trail=[{"id": "m0", "dir": "in", "from": "support@datanet.example.com", "at": "2026-09-01T10:00:00Z",
            "toMe": True, "ccMe": False, "to": ["ethan@starflooringandremodeling.com"], "flag": False}],
    subject="A Response to your Ticket #9001", snippet="Can you confirm the workstation?",
    sender_name="DataNet Support", sender_email="support@datanet.example.com",
    is_flagged=False, msg_count=1, last_at="2026-09-01T10:00:00Z",
    user_name="Ethan Fudge", user_email="ethan@starflooringandremodeling.com",
    account_domains={},
)
v = triage_engine.decide(sig, sender_email="support@datanet.example.com",
                         subject="A Response to your Ticket #9001",
                         user_rules=hard,
                         active_rule_ids=triage_engine.LEGACY_TUNED_RULE_IDS)
check("the accepted rule now decides that sender's mail",
      v.decided_by == f"user:{rules[0].id}" and v.state == "fyi",
      f"got {v.state} via {v.decided_by}")
print(f"        audit line: {v.reason}")

# --- 6. Rejecting a suggestion blocks it forever ----------------------------
add_feedback("promo@vendor.example.com", "bulk", "score:no-evidence", {"cold": True})
add_feedback("promo@vendor.example.com", "bulk", "score:no-evidence", {"cold": True})
add_feedback("promo@vendor.example.com", "bulk", "score:no-evidence", {"cold": True})
made2 = triage_feedback._run_suggester(db, user)
s2 = next((s for s in made2 if s.scope == "sender" and s.pattern == "promo@vendor.example.com"), None)
check("a second sender pattern also suggests", s2 is not None)
if s2:
    triage_feedback.reject_suggestion(s2.id, user=user, db=db)
    add_feedback("promo@vendor.example.com", "bulk", "score:no-evidence", {"cold": True})
    made3 = triage_feedback._run_suggester(db, user)
    blocked = not any(s.scope == "sender" and s.pattern == "promo@vendor.example.com" for s in made3)
    check("a rejected pattern is never suggested again", blocked)

# --- 7. "already handled" must NOT generalize to the sender -----------------
for i in range(4):
    add_feedback(f"person{i}@acme.example.com", "resolved", "score:evidence",
                 {"answered": True}, why="already_handled")
made4 = triage_feedback._run_suggester(db, user)
leaked = [s for s in made4 if s.scope in ("sender", "domain") and "acme.example.com" in s.pattern]
check("'already handled' does not become a sender/domain rule",
      not leaked, f"leaked {[(s.scope, s.pattern) for s in leaked]}")

# --- 8. A manual move SURVIVES a re-sync ------------------------------------
# The trust-critical path: re-reading the mailbox must not revert a correction.
import email_triage  # noqa: E402

thread = models.EmailThread(
    user_id=user.id, conversation_id="conv-hold",
    subject="Ad for bids_ Group C Shade", snippet="Bid due 10/6/26 1:00 pm.",
    sender_name="SDUSD", sender_email="no-reply@reproconnect.example.com",
    messages_json=json.dumps([{
        "id": "m1", "dir": "in", "from": "no-reply@reproconnect.example.com",
        "at": "2026-09-01T10:00:00Z", "toMe": True, "ccMe": False,
        "to": [user.email], "flag": False,
    }]),
    last_message_id="m1", last_at="2026-09-01T10:00:00Z", last_direction="in",
    msg_count=1, state="needs_reply", rank=75,
)
db.add(thread)
db.commit()

# The user disagrees and files it as informational.
from schemas import ThreadReclassifyIn  # noqa: E402

triage_feedback.reclassify_thread(
    thread.id, ThreadReclassifyIn(state="fyi"), user=user, db=db)
check("a manual move is recorded with the message id it was made against",
      thread.state == "fyi" and thread.manual_msg_id == "m1",
      f"state={thread.state} manual_msg_id={thread.manual_msg_id!r}")

# Now re-triage it the way a full resync would, with rules that WOULD promote it.
email_triage._triage_thread(
    thread, user, {}, outbound_map={}, user_rules=[],
    active_rule_ids=triage_engine.LEGACY_TUNED_RULE_IDS)
check("a re-sync does NOT revert the user's verdict",
      thread.state == "fyi" and thread.decided_by == "user:manual-move",
      f"got {thread.state} via {thread.decided_by}")
check("signals are still refreshed under the hold",
      "bid_invitation" in (thread.signals_json or ""),
      f"signals={thread.signals_json!r}")

# A genuinely NEW message releases the hold.
thread.last_message_id = "m2"
email_triage._triage_thread(
    thread, user, {}, outbound_map={}, user_rules=[],
    active_rule_ids=triage_engine.LEGACY_TUNED_RULE_IDS)
check("a new message on the thread releases the hold",
      thread.decided_by == "tier1:bid-invitation",
      f"got {thread.state} via {thread.decided_by}")

print()
print(f"{'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' FAILURES: ' + ', '.join(FAILS)}")
db.close()
sys.exit(1 if FAILS else 0)
