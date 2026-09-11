"""Triage decision engine: signals in, one auditable verdict out.

WHY THIS MODULE EXISTS
----------------------
The first version of the triage lived in one function, `_triage_deterministic`,
built as a cascade of 12 early returns. Every rule that returned made every
later rule unreachable, which produced three problems that only got worse as
rules accumulated:

  1. Rule ORDER decided outcomes, and the order was accidental. The
     recorder-bot rule ran before the hit-list account check was consulted,
     so a real customer whose sender name matched went to Cleanup at rank 5.
  2. Rules SHADOWED each other. "Ticket #4412 has been resolved" hit the
     ticket rule and went to the model, so the closure rule written to catch
     exactly that case never ran.
  3. Nothing recorded WHY. The verdict carried a prose reason but not the
     signals behind it, so "this was classified wrong" could not be traced to
     the rule that did it — which made every correction a guess.

The fix is to separate observation from judgment:

    collect_signals(thread, ...) -> dict   # never returns early, sees everything
    decide(signals, rules)      -> Verdict # priority-ordered rules, first match wins

Signals are stored on the thread row, so a correction three days later can be
replayed against the exact facts the engine saw. `decided_by` names the rule
that fired. Those two fields are what turn user corrections into rules instead
of into another regex in a cascade.

THE THREE TIERS (and why new inboxes start nearly empty)
--------------------------------------------------------
Every rule in the original cascade was tuned against ONE mailbox (Ethan's, an
IT admin). Shipping those as company-wide defaults means a salesperson inherits
an IT admin's idea of what matters — cold outreach demoted to FYI is correct
for IT and wrong for sales, whose job IS cold outreach.

So company rules are split:

  STRUCTURAL  Facts about thread shape, not judgments. Always on, not editable.
              "You sent the last message" is not an opinion.
  Tier 1      Mechanical automation detection (no-reply senders, unsubscribe
              copy, out-of-office, explicit closure language). On for everyone,
              user may disable. Nobody should have to teach the bot that a
              newsletter is a newsletter.
  Tier 2      JUDGMENT rules, seeded from Ethan's tuning but INACTIVE on a new
              inbox. They are kept here because several have real company-wide
              value, and they become per-user suggestions once a user's own
              corrections corroborate them. This is the "built by the user over
              time" property: the bot earns each of these rules per person
              rather than assuming them.

User rules are evaluated BEFORE tier 1 and tier 2 but AFTER structural, because
scoping your own inbox is the point — but no rule should claim you didn't send
a message you sent.

PRECEDENCE, stated once (the order below is the whole contract):

    structural  ->  user hard rules  ->  tier 1  ->  tier 2 (if enabled)
                ->  deterministic scoring  ->  model
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

# --- Pattern library ----------------------------------------------------------
#
# These moved here from email_triage.py unchanged EXCEPT where a bug is called
# out in a comment. They are matched by the rules below, never applied directly.

BULK_SENDERS = re.compile(
    r"^(no-?reply|do-?not-?reply|donotreply|notifications?|notify|newsletter|"
    r"news|mailer(-daemon)?|postmaster|alerts?|updates|marketing|promo(tions)?|"
    r"automated|noresponse|bounce)[@.+_-]", re.I)
BULK_SUBJECT = re.compile(
    r"(verification code|security code|verify your .{0,24}(email|account|identity)|"
    r"password reset|quarantine digest|confirm your (email|subscription)|"
    r"account (successfully )?created|welcome to|get started with|free trial|"
    r"webinar|your receipt|your invoice is ready|quick setup before your zoom)", re.I)
BULK_COPY = re.compile(
    r"(unsubscribe|view (this email )?in (your )?browser|manage (your )?preferences|"
    r"email preferences|stop receiving these)", re.I)
TICKET_SUBJECT = re.compile(
    r"(^| )(a response to your ticket|re: ticket #|new comment on ticket|"
    r"ticket #\d|\[?ticket:? ?#?\d)", re.I)
# FIXED: the original had no word boundary, so "Bread AI", "Thread Assistant"
# and "spread support" matched and went to Cleanup at rank 5. Anchored now.
RECORDER_BOT = re.compile(
    r"(\bread(\.?ai| ai| assistant| support)\b|\bfathom\b|\botter\.ai\b|\bgrain\.co\b)", re.I)
CLOSURE = re.compile(
    r"(has been (resolved|closed|completed)|ticket .{0,25}(closed|resolved)|"
    r"case (is )?closed|marked as (resolved|complete)|your (request|issue) (was|has been) (resolved|closed)|"
    r"no (further )?action (is )?(needed|required)|this is an automated confirmation)", re.I)
OOO = re.compile(r"(out of (the )?office|automatic reply|auto-?reply)", re.I)
# NARROWED: the original allowed 80 free characters after the ack word, so
# "Thanks Ethan, also send the invoice to Marc." resolved silently and became
# unrecoverable (resolved threads were never rendered). Now the whole fresh
# text must BE an acknowledgment: an ack word, optional name, optional short
# closing clause with no imperative verb. Anything longer goes to judgment.
ACK = re.compile(
    r"^(got it|sounds good|perfect|great|thank(s| you)|ok(ay)?|will do|"
    r"no problem|awesome|received|noted|appreciate it|much appreciated)"
    r"[\s,!.]*(again|so much|a lot|for (the|your) \w+)?[\s,!.]*"
    r"([A-Z][a-z]+)?[\s,!.]*$", re.I)
# Sender addresses/names that read as automation even when they carry a human
# name. Marketing mail greets you by name; support acks end in "did we help?".
ROBOT_SENDER = re.compile(
    r"(support|customer service|customer care|helpdesk|team$|notifications?|"
    r"accounts?@|hello@|hi@|contact@|sales@|info@|hey@|growth@|assistant)", re.I)

# --- Patterns added 2026-09-02 from the first real misclassification batch ---
#
# Six of the ten reported misfires came from the "mechanical" bulk rules, which
# had a flawed premise: they treated "no human reads a reply to this address"
# as "there is nothing here for you to do". Those are different claims. A state
# compliance notice with a discontinuance deadline and a school-district bid
# invitation both arrive from no-reply senders.

# An explicit demand with a consequence. Gated on the mail being addressed to
# the user (see tier1:action-required) because "Action Required" also shows up
# in vendor subject lines on threads someone else is handling.
DEMANDS_ACTION = re.compile(
    r"(response needed|action required|action needed|required action|"
    r"must (be )?(submit|provide|complete|sign|return|respond)|"
    r"you must (print|sign|submit|provide|complete|respond|review)|"
    r"(on or before|no later than|due by|respond by|reply by|submit by) \w|"
    r"failure to (provide|respond|submit|comply)|"
    r"to avoid (discontinuance|cancellation|penalt|suspension)|"
    r"signature required|please (complete|fill out|sign|submit)( and return)?|"
    r"(complete|return|sign) the attached|past due|overdue|"
    r"payment (failed|declined))", re.I)

# Bid solicitations. Star is a construction contractor, so these are inbound
# REVENUE — the single worst thing to file as junk. They routinely arrive from
# plan-room robots (reproconnect, BuildingConnected, iSqFt) with unsubscribe
# footers, which is why this has to outrank every bulk rule.
BID_INVITATION = re.compile(
    r"(ad(vertisement)? for bids?|invitation to bid|notice to bidders|"
    r"request for (proposal|quote|qualification)|\brf[pqi]\b|\bitb\b|"
    r"bid (date|due|opening|package|invitation|results)|prequalification|"
    r"call for bids|bidding opportunit)", re.I)

# Money records. Informational, but never junk: an owner wants to see what the
# company was charged. Promotion cases (past due, declined) live in
# DEMANDS_ACTION above and are checked first.
FINANCIAL_RECORD = re.compile(
    r"(your receipt|receipt (from|number|#)|invoice (#|number|is ready|attached)|"
    r"payment (received|confirmation)|statement (is )?(ready|available)|"
    r"you were charged|charged to your|remittance)", re.I)

# An obligation left on US, phrased as a statement rather than a question.
# This is the "Updated Drawing" case: "I would expect it around 3 weeks from
# now, assuming the deposit gets sent soon." No question mark, so the
# answered-statement rule demoted it and the model agreed. The deposit is
# still ours to send.
PENDING_ON_US = re.compile(
    r"(assuming (the |your |we |you )?\w+ (gets? |is |are |will be )?"
    r"(sent|paid|received|approved|signed|submitted)|"
    r"once (you|we|the) \w+|pending (your|the) \w+|pending receipt|"
    r"as soon as (you|we) |waiting (on|for) (you|your)|"
    r"(need|needs|needed) (your|a) (signature|approval|deposit|payment|sign-?off))", re.I)

GENERIC_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com",
    "icloud.com", "msn.com", "live.com", "att.net", "cox.net", "sbcglobal.net",
}

VALID_STATES = ("needs_reply", "waiting", "fyi", "resolved", "bulk", "model")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in (email or "") else ""


def days_since(iso: str) -> int:
    try:
        dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00")).replace(tzinfo=None)
        return max(0, (_now() - dt).days)
    except Exception:
        return 0


_QUOTE_HEADER = r"_{5,}|From:\s|On .{5,60} wrote:"

# Sign-off markers that essentially never precede real content. Deliberately
# conservative: "Thanks," and "Regards," are NOT here, because "Thanks, can
# you also send the invoice?" is content, and cutting there would recreate the
# ack bug that hid real work.
_SIGNATURE_START = re.compile(
    r"(?:^|\n)[ \t]*(?:"
    r"-{2,}[ \t]*(?:\n|$)"
    r"|\U0001F4CD?[ \t]*NEW ADDRESS"
    r"|(?:best|kind|warm)(?:est)? regards"
    r"|very respectfully"
    r"|celebrating \d+\s*years? in business"
    r"|sent from my (?:iphone|ipad|android|mobile|samsung)"
    r"|get outlook for (?:ios|android)"
    r")", re.I)

# Below this, a "note" is really just whitespace or a stray character, and the
# forwarded content underneath is what the thread is actually about.
_MIN_MEANINGFUL = 20


def _strip_signature(text: str) -> str:
    m = _SIGNATURE_START.search(text or "")
    return (text[:m.start()] if m else (text or "")).strip()


def fresh(text: str) -> str:
    """The meaningful part of a bodyPreview.

    Normally that is everything before the reply-header junk: signals like
    'asks a question' must read the NEW text, not the quoted history
    underneath, which is full of stale question marks.

    Two refinements from real mail (2026-09-02):

    1. SIGNATURES ARE STRIPPED. Every Star signature opens with a 200-char
       "NEW ADDRESS" banner, and Graph caps bodyPreview at 255 characters, so
       on a forward with no note the signature was the ENTIRE snippet. Rows
       displayed a mailing address and the model was handed one to reason
       about.
    2. A BARE FORWARD FALLS BACK TO THE FORWARDED TEXT. If the new text is
       only a signature, the meaning of the message lives below the divider,
       so read that instead and drop its From/Sent/To/Subject header lines.
       Stale question marks are an acceptable risk here: when someone
       forwards a thread with no comment, a question inside it IS being
       handed to you.
    """
    raw = text or ""
    parts = re.split(_QUOTE_HEADER, raw, maxsplit=1)
    head = _strip_signature(parts[0])
    if len(head) >= _MIN_MEANINGFUL:
        return head
    if len(parts) > 1:
        tail = _strip_signature(parts[1])
        tail = re.sub(r"(?im)^[ \t]*(from|sent|to|cc|subject|date|importance):.*$",
                      " ", tail)
        tail = re.sub(r"\s{2,}", " ", tail).strip()
        if len(tail) >= _MIN_MEANINGFUL:
            return tail
    return head


# --- Signals ------------------------------------------------------------------
#
# One flat, JSON-serializable dict per thread. Every field is computed for
# every thread — no early exits, no conditional gaps — so a rule (or a
# correction replayed weeks later) can read any of them. ~250 bytes on the row,
# well inside the 1.2 KB/row budget the no-bodies rule buys us.

SIGNAL_LABELS: dict[str, str] = {
    "empty": "thread has no messages",
    "self_note": "you emailed yourself",
    "you_spoke_last": "you sent the last message",
    "bulk_sender": "sender address reads as automated",
    "bulk_subject": "notification-style subject",
    "bulk_copy": "contains unsubscribe copy",
    "ticket": "ticket-system auto-update",
    "recorder_bot": "meeting-recording bot",
    "ooo": "out-of-office auto-reply",
    "closure": "explicit closure language",
    "ack": "last word was an acknowledgment",
    "robot_sender": "sender address reads as a shared/support mailbox",
    "cold": "nobody here has replied and it is not a real thread",
    "demands_action": "states a required action or a deadline",
    "bid_invitation": "a bid or proposal solicitation",
    "financial_record": "a receipt, invoice or statement",
    "pending_on_us": "leaves something outstanding on our side",
    "has_conversation": "a real back-and-forth, not a one-off",
    "we_spoke": "you have sent something in this thread",
    "answered": "they replied to you with a statement, not a question",
    "colleague_last": "a colleague spoke last",
    "replied_elsewhere": "you emailed this person in another thread since",
    "to_me": "addressed directly to you",
    "cc_me": "you were only copied",
    "named": "your first name appears in the text",
    "flagged": "you flagged it in Outlook",
    "asks": "contains a question",
    "account_match": "sender domain matches a hit-list account",
    "internal": "sender is a colleague",
}


def collect_signals(
    *,
    trail: list[dict],
    subject: str,
    snippet: str,
    sender_name: str,
    sender_email: str,
    is_flagged: bool,
    msg_count: int,
    last_at: str,
    user_name: str,
    user_email: str,
    account_domains: dict[str, tuple[str, str]],
    outbound_map: dict[str, list[str]] | None = None,
) -> dict:
    """Observe everything, judge nothing. Pure function: same inputs always
    produce the same signals, which is what makes the eval harness possible
    and what lets a rule change be re-run over stored threads for free."""
    me_first = (user_name or "").split()[0].lower() if user_name else ""
    me_email = (user_email or "").lower()
    my_domain = _domain(me_email)
    text = f"{subject or ''} {snippet or ''}"

    inbound = [r for r in trail if r.get("dir") == "in"]
    last = trail[-1] if trail else {}
    latest_inbound = inbound[-1] if inbound else {}
    sender_dom = _domain(latest_inbound.get("from", "") or sender_email or "")

    acct = account_domains.get(sender_dom) if sender_dom not in GENERIC_DOMAINS else None
    internal = bool(sender_dom) and sender_dom == my_domain
    local = (sender_email or "").split("@")[0] if sender_email else ""
    fresh_snip = snippet or ""

    asks = "?" in fresh_snip
    # "Answered": they wrote back with a statement after you had spoken. Often
    # that IS the end of the thread, but not always, so it feeds judgment
    # rather than settling anything on its own.
    answered = (msg_count > 1 and not asks
                and any(r.get("dir") == "out" for r in trail[:-1]))
    # Has anyone here actually engaged, and is this a thread at all? Bulk mail
    # does not hold conversations, so this is the cheapest available guard
    # against filing an 8-message vendor project thread as a newsletter
    # (the RingPlan case: subject contained "Welcome to", so BULK_SUBJECT hit).
    we_spoke = any(r.get("dir") == "out" for r in trail)
    has_conversation = we_spoke or msg_count > 2

    sig = {
        # --- structure -------------------------------------------------------
        "empty": not trail,
        "msg_count": int(msg_count or 0),
        "you_spoke_last": bool(last) and last.get("dir") == "out",
        "self_note": bool(
            inbound and latest_inbound.get("from") == me_email
            and (not subject or "sent from my iphone" in fresh_snip.lower())
        ),
        "days_waiting": days_since(last_at),
        # --- identity --------------------------------------------------------
        "sender_domain": sender_dom,
        "account_match": bool(acct),
        "account_id": acct[0] if acct else None,
        "account_name": acct[1] if acct else "",
        "internal": internal,
        "to_me": bool(last.get("toMe")) if last else False,
        "cc_me": bool(last.get("ccMe")) if last else False,
        "named": bool(me_first) and bool(
            re.search(rf"\b{re.escape(me_first)}\b", fresh_snip.lower())),
        "flagged": bool(is_flagged),
        # A colleague REPLIED — which requires something to have been replied
        # to. The original engine tested only "internal and last inbound is
        # not me", so a colleague OPENING a thread with a direct question
        # ("can you get me the mileage sheet today?") was labelled "colleague
        # replied - verify it covers what was asked of you" and sent to the
        # model instead of straight to the reply lane. The eval harness caught
        # this on its first run; msg_count > 1 is the missing precondition.
        "colleague_last": bool(
            internal and last and last.get("from") != me_email
            and last.get("dir") == "in" and (msg_count or 0) > 1),
        # --- content shape ---------------------------------------------------
        "asks": asks,
        "bulk_sender": bool(local and BULK_SENDERS.match(local + "@")),
        "bulk_subject": bool(BULK_SUBJECT.search(subject or "")),
        "bulk_copy": bool(BULK_COPY.search(fresh_snip)),
        "ticket": bool(inbound and TICKET_SUBJECT.search(subject or "")),
        "recorder_bot": bool(RECORDER_BOT.search(f"{sender_name or ''} {sender_email or ''}")),
        "ooo": bool(OOO.search(text)),
        "closure": bool(CLOSURE.search(text)),
        "ack": bool(msg_count > 1 and ACK.match(fresh_snip) and not asks),
        "robot_sender": bool(ROBOT_SENDER.search(f"{sender_name or ''} {sender_email or ''}")),
        # Cold outreach: nobody here has replied, it is not a real thread, not
        # on the hit list, not a colleague, not flagged. This is where
        # marketing lives — and also where a real new customer lives, which is
        # why it feeds judgment, not a verdict.
        #
        # WAS `msg_count == 1`, which missed every salesperson who bumps their
        # own email. Two unanswered messages from a stranger is MORE cold, not
        # less, but the old test let the bump promote it into the reply lane
        # (the Justin Bullerjahn case on Bob's mailbox).
        "cold": bool(not has_conversation and not acct and not internal
                     and not is_flagged),
        "answered": answered,
        "has_conversation": has_conversation,
        "we_spoke": we_spoke,
        # Content demands, checked against subject AND the fresh snippet.
        "demands_action": bool(DEMANDS_ACTION.search(text)),
        "bid_invitation": bool(BID_INVITATION.search(text)),
        "financial_record": bool(FINANCIAL_RECORD.search(text)),
        "pending_on_us": bool(PENDING_ON_US.search(fresh_snip)),
        "replied_elsewhere": bool(
            outbound_map and sender_email
            and _replied_in_another_thread(sender_email, last_at or "", outbound_map)),
    }
    return sig


def _replied_in_another_thread(sender: str, since: str,
                               outbound_map: dict[str, list[str]]) -> bool:
    """Did the user email this person in ANY other thread after `since`? Reads
    a map precomputed once per sync, so this costs no Graph call and no scan."""
    if not sender:
        return False
    return any(at > since for at in outbound_map.get(sender.lower(), []))


# --- Verdicts and rules -------------------------------------------------------

@dataclass
class Verdict:
    state: str
    rank: int
    category: str
    reason: str
    decided_by: str            # "structural:empty" | "user:<rule id>" | "tier1:ooo" | "model"
    model_hint: str = ""       # passed to the model when state == "model"
    signals_fired: list[str] = field(default_factory=list)


@dataclass
class CompanyRule:
    """A company rule as DATA, not control flow. Priority is explicit and
    inspectable instead of being implied by line number, which is what made
    the old cascade impossible to reason about."""

    id: str
    tier: int                  # -1 structural, 1 default-on, 2 seeded/opt-in
    when: Callable[[dict], bool]
    state: str                 # target state, or "model" to defer to judgment
    rank: int | None = None    # None = leave scoring alone (model path)
    category: str | None = None
    reason: str = ""
    hint: str = ""             # model_hint when state == "model"
    label: str = ""            # human-readable, shown in the Rules tab
    why: str = ""              # shown as the rule's explanation in the UI


# STRUCTURAL: facts about thread shape. Not opinions, not editable, and they
# run before user rules because no rule should claim you didn't send a message
# you actually sent.
STRUCTURAL_RULES: list[CompanyRule] = [
    CompanyRule(
        id="structural:empty", tier=-1,
        when=lambda s: s["empty"],
        state="resolved", rank=0, category="other",
        reason="thread emptied",
        label="Empty threads are closed",
        why="Every message was deleted or moved out, so there is nothing left to act on.",
    ),
    CompanyRule(
        id="structural:self-note", tier=-1,
        when=lambda s: s["self_note"],
        state="resolved", rank=0, category="other",
        reason="note to yourself",
        label="Notes to yourself are not tasks",
        why="Mail you sent to your own address (usually a phone note) is never a reply candidate.",
    ),
    CompanyRule(
        id="structural:you-spoke-last", tier=-1,
        when=lambda s: s["you_spoke_last"],
        state="waiting", rank=20, category=None,
        reason="you replied last — waiting on them",
        label="If you replied last, the ball is theirs",
        why="You sent the most recent message, so the thread is not waiting on you.",
    ),
]

# TIER 1: on for everyone by default. Nobody should have to teach the bot that
# a newsletter is a newsletter — but ORDER inside this list is load-bearing.
#
# The first version put the three bulk rules first, which cost six of the ten
# misclassifications in the 2026-09-02 batch: a state compliance notice, a
# 401(k) amendment needing a wet signature, and an SDUSD bid invitation all
# arrive from no-reply senders and were filed at rank 5. Real obligations now
# get first look; automation detection only claims what is left.
TIER1_RULES: list[CompanyRule] = [
    CompanyRule(
        # Unambiguous and checked before everything: an away message is never
        # a task, whatever else its text happens to contain.
        id="tier1:ooo", tier=1,
        when=lambda s: s["ooo"],
        state="fyi", rank=10, category="notification",
        reason="auto-reply / out of office",
        label="Out-of-office replies are informational",
        why="An automatic reply tells you someone is away. It never needs an answer.",
    ),
    CompanyRule(
        # Beats every bulk rule. Gated on the demand being aimed at YOU:
        # "Action Required" also appears in vendor subject lines on threads a
        # colleague is running, where you are only copied.
        id="tier1:action-required", tier=1,
        when=lambda s: s["demands_action"] and (
            s["to_me"] or s["named"] or s["flagged"]),
        state="needs_reply", rank=None, category=None,
        reason="states a required action or a deadline",
        label="A stated deadline or required action reaches you",
        why="Compliance notices, signature requests and past-due notices come from no-reply addresses. Being unable to reply to the sender does not mean there is nothing to do.",
    ),
    CompanyRule(
        # Also beats every bulk rule, and deliberately NOT gated on to_me:
        # plan rooms mail these to shared and distribution addresses.
        id="tier1:bid-invitation", tier=1,
        when=lambda s: s["bid_invitation"],
        state="needs_reply", rank=None, category="customer",
        reason="bid invitation",
        label="Bid and proposal invitations are business, never junk",
        why="Solicitations from plan rooms (SDUSD, BuildingConnected, iSqFt) arrive from no-reply senders with unsubscribe footers. For a contractor these are inbound revenue.",
    ),
    CompanyRule(
        id="tier1:pending-on-us", tier=1,
        when=lambda s: s["pending_on_us"] and not s["you_spoke_last"],
        state="needs_reply", rank=None, category=None,
        reason="leaves something outstanding on our side",
        label="An obligation left with us still counts, even with no question asked",
        why='Phrases like "assuming the deposit gets sent" or "pending your signature" put the next move on us. Without this they read as statements and get filed as settled.',
    ),
    CompanyRule(
        id="tier1:financial-record", tier=1,
        when=lambda s: s["financial_record"] and not s["demands_action"],
        state="fyi", rank=40, category="vendor",
        reason="receipt / invoice — worth seeing",
        label="Receipts and invoices are worth seeing, not cleanup",
        why="Money records are informational but never junk. Anything past due or declined is promoted by the deadline rule above instead.",
    ),
    CompanyRule(
        # A no-reply address is WEAK evidence and never enough on its own.
        #
        # The address tells you how the mail was SENT, not what it SAYS:
        # Paychex, the State of California and a marketing blast all use the
        # same envelope. Requiring a second, CONTENT signal is the same
        # principle already applied to `to_me` in the reply lane ("toMe alone
        # is not a strong signal"). Without it, six real obligations landed in
        # Cleanup in the first week of real use.
        id="tier1:bulk-sender", tier=1,
        when=lambda s: (s["bulk_sender"] and (s["bulk_copy"] or s["bulk_subject"])
                        and not s["account_match"] and not s["has_conversation"]),
        state="bulk", rank=5, category="notification",
        reason="automated sender + bulk content",
        label="no-reply senders that also read as bulk are cleanup",
        why="A no-reply address ALONE is not enough (compliance notices and bid invites use one). Cleanup needs a second signal: unsubscribe copy or a machine-written subject.",
    ),
    CompanyRule(
        # The fallback for a no-reply sender with nothing else to go on:
        # visible but low, never hidden. This is what catches obligations
        # phrased in words DEMANDS_ACTION does not know yet — a certificate
        # renewal, a portal upload request, a policy change. Being wrong here
        # costs a row near the bottom of "Worth knowing" instead of a message
        # nobody ever sees again.
        id="tier1:automated-notification", tier=1,
        when=lambda s: (s["bulk_sender"] and not s["bulk_copy"]
                        and not s["bulk_subject"] and not s["account_match"]
                        and not s["has_conversation"]),
        state="fyi", rank=20, category="notification",
        reason="automated sender — not a task, but not junk either",
        label="Unclassified automated mail stays visible",
        why="Mail from a no-reply address with no other bulk signal is filed as informational rather than cleanup, so an obligation worded in a way we have not seen yet is still on screen.",
    ),
    CompanyRule(
        id="tier1:bulk-copy", tier=1,
        when=lambda s: s["bulk_copy"] and not s["account_match"]
                       and not s["has_conversation"],
        state="bulk", rank=5, category="notification",
        reason="unsubscribe copy",
        label="Mail with unsubscribe copy is cleanup",
        why="An unsubscribe or 'manage preferences' link means it was sent to a list, not to you.",
    ),
    CompanyRule(
        id="tier1:bulk-subject", tier=1,
        when=lambda s: s["bulk_subject"] and not s["account_match"]
                       and not s["has_conversation"],
        state="bulk", rank=5, category="notification",
        reason="notification-style subject",
        label="Verification codes and signup mail are cleanup",
        why="Subjects like 'verification code' or 'welcome to' are machine-generated one-offs. Skipped once a thread has a real back-and-forth.",
    ),
    CompanyRule(
        # You were copied on someone else's working thread. Common for an
        # owner: Olga runs the vendor install, Salam is on Cc for eight
        # messages. Worth seeing, not a task.
        id="tier1:cc-bystander", tier=1,
        when=lambda s: (s["cc_me"] and s["has_conversation"] and not s["we_spoke"]
                        and not s["to_me"] and not s["named"] and not s["flagged"]),
        state="fyi", rank=25, category=None,
        reason="you were copied — someone else is running this",
        label="Threads you are only copied on are informational",
        why="A multi-message thread where you were Cc, never To, and never spoke is someone else's work. It stays visible but is not treated as yours.",
    ),
    CompanyRule(
        id="tier1:closure", tier=1,
        when=lambda s: s["closure"] and not s["asks"],
        state="resolved", rank=0, category=None,
        reason="closure language — thread looks settled",
        label="Explicitly closed threads are done",
        why="Phrasings like 'has been resolved' or 'case closed' state that the loop is shut.",
    ),
]

# TIER 2: judgment rules. Every one of these came from tuning against a single
# IT-admin mailbox in Aug 2026, and several are role-dependent — demoting cold
# outreach is right for IT and wrong for sales, whose job it is. They are kept
# because several carry real company-wide value, but they start INACTIVE on a
# new inbox and are offered per-user once that user's own corrections
# corroborate them.
TIER2_RULES: list[CompanyRule] = [
    CompanyRule(
        id="tier2:recorder-bot", tier=2,
        when=lambda s: s["recorder_bot"] and not s["account_match"],
        state="bulk", rank=5, category="notification",
        reason="meeting-recording bot",
        label="Meeting-recorder mail (Read.ai, Fathom, Otter) is cleanup",
        why="Recurring setup nags from recording bots. Turn this OFF if you rely on their summaries.",
    ),
    CompanyRule(
        id="tier2:ticket-update", tier=2,
        when=lambda s: s["ticket"],
        state="model", category="notification",
        reason="ticket update",
        hint="ticket update — promote to needs-reply only if a specific question is asked of you",
        label="Ticket-system updates default to informational",
        why="Helpdesk notes are usually status, not a to-do. Turn this OFF if tickets ARE your work.",
    ),
    CompanyRule(
        id="tier2:ack", tier=2,
        when=lambda s: s["ack"],
        state="resolved", rank=0, category=None,
        reason="they acknowledged — nothing left to answer",
        label="'Thanks, got it' closes a thread",
        why="A short acknowledgment as the last word is a social close, not a task.",
    ),
    CompanyRule(
        id="tier2:colleague-covered", tier=2,
        when=lambda s: s["colleague_last"] and not s["to_me"] and not s["named"],
        state="fyi", rank=15, category="internal",
        reason="a colleague replied — you were copied",
        label="A colleague's reply settles threads you were only copied on",
        why="Someone internal answered and you were Cc, not To. Only applies when you were not named.",
    ),
    CompanyRule(
        id="tier2:colleague-verify", tier=2,
        when=lambda s: s["colleague_last"] and (s["to_me"] or s["named"]),
        state="model",
        reason="colleague replied",
        hint="colleague replied — verify it covers what was asked of you",
        label="Check whether a colleague's reply covered your part",
        why="You were addressed or named, so their answer might not have covered you.",
    ),
    CompanyRule(
        id="tier2:replied-elsewhere", tier=2,
        when=lambda s: s["replied_elsewhere"],
        state="fyi", rank=35, category=None,
        reason="you may have replied in another thread — verify",
        label="Demote when you already emailed this person elsewhere",
        why="You sent this person mail in a different thread after their message, so you likely handled it.",
    ),
    CompanyRule(
        id="tier2:cold-outreach", tier=2,
        when=lambda s: s["cold"] and not s["flagged"],
        state="model", rank=40,
        reason="cold outreach",
        hint="cold outreach — worth knowing, promote only if it asks something specific",
        label="Cold first contact is informational, not a task",
        why="Sales pitches and first contacts stay visible but are not to-dos. Turn OFF if prospecting is your job.",
    ),
    CompanyRule(
        id="tier2:robot-sender", tier=2,
        when=lambda s: (s["robot_sender"] and not s["account_match"]
                        and not s["internal"]),
        state="model",
        reason="shared mailbox sender",
        hint="sender is a shared/support mailbox — a name or question in the text is weak evidence",
        label="Shared-mailbox senders get judged, not auto-tasked",
        why="support@ and info@ addresses greet you by name and ask 'did we help?' automatically.",
    ),
    CompanyRule(
        id="tier2:answered-statement", tier=2,
        when=lambda s: s["answered"],
        state="model",
        reason="they replied with a statement",
        hint="they replied to your message — check if anything is still asked of you",
        label="A reply with no question gets judged",
        why="They answered what you asked. Often that ends the thread, but not always.",
    ),
]

ALL_COMPANY_RULES: list[CompanyRule] = STRUCTURAL_RULES + TIER1_RULES + TIER2_RULES
RULES_BY_ID: dict[str, CompanyRule] = {r.id: r for r in ALL_COMPANY_RULES}

# Tier-1 ids are the default-on set for a brand-new inbox.
DEFAULT_ACTIVE_RULE_IDS: set[str] = {r.id for r in TIER1_RULES}
# Ethan's mailbox keeps everything it was tuned with, so the refactor does not
# regress the inbox all of this was validated against. Applied as a one-time
# per-user seed, not as a company default.
LEGACY_TUNED_RULE_IDS: set[str] = {r.id for r in TIER1_RULES + TIER2_RULES}


# --- User rules ---------------------------------------------------------------

USER_SCOPES = ("sender", "domain", "subject", "signal", "sender_subject")
USER_ACTIONS = ("force_state", "promote", "demote")


def _user_rule_matches(rule, signals: dict, *, sender_email: str, subject: str) -> bool:
    """Does one user rule apply to this thread? Scopes are deliberately narrow
    and literal — no fuzzy matching. A rule the user cannot predict is a rule
    they will not trust."""
    pat = (rule.pattern or "").strip().lower()
    if not pat:
        return False
    scope = rule.scope
    if scope == "sender":
        return (sender_email or "").lower() == pat
    if scope == "domain":
        return signals.get("sender_domain", "") == pat.lstrip("@")
    if scope == "subject":
        return pat in (subject or "").lower()
    if scope == "signal":
        return bool(signals.get(pat))
    if scope == "sender_subject":
        # "addr::keyword" — both must hold.
        addr, _, kw = pat.partition("::")
        return ((sender_email or "").lower() == addr.strip()
                and kw.strip() in (subject or "").lower())
    return False


def _apply_user_action(rule, signals: dict) -> tuple[str, int | None]:
    """Resolve a user rule to (state, rank). `promote`/`demote` are relative so
    a user can say "bump this sender" without naming a lane."""
    if rule.action == "force_state":
        target = (rule.target_state or "fyi").lower()
        if target not in ("needs_reply", "fyi", "resolved", "bulk"):
            target = "fyi"
        rank = {"needs_reply": 75, "fyi": 30, "resolved": 0, "bulk": 5}[target]
        return target, rank
    if rule.action == "promote":
        return "needs_reply", 75
    return "fyi", 30


# --- The decision -------------------------------------------------------------

def decide(
    signals: dict,
    *,
    sender_email: str = "",
    subject: str = "",
    user_rules: list | None = None,
    active_rule_ids: set[str] | None = None,
) -> Verdict:
    """Signals plus rules in, one verdict out. First match wins, and the
    matching rule's id lands in `decided_by` so a wrong verdict names the rule
    that produced it.

    `active_rule_ids` is the per-user set of enabled COMPANY rules. Pass None
    for a brand-new inbox and only tier 1 applies — tier 2 stays off until the
    user's own corrections earn it."""
    active = DEFAULT_ACTIVE_RULE_IDS if active_rule_ids is None else active_rule_ids
    fired = [k for k, v in signals.items()
             if v is True and k in SIGNAL_LABELS]

    # 1. Structural facts. Always, and before user rules.
    for rule in STRUCTURAL_RULES:
        if rule.when(signals):
            return Verdict(
                state=rule.state,
                rank=rule.rank if rule.rank is not None else 20,
                category=rule.category or _category_of(signals),
                reason=rule.reason,
                decided_by=rule.id,
                signals_fired=fired,
            )

    # 2. User hard rules. Scoping your own inbox beats any company default.
    for rule in (user_rules or []):
        if not getattr(rule, "active", True):
            continue
        if _user_rule_matches(rule, signals, sender_email=sender_email, subject=subject):
            state, rank = _apply_user_action(rule, signals)
            label = (rule.note or "").strip() or _describe_user_rule(rule)
            return Verdict(
                state=state,
                rank=rank if rank is not None else 30,
                category=_category_of(signals),
                reason=f"your rule: {label}",
                decided_by=f"user:{rule.id}",
                signals_fired=fired,
            )

    # 3. Company rules, tier 1 then tier 2, honoring the user's active set.
    for rule in TIER1_RULES + TIER2_RULES:
        if rule.id not in active:
            continue
        if rule.when(signals):
            return Verdict(
                state=rule.state,
                rank=rule.rank if rule.rank is not None else _score(signals),
                category=rule.category or _category_of(signals),
                reason=rule.reason,
                decided_by=rule.id,
                model_hint=rule.hint,
                signals_fired=fired,
            )

    # 4. Nothing claimed it: score the reply candidates.
    return _score_verdict(signals, fired)


