"""Eval harness for the triage engine.

WHY THIS EXISTS
---------------
Every rule in this engine was tuned by looking at one real inbox and deciding
a verdict was wrong. That process produced 15 rules in three weeks and no way
to tell whether rule 16 broke rule 4. This file is that missing check: the
documented acceptance cases from CLAUDE.md, expressed as fixtures, scored
automatically.

Run it:
    cd backend && python tests/test_triage.py          # plain, no pytest needed
    cd backend && python -m pytest tests/test_triage.py -q

The harness is deliberately dependency-free at the DB level: `collect_signals`
and `decide` are pure functions, so a case is just a dict. No Postgres, no
Graph, no model, no tokens. That is what makes it runnable on every change.

ADDING A CASE FROM REAL FEEDBACK
--------------------------------
When a worker reports a wrong verdict, add it to `cases.json` with the lane it
belonged in. The scorer then tells you whether a rule change fixes that case
WITHOUT breaking any other. That is the whole point: corrections become
regression tests instead of one-off regex edits.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import triage_engine  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# Cases carry an optional `user` because whose mailbox a thread sits in changes
# the verdict: internal-domain detection and first-name matching both key off
# it, and the reported misclassifications came from three different mailboxes.
USERS = {
    "ethan": ("Ethan Fudge", "ethan@starflooringandremodeling.com"),
    "salam": ("Salam Hasenin", "salam@starflooringandremodeling.com"),
    "bob": ("Bob Bendixen", "bob@starflooringandremodeling.com"),
}
ME = USERS["ethan"][1]
MY_NAME = USERS["ethan"][0]
# One shared account, so hit-list matching can be exercised.
ACCOUNT_DOMAINS = {"conam.com": ("a1", "CONAM Management")}


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def build_thread(case: dict) -> dict:
    """Turn a compact fixture into the arguments `collect_signals` expects.

    `trail` in a fixture is a short list of "in"/"out" markers with optional
    flags, e.g. ["out", "in:toMe"], oldest first — the same shape the real
    messages_json trail has, minus the fields signals never read."""
    me_name, me_email = USERS[case.get("user", "ethan")]
    trail = []
    n = len(case.get("trail", ["in"]))
    for i, spec in enumerate(case.get("trail", ["in"])):
        parts = spec.split(":")
        direction = parts[0]
        opts = set(parts[1:])
        sender = (me_email if direction == "out"
                  else case.get("sender_email", "someone@example.com"))
        trail.append({
            "id": f"m{i}",
            "dir": direction,
            "from": sender,
            "name": case.get("sender_name", ""),
            "at": _iso(case.get("age_days", 1) + (n - 1 - i)),
            "toMe": "toMe" in opts,
            "ccMe": "ccMe" in opts,
            "toInt": [],
            "to": [me_email] if direction == "in" else [case.get("sender_email", "")],
            "flag": False,
        })
    return {
        "trail": trail,
        "subject": case.get("subject", ""),
        "snippet": case.get("snippet", ""),
        "sender_name": case.get("sender_name", ""),
        "sender_email": case.get("sender_email", "someone@example.com"),
        "is_flagged": bool(case.get("flagged")),
        "msg_count": len(trail),
        "last_at": trail[-1]["at"] if trail else "",
        "user_name": me_name,
        "user_email": me_email,
        "account_domains": ACCOUNT_DOMAINS,
        "outbound_map": case.get("outbound_map"),
    }


def run_case(case: dict, *, active_rule_ids=None, user_rules=None):
    kwargs = build_thread(case)
    signals = triage_engine.collect_signals(**kwargs)
    verdict = triage_engine.decide(
        signals,
        sender_email=case.get("sender_email", ""),
        subject=case.get("subject", ""),
        user_rules=user_rules,
        active_rule_ids=active_rule_ids,
    )
    return signals, verdict


def load_cases() -> list[dict]:
    with open(os.path.join(FIXTURES, "cases.json"), encoding="utf-8") as fh:
        return json.load(fh)


def score(cases: list[dict], *, active_rule_ids=None, verbose=True):
    """Run every case and report. `expect` is the state the case must reach;
    "model" means "must be handed to judgment", which is a legitimate expected
    outcome — the rules are not supposed to settle everything."""
    passed, failed = 0, []
    for case in cases:
        rules = None
        if case.get("user_rules"):
            rules = [_DictRule(r) for r in case["user_rules"]]
        # `active_rules` overrides the run's rule set for one case. The string
        # "tier1" means a brand-new inbox (mechanical rules only), which is how
        # a case can assert that a verdict DEPENDS on an opt-in tier-2 rule.
        active = active_rule_ids
        if "active_rules" in case:
            active = (triage_engine.DEFAULT_ACTIVE_RULE_IDS
                      if case["active_rules"] == "tier1"
                      else set(case["active_rules"]))
        signals, verdict = run_case(case, active_rule_ids=active, user_rules=rules)
        ok = verdict.state == case["expect"]
        if "expect_decided_by" in case:
            ok = ok and verdict.decided_by.startswith(case["expect_decided_by"])
        if ok:
            passed += 1
        else:
            failed.append((case, verdict))
    if verbose:
        print(f"\n{passed}/{len(cases)} cases pass")
        for case, v in failed:
            print(f"\n  FAIL  {case['name']}")
            print(f"        expected {case['expect']}"
                  + (f" via {case['expect_decided_by']}" if "expect_decided_by" in case else ""))
            print(f"        got      {v.state} via {v.decided_by}")
            print(f"        reason   {v.reason}")
    return passed, failed


class _DictRule:
    """A user rule expressed as a fixture dict, duck-typed for the engine."""

    def __init__(self, d: dict):
        self.id = d.get("id", "fixture")
        self.kind = d.get("kind", "hard")
        self.scope = d.get("scope", "sender")
        self.pattern = d.get("pattern", "")
        self.action = d.get("action", "demote")
        self.target_state = d.get("target_state", "")
        self.note = d.get("note", "")
        self.active = d.get("active", True)


# --- pytest entry points ------------------------------------------------------

def test_documented_cases():
    """Every acceptance case from CLAUDE.md's 'don't relearn these' list."""
    cases = load_cases()
    passed, failed = score(cases, verbose=False)
    assert not failed, "\n".join(
        f"{c['name']}: expected {c['expect']}, got {v.state} via {v.decided_by}"
        for c, v in failed)


def test_ack_does_not_swallow_a_real_ask():
    """The bug the old `_ACK` regex had: 80 free characters after the ack word
    let a real instruction resolve silently, and resolved threads were never
    rendered, so it was unrecoverable."""
    _, v = run_case({
        "name": "thanks-plus-instruction",
        "trail": ["out", "in:toMe"],
        "sender_email": "marc@example.com",
        "snippet": "Thanks Ethan, also send the invoice to Marc.",
    }, active_rule_ids=triage_engine.LEGACY_TUNED_RULE_IDS)
    assert v.state != "resolved", f"a real ask was swallowed: {v.decided_by}"


def test_recorder_bot_needs_word_boundary():
    """"Bread AI" and "Thread Assistant" matched the old unanchored regex and
    landed in Cleanup at rank 5."""
    for name in ("Bread AI", "Thread Assistant", "Spread Support"):
        sig, v = run_case({
            "name": name, "trail": ["in:toMe"], "sender_name": name,
            "sender_email": "hello@bread.example.com",
            "snippet": "Can you confirm the tile order for Tuesday?",
        }, active_rule_ids=triage_engine.LEGACY_TUNED_RULE_IDS)
        assert not sig["recorder_bot"], f"{name} still reads as a recorder bot"


def test_new_inbox_gets_no_judgment_rules():
    """A brand-new inbox must not inherit the Aug-2026 tuning. Cold outreach
    is the clearest case: demoting it is right for IT and wrong for sales."""
    case = {
        "name": "cold-pitch", "trail": ["in:toMe"],
        "sender_name": "Abacus AI", "sender_email": "growth@abacus.example.com",
        "subject": "Cut your reporting time in half",
        "snippet": "Hi Ethan, do you have 15 minutes on Thursday?",
    }
    _, tuned = run_case(case, active_rule_ids=triage_engine.LEGACY_TUNED_RULE_IDS)
    _, fresh = run_case(case, active_rule_ids=None)   # defaults = tier 1 only
    assert tuned.decided_by == "tier2:cold-outreach"
    assert not fresh.decided_by.startswith("tier2:"), (
        f"new inbox inherited a tier-2 rule: {fresh.decided_by}")


def test_user_rule_beats_company_rule():
    """Scoping your own inbox is the point, so a user rule outranks a company
    default — but never a structural fact."""
    case = {
        "name": "user-promotes-recorder-bot", "trail": ["in:toMe"],
        "sender_name": "Read AI", "sender_email": "support@read.ai",
        "subject": "Your meeting summary",
        "snippet": "Here is your summary for Tuesday's sales meeting.",
        "user_rules": [{"id": "r1", "scope": "sender", "pattern": "support@read.ai",
                        "action": "force_state", "target_state": "needs_reply"}],
        "active_rules": list(triage_engine.LEGACY_TUNED_RULE_IDS),
    }
    rules = [_DictRule(r) for r in case["user_rules"]]
    _, v = run_case(case, active_rule_ids=set(case["active_rules"]), user_rules=rules)
    assert v.state == "needs_reply" and v.decided_by == "user:r1", v.decided_by


def test_structural_beats_user_rule():
    """No rule should claim you did not send a message you sent."""
    rules = [_DictRule({"id": "r1", "scope": "sender", "pattern": "marc@example.com",
                        "action": "promote"})]
    _, v = run_case({
        "name": "you-spoke-last", "trail": ["in:toMe", "out"],
        "sender_email": "marc@example.com", "snippet": "Sent over the quote.",
    }, user_rules=rules)
    assert v.decided_by == "structural:you-spoke-last", v.decided_by


def test_every_verdict_names_its_rule():
    """`decided_by` is what turns a correction into a fix. A verdict without it
    is a verdict nobody can debug."""
    for case in load_cases():
        active = (set(case["active_rules"]) if "active_rules" in case
                  else triage_engine.LEGACY_TUNED_RULE_IDS)
        _, v = run_case(case, active_rule_ids=active)
        assert v.decided_by, f"{case['name']} produced a verdict with no decided_by"


def test_signature_is_stripped_from_the_snippet():
    """Star signatures open with a ~200-char NEW ADDRESS banner and Graph caps
    bodyPreview at 255, so on a forward with no note the signature WAS the
    entire snippet — rows displayed a mailing address and the model was handed
    one to reason about."""
    fwd = (
        "\U0001F4CD NEW ADDRESS - Effective immediately:\r\n"
        "Mailing: 4620 Alvarado Canyon Rd., Suite 18, San Diego, CA 92120\r\n"
        "Salam Hasenin\r\nOwner\r\n"
        "________________________________\r\n"
        "From: Paychex Inc. <noreply@paychex.com>\r\n"
        "Subject: Action required: Print and sign\r\n\r\n"
        "You must print, review, and sign page 14."
    )
    out = triage_engine.fresh(fwd)
    assert "NEW ADDRESS" not in out, f"signature leaked into the snippet: {out!r}"
    assert "print" in out.lower(), f"forwarded content was lost: {out!r}"


def test_signature_strip_does_not_eat_a_real_ask():
    """The counterpart risk: cutting at a sign-off must never remove content.
    'Thanks,' is deliberately NOT a signature marker — that would recreate the
    ack bug that silently hid real work."""
    out = triage_engine.fresh("Thanks,\r\n\r\nCan you also send the invoice to accounting?")
    assert "invoice" in out, f"stripped a real ask: {out!r}"
    out2 = triage_engine.fresh("Yes this looks correct.\r\n\r\nBest regards,\r\nEthan")
    assert out2.startswith("Yes this looks correct"), out2
    assert "Best regards" not in out2, out2


def test_signals_are_json_serializable():
    """Signals are stored on the row; a non-serializable value would break sync
    for the whole mailbox, not just one thread."""
    for case in load_cases():
        sig, _ = run_case(case, active_rule_ids=triage_engine.LEGACY_TUNED_RULE_IDS)
        json.dumps(sig)


if __name__ == "__main__":
    cases = load_cases()
    print("=" * 68)
    print("Triage engine eval")
    print("=" * 68)
    print("\nLegacy tuned rule set (Ethan's mailbox):")
    score(cases, active_rule_ids=triage_engine.LEGACY_TUNED_RULE_IDS)

    print("\n" + "-" * 68)
    print("Invariant checks")
    print("-" * 68)
    checks = [
        ("ack does not swallow a real ask", test_ack_does_not_swallow_a_real_ask),
        ("recorder-bot regex is word-bounded", test_recorder_bot_needs_word_boundary),
        ("new inbox gets no judgment rules", test_new_inbox_gets_no_judgment_rules),
        ("user rule beats company rule", test_user_rule_beats_company_rule),
        ("structural beats user rule", test_structural_beats_user_rule),
        ("every verdict names its rule", test_every_verdict_names_its_rule),
        ("signature stripped from snippet", test_signature_is_stripped_from_the_snippet),
        ("signature strip keeps a real ask", test_signature_strip_does_not_eat_a_real_ask),
        ("signals are JSON-serializable", test_signals_are_json_serializable),
    ]
    bad = 0
    for label, fn in checks:
        try:
            fn()
            print(f"  pass  {label}")
        except AssertionError as err:
            bad += 1
            print(f"  FAIL  {label}\n        {err}")
    print()
    sys.exit(1 if (bad or score(cases, active_rule_ids=triage_engine.LEGACY_TUNED_RULE_IDS,
                                verbose=False)[1]) else 0)
