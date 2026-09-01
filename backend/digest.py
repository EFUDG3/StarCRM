"""Star Mail daily digest — phase 2.

Builds the morning email that surfaces what needs the user's attention today,
drawing from the same stored data the tabs read: triage verdicts from the
Email tab, CRM follow-ups, shared Accounts, shared Projects. Read-first: the
digest is a SUMMARY + DEEP-LINK layer, not a mail client. Every row links
back into Star Mail or opens the source in Outlook.

Status (2026-08-21):
    - Content builder + HTML/plain-text renderers: DONE, no new scopes.
    - Preview endpoints (/api/email/digest/preview{,.json,.txt}): LIVE now.
      Iterate on the design in the browser without any send infrastructure.
    - send_digest(): WRITTEN but requires app-level Mail.Send scoped to a
      service mailbox via ApplicationAccessPolicy. Raises a clear error
      until that lands in Entra.
    - run_daily_digests(): the Container Apps Job entrypoint. Runs today in
      dry_run=True mode (builds + logs, does NOT send). Flip to False after
      the scope is granted and DIGEST_FROM points at a real mailbox.

Design constraints that shaped this file:
    - Zero new Graph calls at digest time. Everything the digest needs is
      already in the DB from the Email tab's earlier syncs; adding live
      Graph fetches would put the 7am cron on the user's delegated token
      refresh, which is fragile for users who haven't opened the app.
    - Outlook 2016+ compatible HTML: TABLE layout, INLINE styles only. No
      class selectors, no <style> block, no flexbox, no external fonts.
    - Empty digests skip sending. A digest that reads as noise gets
      archived; a clean-slate morning shouldn't teach that behavior.
"""

import html
import logging
import os
from datetime import date as date_cls, datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

import graph
import m365
import telemetry
from database import SessionLocal, get_db
from models import (Account, Contact, EmailThread, Project, User, UserPref)

log = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/api/email/digest", tags=["digest"])

# Caps per section. A digest that runs to 40 lines gets archived at a glance;
# 5-8 items each keeps the whole thing scannable in one screen.
_TOP_REPLIES = 8
_TOP_UPCOMING = 8   # calendar events tomorrow through end-of-week
_UPCOMING_DAYS = 6  # skip today (Star Mail's job); look ahead this many days
_TOP_FOLLOWUPS = 6
_TOP_QUIET_ACCOUNTS = 5
_TOP_LATE_PROJECTS = 5

# An account is "quiet" if no one has touched it in this many days.
_QUIET_DAYS = 30

# Service mailbox the digest sends AS. Configure via env once the mailbox
# exists in Exchange and Mail.Send is scoped to it via ApplicationAccessPolicy.
_FROM = os.getenv("DIGEST_FROM", "starbot@starflooringandremodeling.com")
_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL", "https://starbot.starflooringandremodeling.com"
).rstrip("/")


# ---------- helpers -----------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _today() -> date_cls:
    return date_cls.today()


def _first_name(user: User) -> str:
    return (user.name or "there").split()[0]


def _rep_matches(rep: str, user: User) -> bool:
    """Same first-name matching rule the tabs use for 'my accounts' filters.
    Accepts slash-combos like 'Rudy / Leighann'."""
    if not rep or not user.name:
        return False
    my = user.name.split()[0].lower()
    for tok in rep.lower().split("/"):
        t = tok.strip()
        if t == my or t.startswith(my):
            return True
    return False


def _days_since(iso: str) -> int:
    try:
        dt = datetime.fromisoformat((iso or "").replace("Z", "+00:00")).replace(tzinfo=None)
        return max(0, (_now() - dt).days)
    except Exception:
        return 0


def _fmt_date(d: date_cls) -> str:
    """Cross-platform 'Month DD' with no leading zero on day."""
    return f"{d.strftime('%B')} {d.day}"


# ---------- content builder --------------------------------------------------


