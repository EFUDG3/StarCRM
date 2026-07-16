"""Microsoft Graph helpers for the starbot chat tools.

Thin, synchronous wrappers over the Graph REST API using the signed-in user's
delegated token (from m365.get_graph_token). Every function returns plain
dicts/lists shaped for Claude's tool results: compact, with webLink fields so
answers can cite their sources.

All mailbox/calendar reads are the *user's own* data (delegated scopes) — this
module never uses application permissions.
"""
import html
import re
import time
from typing import Any

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
# San Diego company — render calendar/email times in Pacific.
TIMEZONE = "America/Los_Angeles"
_TIMEOUT = 30.0


class GraphError(Exception):
    """A Graph call failed. status 401 means the token is stale (re-sign-in)."""

    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def _request(token: str, method: str, path: str, *, params: dict | None = None,
             json: dict | None = None, headers: dict | None = None,
             _retried: bool = False) -> dict:
    hdrs = {"Authorization": f"Bearer {token}", **(headers or {})}
    resp = httpx.request(
        method, f"{GRAPH}{path}", params=params, json=json, headers=hdrs, timeout=_TIMEOUT
    )
    # Graph (especially /search/query) throws occasional transient 5xxs; one
    # short retry absorbs almost all of them instead of failing the tool call.
    if resp.status_code >= 500 and not _retried:
        time.sleep(1.5)
        return _request(token, method, path, params=params, json=json,
                        headers=headers, _retried=True)
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except Exception:
            detail = resp.text[:300]
        raise GraphError(resp.status_code, f"Graph {resp.status_code}: {detail}")
    return resp.json() if resp.content else {}