def _category_of(signals: dict) -> str:
    if signals.get("account_match"):
        return "customer"
    if signals.get("internal"):
        return "internal"
    return "other"


def _score(signals: dict) -> int:
    """Deterministic 0-100 within-lane ordering. Same weights as the original
    engine so existing rank behavior is preserved."""
    score = 55
    if signals.get("to_me"):
        score += 10
    if signals.get("flagged"):
        score += 15
    if signals.get("account_match"):
        score += 15
    if signals.get("internal"):
        score += 5
    days = signals.get("days_waiting", 0)
    if days >= 2:
        score += min(days * 2, 14)
    return min(score, 100)


def _score_verdict(signals: dict, fired: list[str]) -> Verdict:
    """The fallback path. Positive human evidence puts a thread in the reply
    lane; anything short of that goes to the model.

    Unlike the original `strong` expression (a five-way disjunction ANDed
    against three negations, with the losing branch throwing its evidence
    away), the evidence and the veto are both recorded, so a wrong verdict
    here says WHICH signal carried it and WHICH one blocked it."""
    reasons: list[str] = []
    if signals.get("to_me"):
        reasons.append("addressed to you")
    if signals.get("flagged"):
        reasons.append("you flagged it")
    if signals.get("account_match"):
        reasons.append(f"hit-list account: {signals.get('account_name')}")
    if signals.get("internal"):
        reasons.append("internal")
    days = signals.get("days_waiting", 0)
    if days >= 2:
        reasons.append(f"waiting {days}d")
    if signals.get("asks"):
        reasons.append("asks a question")

    score = _score(signals)
    category = _category_of(signals)

    # Positive human evidence. Any ONE of these is enough; the point is that
    # "addressed to you" alone is NOT (that is how verification codes topped
    # the lane), so toMe must arrive with a question.
    evidence = [
        ("flagged", signals.get("flagged")),
        ("named", signals.get("named")),
        ("account_match", signals.get("account_match")),
        ("internal", signals.get("internal")),
        ("to_me+asks", signals.get("to_me") and signals.get("asks")),
    ]
    carried = [name for name, ok in evidence if ok]

    if carried:
        return Verdict(
            state="needs_reply", rank=score, category=category,
            reason=", ".join(reasons) or "direct message to you",
            decided_by=f"score:evidence({'+'.join(carried)})",
            signals_fired=fired,
        )
    return Verdict(
        state="model", rank=score, category=category,
        reason="unclear whether this needs you",
        decided_by="score:no-evidence",
        model_hint="no strong signal either way — decide from the metadata",
        signals_fired=fired,
    )


def _describe_user_rule(rule) -> str:
    """Plain-language rendering of a user rule, for the audit line and the
    Rules tab. The user must be able to read back exactly what they told it."""
    scope_txt = {
        "sender": f"mail from {rule.pattern}",
        "domain": f"mail from anyone at {rule.pattern}",
        "subject": f'subjects containing "{rule.pattern}"',
        "signal": f"threads where {SIGNAL_LABELS.get(rule.pattern, rule.pattern)}",
        "sender_subject": f"mail from {str(rule.pattern).split('::')[0]} about "
                          f'"{str(rule.pattern).partition("::")[2]}"',
    }.get(rule.scope, str(rule.pattern))
    if rule.action == "force_state":
        lane = {"needs_reply": "Needs your reply", "fyi": "Worth knowing",
                "resolved": "Handled", "bulk": "Cleanup"}.get(
                    rule.target_state or "fyi", rule.target_state or "fyi")
        return f"{scope_txt} goes to {lane}"
    verb = "is always a reply" if rule.action == "promote" else "is never a task"
    return f"{scope_txt} {verb}"


def describe_user_rule(rule) -> str:
    """Public wrapper — the router and the Rules tab both render rules."""
    return _describe_user_rule(rule)