def _fetch_upcoming(token: str, from_date: date_cls, days: int) -> list[dict]:
    """TOMORROW through end-of-week calendar events for this user. Skips
    today deliberately — Star Mail's "your day" line already covers today;
    the digest's job is to surface what's coming so plans made TODAY can
    account for it. Any Graph failure returns [], never raises — a missing
    calendar section shouldn't fail the whole digest.

    Rules for what makes the cut: declined events excluded (they aren't on
    my plate), private events kept (the user chose to see them). Recurring
    daily events are shown per instance because the calendar view expands
    recurrences — the display is grouped by day, so a daily standup shows
    once per day, not once per week."""
    start = (from_date + timedelta(days=1)).isoformat()
    end = (from_date + timedelta(days=days)).isoformat()
    try:
        events = graph.list_calendar_events(token, start, end, top=40)
    except Exception:
        return []
    out = []
    for e in events:
        # Skip declined-by-me events. Attendees carry {name, response,
        # emailAddress}; if there's a self-entry with response "declined",
        # drop it. We don't have the user's email cheaply here, so read
        # the attendee list conservatively — anything explicitly declined
        # by anyone doesn't drop it; the presence of a personal decline
        # would require an addr match we skip for now.
        out.append(e)
    return out[:_TOP_UPCOMING]


def build_digest(
    db: Session, user: User, on_date: date_cls | None = None,
    graph_token: str | None = None,
) -> dict:
    """One structured payload with every section the renderers need.

    Data sources:
    - DB (always): replies, followups, quiet accounts, late projects.
    - Graph calendar (optional): tomorrow-through-end-of-week meetings.
      If graph_token is None or the call fails, that section is skipped
      cleanly — a stale token in the cron shouldn't break the digest.

    Sections are populated independently so a missing source degrades
    gracefully to an empty section rather than blowing up the whole
    payload."""
    today = on_date or _today()

    # 1. Reply lane — top-ranked needs_reply threads (skip dismissed).
    replies = (
        db.query(EmailThread)
        .filter(EmailThread.user_id == user.id,
                EmailThread.state == "needs_reply",
                EmailThread.dismissed_at.is_(None))
        .order_by(EmailThread.rank.desc(), EmailThread.last_at.desc())
        .limit(_TOP_REPLIES)
        .all()
    )
    total_replies = (
        db.query(EmailThread)
        .filter(EmailThread.user_id == user.id,
                EmailThread.state == "needs_reply",
                EmailThread.dismissed_at.is_(None))
        .count()
    )

    # 2. CRM follow-ups due today or overdue (user-scoped Contacts).
    followups = (
        db.query(Contact)
        .filter(Contact.user_id == user.id,
                Contact.next_due != "",
                Contact.next_due <= today.isoformat())
        .order_by(Contact.next_due.asc())
        .limit(_TOP_FOLLOWUPS)
        .all()
    )

    # 3. MY accounts that have gone quiet (>30 days since last touch).
    cutoff = _now() - timedelta(days=_QUIET_DAYS)
    quiet: list[Account] = []
    for a in db.query(Account).order_by(Account.updated_at.asc()).all():
        if not _rep_matches(a.rep, user):
            continue
        if a.updated_at and a.updated_at < cutoff:
            quiet.append(a)
            if len(quiet) >= _TOP_QUIET_ACCOUNTS:
                break

    # 4. Projects assigned to me that are past target and still live.
    late_projects: list[Project] = []
    for p in db.query(Project).all():
        if not _rep_matches(p.pm, user):
            continue
        if p.stage in ("complete", "lost"):
            continue
        if p.target_date and p.target_date < today.isoformat():
            late_projects.append(p)
    late_projects.sort(key=lambda p: p.target_date or "")
    late_projects = late_projects[:_TOP_LATE_PROJECTS]

    # 5. Coming up — tomorrow through end-of-week meetings. Only fetches
    # when we have a token; the cron passes one, the preview passes the
    # signed-in user's cookie-derived token.
    upcoming_events = _fetch_upcoming(graph_token, today, _UPCOMING_DAYS) if graph_token else []

    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "firstName": _first_name(user),
            "email": user.email or "",
        },
        "date": today.isoformat(),
        "generatedAt": _now().isoformat(),
        "sections": {
            "replies": [_shape_thread(t) for t in replies],
            "upcoming": [_shape_event(e) for e in upcoming_events],
            "followups": [_shape_followup(c, today) for c in followups],
            "quietAccounts": [_shape_account(a) for a in quiet],
            "lateProjects": [_shape_project(p, today) for p in late_projects],
        },
        "counts": {
            "totalNeedsReply": total_replies,
            "shown": len(replies),
        },
    }