def _strip_html(content: str) -> str:
    """Fallback plain-texting for HTML bodies Graph won't convert."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", content, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n\s*", "\n\n", text)).strip()


def _safe_url(url: str) -> str:
    """Graph webUrl/webLink values can contain literal spaces and parentheses
    (SharePoint paths like "Shared Documents/SOP's/Floor Prep SOP.docx").
    Those characters make `[name](url)` invalid markdown, so the chat UI and
    claude.ai render the raw text instead of a link. Encode just the breaking
    characters; already-encoded sequences contain none of them and pass through
    untouched."""
    return (url or "").replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _msg_summary(m: dict) -> dict:
    sender = (m.get("from") or {}).get("emailAddress") or {}
    return {
        "id": m.get("id", ""),
        "subject": m.get("subject", ""),
        "from": f"{sender.get('name', '')} <{sender.get('address', '')}>".strip(),
        "received": m.get("receivedDateTime", ""),
        "preview": (m.get("bodyPreview") or "").strip(),
        "isRead": m.get("isRead", True),
        "hasAttachments": m.get("hasAttachments", False),
        "importance": m.get("importance", "normal"),
        "webLink": _safe_url(m.get("webLink", "")),
        "conversationId": m.get("conversationId", ""),
    }


_MSG_FIELDS = ("id,subject,from,receivedDateTime,bodyPreview,isRead,"
               "hasAttachments,importance,webLink,conversationId")


def search_messages(token: str, query: str = "", from_address: str = "",
                    since: str = "", until: str = "", top: int = 15) -> list[dict]:
    """Search the mailbox. `query` uses Graph $search (subject/body/people);
    otherwise falls back to $filter on sender/date. The two can't combine, so
    when both are given, $search wins and the rest is applied client-side."""
    top = max(1, min(top, 25))
    params: dict[str, Any] = {"$select": _MSG_FIELDS, "$top": top * 2 if query else top}
    if query:
        params["$search"] = f'"{query}"'
    else:
        clauses = []
        if from_address:
            clauses.append(f"from/emailAddress/address eq '{from_address}'")
        if since:
            clauses.append(f"receivedDateTime ge {since}T00:00:00Z")
        if until:
            clauses.append(f"receivedDateTime le {until}T23:59:59Z")
        if clauses:
            params["$filter"] = " and ".join(clauses)
        params["$orderby"] = "receivedDateTime desc"
    data = _request(token, "GET", "/me/messages", params=params)
    rows = [_msg_summary(m) for m in data.get("value", [])]
    if query:  # client-side narrowing that $search can't express
        if from_address:
            fa = from_address.lower()
            rows = [r for r in rows if fa in r["from"].lower()]
        if since:
            rows = [r for r in rows if r["received"][:10] >= since]
        if until:
            rows = [r for r in rows if r["received"][:10] <= until]
    return rows[:top]


def list_recent_messages(token: str, top: int = 25, unread_only: bool = False) -> list[dict]:
    """Newest inbox messages — the raw material for triage and to-do building."""
    top = max(1, min(top, 50))
    params: dict[str, Any] = {
        "$select": _MSG_FIELDS,
        "$top": top,
        "$orderby": "receivedDateTime desc",
    }
    if unread_only:
        params["$filter"] = "isRead eq false"
    data = _request(token, "GET", "/me/mailFolders/inbox/messages", params=params)
    return [_msg_summary(m) for m in data.get("value", [])]


def get_message(token: str, message_id: str, max_chars: int = 8000) -> dict:
    """One message with its full body as plain text."""
    data = _request(
        token, "GET", f"/me/messages/{message_id}",
        params={"$select": _MSG_FIELDS + ",body,toRecipients,ccRecipients"},
        headers={"Prefer": 'outlook.body-content-type="text"'},
    )
    body = (data.get("body") or {}).get("content", "")
    if (data.get("body") or {}).get("contentType") == "html":
        body = _strip_html(body)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…[truncated]"
    out = _msg_summary(data)
    out["to"] = [
        f"{r['emailAddress'].get('name', '')} <{r['emailAddress'].get('address', '')}>"
        for r in data.get("toRecipients", [])
    ]
    out["body"] = body
    return out


def list_calendar_events(token: str, start: str, end: str, top: int = 50) -> list[dict]:
    """Events between two ISO dates (inclusive), expanded from recurrences,
    in local (Pacific) time."""
    data = _request(
        token, "GET", "/me/calendarView",
        params={
            "startDateTime": f"{start}T00:00:00",
            "endDateTime": f"{end}T23:59:59",
            "$select": "id,subject,start,end,location,organizer,isAllDay,webLink,bodyPreview,attendees",
            "$orderby": "start/dateTime",
            "$top": max(1, min(top, 100)),
        },
        headers={"Prefer": f'outlook.timezone="{TIMEZONE}"'},
    )
    out = []
    for e in data.get("value", []):
        organizer = (e.get("organizer") or {}).get("emailAddress") or {}
        out.append({
            "id": e.get("id", ""),
            "subject": e.get("subject", ""),
            "start": (e.get("start") or {}).get("dateTime", "")[:16],
            "end": (e.get("end") or {}).get("dateTime", "")[:16],
            "isAllDay": e.get("isAllDay", False),
            "location": (e.get("location") or {}).get("displayName", ""),
            "organizer": f"{organizer.get('name', '')} <{organizer.get('address', '')}>".strip(),
            "attendees": [
                (a.get("emailAddress") or {}).get("name", "")
                for a in (e.get("attendees") or [])[:10]
            ],
            "preview": (e.get("bodyPreview") or "").strip()[:200],
            "webLink": _safe_url(e.get("webLink", "")),
        })
    return out


def list_calendar_events_for_scan(token: str, start: str, end: str) -> list[dict]:
    """Calendar events with the fields address extraction needs: the dedicated
    location field (often filled via Outlook's place picker) PLUS the full body
    as text, since drivers also write addresses into descriptions."""
    data = _request(
        token, "GET", "/me/calendarView",
        params={
            "startDateTime": f"{start}T00:00:00",
            "endDateTime": f"{end}T23:59:59",
            "$select": "subject,start,location,bodyPreview,body",
            "$orderby": "start/dateTime",
            "$top": 100,
        },
        headers={"Prefer": f'outlook.timezone="{TIMEZONE}", outlook.body-content-type="text"'},
    )
    out = []
    for e in data.get("value", []):
        body = (e.get("body") or {}).get("content", "") or e.get("bodyPreview", "") or ""
        if (e.get("body") or {}).get("contentType") == "html":
            body = _strip_html(body)
        out.append({
            "subject": e.get("subject", ""),
            "date": (e.get("start") or {}).get("dateTime", "")[:10],
            "location": (e.get("location") or {}).get("displayName", ""),
            "body": body.strip()[:1200],
        })
    return out


def search_files(token: str, query: str, top: int = 10) -> list[dict]:
    """Search SharePoint + OneDrive (driveItem) the user can access."""
    if not (query or "").strip():
        # Graph 400s on an empty queryString; give the model a fixable message.
        raise GraphError(400, "search_files needs a non-empty query — pass the "
                              "keywords to look for, e.g. 'flood restoration SOP'")
    data = _request(
        token, "POST", "/search/query",
        json={
            "requests": [{
                "entityTypes": ["driveItem"],
                "query": {"queryString": query},
                "from": 0,
                "size": max(1, min(top, 25)),
            }]
        },
    )
    out = []
    for container in (data.get("value") or []):
        for hc in container.get("hitsContainers") or []:
            for hit in hc.get("hits") or []:
                res = hit.get("resource") or {}
                out.append({
                    "name": res.get("name", ""),
                    "summary": _strip_html(hit.get("summary", "") or "")[:300],
                    "webUrl": _safe_url(res.get("webUrl", "")),
                    "lastModified": res.get("lastModifiedDateTime", ""),
                    "modifiedBy": ((res.get("lastModifiedBy") or {}).get("user") or {}).get(
                        "displayName", ""
                    ),
                    "sizeBytes": res.get("size"),
                })
    return out