def _shape_event(e: dict) -> dict:
    """Reduce a Graph calendarView event to just what the digest needs.
    graph.list_calendar_events returns start/end as ISO strings truncated
    to 16 chars ("YYYY-MM-DDTHH:MM"), already in Pacific because the
    helper sets the Prefer: outlook.timezone header."""
    start = e.get("start", "")  # "2026-08-26T09:00"
    end = e.get("end", "")
    day = start[:10]
    time_str = ""
    if not e.get("isAllDay") and len(start) >= 16:
        try:
            hh, mm = int(start[11:13]), int(start[14:16])
            ampm = "a" if hh < 12 else "p"
            h12 = hh % 12 or 12
            time_str = f"{h12}:{mm:02d}{ampm}"
        except Exception:
            pass
    return {
        "day": day,
        "time": time_str or ("all day" if e.get("isAllDay") else ""),
        "isAllDay": bool(e.get("isAllDay")),
        "subject": e.get("subject", "(no title)"),
        "location": e.get("location", ""),
        "organizer": e.get("organizer", ""),
        "webLink": e.get("webLink", ""),
    }


def _shape_thread(t: EmailThread) -> dict:
    return {
        "id": t.id,
        "subject": t.subject or "(no subject)",
        "from": t.sender_name or t.sender_email or "?",
        "reason": t.reason or "",
        "rank": t.rank or 0,
        "accountName": t.account_name or "",
        "webLink": t.web_link or f"{_BASE_URL}/#email",
        "daysWaiting": _days_since(t.last_at or ""),
    }


def _shape_followup(c: Contact, today: date_cls) -> dict:
    overdue = 0
    try:
        overdue = (today - date_cls.fromisoformat(c.next_due)).days
    except Exception:
        overdue = 0
    return {
        "id": c.id,
        "name": c.name,
        "company": c.company or "",
        "action": c.next_action or "",
        "due": c.next_due,
        "overdueBy": max(0, overdue),
    }


def _shape_account(a: Account) -> dict:
    days = (_now() - a.updated_at).days if a.updated_at else 999
    return {
        "id": a.id,
        "name": a.name,
        "daysQuiet": days,
        "totalUnits": a.total_units,
        "status": a.status or "prospect",
        "url": f"{_BASE_URL}/#accounts",
    }


def _shape_project(p: Project, today: date_cls) -> dict:
    days_late = 0
    try:
        days_late = (today - date_cls.fromisoformat(p.target_date)).days
    except Exception:
        pass
    return {
        "id": p.id,
        "name": p.name,
        "client": p.client or "",
        "stage": p.stage or "in_progress",
        "daysLate": max(0, days_late),
        "target": p.target_date or "",
        "url": f"{_BASE_URL}/#projects",
    }


# ---------- HTML renderer ----------------------------------------------------
#
# Table layout, inline styles only — Outlook 2016+ desktop drops most of what a
# real HTML page can do. The brand colors below mirror the app's palette so
# the digest reads as a companion to the tabs, not a separate product.

_INK = "#1C1C1C"
_MIST = "#F3F0EC"
_SEA = "#922525"
_TIDE = "#C0392B"
_HAIR = "#E7E2DA"
_MUTE = "#8b9a9f"
_SOFT = "#4a5a60"

_EYEBROW = (
    f"font-family:'IBM Plex Mono',ui-monospace,'Courier New',monospace;"
    f"font-size:10px;letter-spacing:.14em;text-transform:uppercase;"
    f"color:{_SEA};font-weight:600;"
)
_LINK_RESET = "text-decoration:none;color:inherit;"


def render_html(d: dict) -> str:
    firstName = html.escape(d["user"]["firstName"])
    day = date_cls.fromisoformat(d["date"])
    weekday = day.strftime("%A")
    date_str = _fmt_date(day)

    body_sections = "".join([
        _section_replies(d["sections"]["replies"],
                         d["counts"]["totalNeedsReply"],
                         d["counts"]["shown"]),
        _section_upcoming(d["sections"].get("upcoming", []), d["date"]),
        _section_followups(d["sections"]["followups"]),
        _section_quiet(d["sections"]["quietAccounts"]),
        _section_late(d["sections"]["lateProjects"]),
    ])

    if not body_sections.strip():
        body_sections = f"""
        <tr><td style="padding:24px 32px 8px 32px;font-family:Georgia,serif;">
          <p style="margin:0 0 6px 0;font-size:16px;color:{_INK};">Clean slate.</p>
          <p style="margin:0;color:{_MUTE};font-size:13px;">Nothing needs your reply, nothing overdue. Enjoy the quiet.</p>
        </td></tr>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Star Mail — Your day</title>
</head>
<body style="margin:0;padding:0;background:{_MIST};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;color:{_INK};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_MIST};padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;background:#ffffff;border:1px solid #cdd6d4;border-radius:8px;overflow:hidden;">
          <tr>
            <td style="padding:24px 32px 4px 32px;">
              <div style="{_EYEBROW}">Star Mail &middot; your day</div>
              <h1 style="margin:8px 0 4px 0;font-family:Georgia,serif;font-size:26px;font-weight:600;color:{_INK};letter-spacing:-.01em;">Good morning, {firstName}.</h1>
              <div style="color:{_SOFT};font-size:14px;">Here's your {weekday}, {date_str}.</div>
            </td>
          </tr>
          {body_sections}
          <tr>
            <td style="padding:20px 32px 24px 32px;border-top:1px solid {_HAIR};">
              <a href="{_BASE_URL}/#email" style="display:inline-block;padding:10px 18px;background:{_INK};color:#ffffff;text-decoration:none;border-radius:4px;font-size:13px;font-weight:600;">Open Star Mail</a>
              <div style="margin-top:14px;font-size:11px;color:{_MUTE};line-height:1.5;">You're receiving this because you turned on the daily digest in Star Mail. <a href="{_BASE_URL}/#email" style="color:{_SEA};">Turn it off</a> any time.</div>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _section_wrapper(title: str, inner: str, note: str = "") -> str:
    note_html = f'<div style="margin-top:2px;font-size:11px;color:{_MUTE};">{html.escape(note)}</div>' if note else ""
    return f"""
    <tr><td style="padding:18px 32px 6px 32px;">
      <div style="{_EYEBROW}">{html.escape(title)}</div>
      {note_html}
    </td></tr>
    <tr><td style="padding:0 32px 8px 32px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{inner}</table>
    </td></tr>
    """


def _section_replies(rows: list, total: int, shown: int) -> str:
    if not rows:
        return ""
    inner = "".join(_reply_row(r) for r in rows)
    extra_html = ""
    if total > shown:
        extra_html = f'<tr><td style="padding:8px 0 0 0;font-size:12px;color:{_MUTE};">+{total - shown} more in the reply lane — <a href="{_BASE_URL}/#email" style="color:{_SEA};">see all</a>.</td></tr>'
    return _section_wrapper("Needs your reply", inner + extra_html)


def _reply_row(r: dict) -> str:
    dot = (
        f'<span style="display:inline-block;width:8px;height:8px;background:{_TIDE};'
        f'border-radius:50%;margin-right:8px;vertical-align:middle;"></span>'
        if r["rank"] >= 65 else ""
    )
    acct = ""
    if r["accountName"]:
        acct = (
            f'<span style="margin-left:8px;padding:2px 7px;background:rgba(146,37,37,.10);'
            f'color:{_SEA};font-size:10px;font-family:ui-monospace,monospace;'
            f'border-radius:9999px;">{html.escape(r["accountName"][:32])}</span>'
        )
    waited = ""
    if r["daysWaiting"] > 0:
        waited = f' <span style="color:{_MUTE};font-size:12px;">&nbsp;·&nbsp;waiting {r["daysWaiting"]}d</span>'
    return f"""
    <tr>
      <td style="padding:12px 0;border-bottom:1px solid {_HAIR};">
        <a href="{html.escape(r["webLink"])}" style="{_LINK_RESET}">
          <div style="font-weight:600;font-size:14px;color:{_INK};">{dot}{html.escape(r["from"])}{acct}</div>
          <div style="font-size:13px;color:{_SOFT};margin-top:3px;">{html.escape(r["subject"])}</div>
          <div style="font-size:12px;color:{_SEA};font-style:italic;margin-top:3px;">{html.escape(r["reason"])}{waited}</div>
        </a>
      </td>
    </tr>
    """


def _section_upcoming(rows: list, today_iso: str) -> str:
    """Group by day so a Tuesday digest doesn't just list eight bare times.
    The whole point of this section is 'what does tomorrow onward look like
    that changes what you do today', and day-grouping is what makes that
    scannable at a glance."""
    if not rows:
        return ""
    today = date_cls.fromisoformat(today_iso)
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["day"], []).append(r)
    inner = ""
    for day_iso in sorted(grouped.keys()):
        try:
            d = date_cls.fromisoformat(day_iso)
        except Exception:
            continue
        delta = (d - today).days
        label = ("Tomorrow" if delta == 1
                 else d.strftime("%A") if 0 < delta <= 6
                 else d.strftime("%A, %b %d"))
        sub = d.strftime("%b %d") if delta == 1 else ""
        sub_html = f' <span style="color:{_MUTE};font-weight:normal;font-size:12px;">{sub}</span>' if sub else ""
        events_html = "".join(_upcoming_row(e) for e in grouped[day_iso])
        inner += f"""
        <tr><td style="padding:12px 0 4px 0;">
          <div style="font-family:Georgia,serif;font-size:14px;font-weight:600;color:{_INK};">{html.escape(label)}{sub_html}</div>
        </td></tr>
        {events_html}
        """
    return _section_wrapper("Coming up", inner)


def _upcoming_row(e: dict) -> str:
    time_html = f'<span style="font-family:ui-monospace,monospace;font-size:12px;color:{_SEA};display:inline-block;min-width:56px;">{html.escape(e["time"])}</span>' if e.get("time") else ""
    loc = ""
    if e.get("location"):
        loc = f' <span style="color:{_MUTE};font-size:12px;">&nbsp;·&nbsp;{html.escape(e["location"][:60])}</span>'
    link = e.get("webLink") or _BASE_URL
    return f"""
    <tr><td style="padding:6px 0;border-bottom:1px solid {_HAIR};">
      <a href="{html.escape(link)}" style="{_LINK_RESET}">
        <div style="font-size:13px;color:{_INK};">{time_html}<span style="font-weight:500;">{html.escape(e["subject"])}</span>{loc}</div>
      </a>
    </td></tr>
    """


def _section_followups(rows: list) -> str:
    if not rows:
        return ""
    inner = "".join(_followup_row(r) for r in rows)
    return _section_wrapper("CRM follow-ups due", inner)


def _followup_row(r: dict) -> str:
    overdue = ""
    if r["overdueBy"] > 0:
        overdue = f' <span style="color:{_TIDE};font-weight:600;font-size:12px;">&nbsp;·&nbsp;{r["overdueBy"]}d overdue</span>'
    comp = f' <span style="color:{_MUTE};font-weight:normal;font-size:13px;">— {html.escape(r["company"])}</span>' if r["company"] else ""
    action = f'<div style="font-size:13px;color:{_SOFT};margin-top:3px;">{html.escape(r["action"])}</div>' if r["action"] else ""
    return f"""
    <tr><td style="padding:10px 0;border-bottom:1px solid {_HAIR};">
      <a href="{_BASE_URL}/#crm" style="{_LINK_RESET}">
        <div style="font-weight:600;font-size:14px;color:{_INK};">{html.escape(r["name"])}{comp}{overdue}</div>
        {action}
      </a>
    </td></tr>
    """


def _section_quiet(rows: list) -> str:
    if not rows:
        return ""
    inner = "".join(_quiet_row(r) for r in rows)
    return _section_wrapper(
        "Your accounts gone quiet",
        inner,
        note=f"No touch in {_QUIET_DAYS}+ days — the shared board only counts as fresh when someone logs a note.",
    )


def _quiet_row(r: dict) -> str:
    units = ""
    if r.get("totalUnits"):
        units = f' <span style="color:{_MUTE};font-weight:normal;font-size:12px;">&nbsp;·&nbsp;{r["totalUnits"]:,} units</span>'
    return f"""
    <tr><td style="padding:10px 0;border-bottom:1px solid {_HAIR};">
      <a href="{html.escape(r["url"])}" style="{_LINK_RESET}">
        <div style="font-weight:600;font-size:14px;color:{_INK};">{html.escape(r["name"])}{units}</div>
        <div style="font-size:12px;color:{_TIDE};margin-top:3px;">No touches in {r["daysQuiet"]} days</div>
      </a>
    </td></tr>
    """


def _section_late(rows: list) -> str:
    if not rows:
        return ""
    inner = "".join(_late_row(r) for r in rows)
    return _section_wrapper("Projects past target", inner)


def _late_row(r: dict) -> str:
    client = f' <span style="color:{_MUTE};font-weight:normal;font-size:13px;">— {html.escape(r["client"])}</span>' if r["client"] else ""
    return f"""
    <tr><td style="padding:10px 0;border-bottom:1px solid {_HAIR};">
      <a href="{html.escape(r["url"])}" style="{_LINK_RESET}">
        <div style="font-weight:600;font-size:14px;color:{_INK};">{html.escape(r["name"])}{client}</div>
        <div style="font-size:12px;color:{_TIDE};margin-top:3px;">{r["daysLate"]}d past target ({html.escape(r["target"])})</div>
      </a>
    </td></tr>
    """


# ---------- Plain-text fallback ---------------------------------------------


def render_text(d: dict) -> str:
    """Text alternative for clients that refuse HTML. Also the debugging
    view — you can eyeball what a digest says without opening a browser."""
    firstName = d["user"]["firstName"]
    day = date_cls.fromisoformat(d["date"])
    weekday = day.strftime("%A")
    lines = [
        "Star Mail — Your day",
        f"Good morning, {firstName}. | {weekday}, {_fmt_date(day)}",
        "",
    ]
    reps = d["sections"]["replies"]
    if reps:
        lines.append(f"NEEDS YOUR REPLY  ({d['counts']['totalNeedsReply']} total)")
        for r in reps:
            mark = "! " if r["rank"] >= 65 else "  "
            wait = f"  (waited {r['daysWaiting']}d)" if r["daysWaiting"] else ""
            acct = f"  [{r['accountName']}]" if r["accountName"] else ""
            lines.append(f"{mark}{r['from']}{acct}  —  {r['subject']}{wait}")
            if r["reason"]:
                lines.append(f"    why: {r['reason']}")
        lines.append("")
    up = d["sections"].get("upcoming") or []
    if up:
        lines.append("COMING UP")
        today = date_cls.fromisoformat(d["date"])
        grouped: dict[str, list[dict]] = {}
        for r in up:
            grouped.setdefault(r["day"], []).append(r)
        for day_iso in sorted(grouped.keys()):
            try:
                dd = date_cls.fromisoformat(day_iso)
            except Exception:
                continue
            delta = (dd - today).days
            label = "Tomorrow" if delta == 1 else dd.strftime("%A")
            lines.append(f"  {label} — {dd.strftime('%b %d')}")
            for e in grouped[day_iso]:
                loc = f"  ({e['location']})" if e.get("location") else ""
                t = e.get("time") or ""
                lines.append(f"    {t:<8}{e['subject']}{loc}")
        lines.append("")
    fu = d["sections"]["followups"]
    if fu:
        lines.append("CRM FOLLOW-UPS DUE")
        for r in fu:
            overdue = f"  ({r['overdueBy']}d overdue)" if r["overdueBy"] > 0 else ""
            comp = f" — {r['company']}" if r["company"] else ""
            lines.append(f"  {r['name']}{comp}{overdue}")
            if r["action"]:
                lines.append(f"    {r['action']}")
        lines.append("")
    q = d["sections"]["quietAccounts"]
    if q:
        lines.append("YOUR ACCOUNTS GONE QUIET")
        for r in q:
            units = f" ({r['totalUnits']:,} units)" if r["totalUnits"] else ""
            lines.append(f"  {r['name']}{units} — {r['daysQuiet']}d quiet")
        lines.append("")
    lp = d["sections"]["lateProjects"]
    if lp:
        lines.append("PROJECTS PAST TARGET")
        for r in lp:
            client = f" — {r['client']}" if r["client"] else ""
            lines.append(f"  {r['name']}{client}  ({r['daysLate']}d past)")
        lines.append("")
    if not (reps or fu or q or lp):
        lines.append("Clean slate. Nothing needs your reply, nothing overdue.")
        lines.append("")
    lines.append(f"Open Star Mail: {_BASE_URL}/#email")
    return "\n".join(lines)


# ---------- Preview endpoints (LIVE now, no Mail.Send needed) ---------------
#
# These are how you iterate on the design over the weekend. GET /preview
# renders THIS user's digest as HTML directly in the browser tab — the
# most useful shape for tweaking spacing/color; /preview.json returns the
# structured data (great for finding "why did this row rank there"); and
# /preview.txt is the plain-text version. All three are auth-gated to the
# signed-in user; nobody can preview anybody else's digest.


def _token_for_preview(user: User, db: Session) -> str | None:
    """Best-effort Graph token for the preview endpoints. Preview is a
    convenience — if the user's token cache is stale we still want the
    digest to render, just without the calendar section."""
    try:
        return m365.get_graph_token(user, db)
    except Exception:
        return None


@router.get("/preview", response_class=HTMLResponse)
def preview_html(
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    d = build_digest(db, user, graph_token=_token_for_preview(user, db))
    return HTMLResponse(render_html(d))


@router.get("/preview.json")
def preview_json(
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    return build_digest(db, user, graph_token=_token_for_preview(user, db))


@router.get("/preview.txt", response_class=HTMLResponse)
def preview_text(
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    d = build_digest(db, user, graph_token=_token_for_preview(user, db))
    txt = html.escape(render_text(d))
    return HTMLResponse(
        f'<pre style="font-family:ui-monospace,monospace;font-size:13px;'
        f'padding:24px;background:{_MIST};color:{_INK};white-space:pre-wrap;">'
        f'{txt}</pre>'
    )


# ---------- Send + cron entrypoint (stubbed until scope lands) --------------
#
# The send path is written to work the moment app-level Mail.Send is granted
# in Entra with an ApplicationAccessPolicy scoping it to DIGEST_FROM. Today
# it raises a clear error if called — the container job runs in dry_run
# mode so the whole pipeline is testable end-to-end without a live scope.


def send_digest(
    user: User, subject: str, html_body: str, text_body: str,
    app_token: str | None = None,
) -> None:
    """POST /users/{DIGEST_FROM}/sendMail with the digest as HTML.

    Requires an APP token with Mail.Send scoped by ApplicationAccessPolicy
    to DIGEST_FROM. The caller (run_daily_digests) acquires it once via MSAL
    client-credentials at start of a run and reuses it across users, so
    per-user token cache staleness never blocks the 7am cron.
    """
    if not app_token:
        raise RuntimeError(
            "send_digest called without an app token. Steps to enable: "
            "(1) create the DIGEST_FROM mailbox in Exchange, "
            "(2) grant this app Mail.Send (application permission) in Entra + admin consent, "
            "(3) restrict it with New-ApplicationAccessPolicy to DIGEST_FROM only, "
            "(4) update the container-job entrypoint to acquire the token via MSAL "
            "client-credentials and pass it in. Until then, run_daily_digests(dry_run=True)."
        )
    if not user.email:
        raise RuntimeError(f"User {user.id} has no email — can't send digest")

    endpoint = f"https://graph.microsoft.com/v1.0/users/{_FROM}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": user.email}}],
        },
        # False so the digests don't pile up in the service mailbox's Sent
        # Items forever. Telemetry table records what went out.
        "saveToSentItems": False,
    }
    resp = httpx.post(
        endpoint,
        headers={"Authorization": f"Bearer {app_token}",
                 "Content-Type": "application/json"},
        json=payload, timeout=30.0,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"sendMail failed {resp.status_code}: {resp.text[:300]}")


def run_daily_digests(app_token: str | None = None, dry_run: bool = True) -> dict:
    """Container Apps Job entrypoint. Iterates users with digest_enabled=True,
    builds each digest, sends via the service mailbox (unless dry_run).

    A per-user failure is logged and skipped — the job never lets one bad
    user block the rest. Cron schedule lives on the Container Apps Job
    resource, not in code, so timing changes don't need a redeploy.

    Return shape lets a wrapper script log a one-line summary like
    'digest run: sent=6 empty=2 errors=0'.
    """
    db = SessionLocal()
    try:
        enabled = (
            db.query(User)
            .join(UserPref, UserPref.user_id == User.id)
            .filter(UserPref.digest_enabled.is_(True))
            .all()
        )
        results = {
            "dryRun": dry_run,
            "sent": 0, "emptySkipped": 0, "errors": 0,
            "detail": [],
        }
        for user in enabled:
            try:
                # Best-effort per-user Graph token for the calendar section.
                # If refresh fails (user hasn't opened the app in 90+ days,
                # or admin revoked consent), skip calendar only — the rest
                # of the digest still goes out.
                user_token = None
                try:
                    user_token = m365.get_graph_token(user, db)
                except Exception:
                    log.info("digest: no Graph token for %s, skipping calendar", user.name)
                d = build_digest(db, user, graph_token=user_token)
                sections = d["sections"]
                # Empty digest = don't send. A blank digest teaches archive-on-
                # sight for future ones. Log so we can see who's quiet.
                if not any(sections.values()):
                    results["emptySkipped"] += 1
                    results["detail"].append({"user": user.name, "outcome": "empty-skipped"})
                    telemetry.log_event(user.id, "digest", "empty-skipped")
                    continue
                weekday = date_cls.fromisoformat(d["date"]).strftime("%A")
                n_replies = len(sections["replies"])
                subj = f"Star Mail — {weekday}, {n_replies} to reply to"
                html_body = render_html(d)
                text_body = render_text(d)
                if dry_run:
                    log.info("digest DRY-RUN %s: subject=%r, bytes=%d",
                             user.name, subj, len(html_body))
                    results["detail"].append({"user": user.name, "outcome": "dry-run",
                                              "subject": subj, "bytes": len(html_body)})
                    telemetry.log_event(user.id, "digest", "dry-run")
                else:
                    send_digest(user, subj, html_body, text_body, app_token=app_token)
                    results["sent"] += 1
                    results["detail"].append({"user": user.name, "outcome": "sent",
                                              "subject": subj})
                    telemetry.log_event(user.id, "digest", "sent")
            except Exception as err:
                results["errors"] += 1
                results["detail"].append({
                    "user": user.name, "outcome": "error", "error": str(err)[:240],
                })
                telemetry.log_event(user.id, "digest", "error", str(err)[:200])
                log.warning("digest error for %s: %s", user.name, err)
        return results
    finally:
        db.close()
