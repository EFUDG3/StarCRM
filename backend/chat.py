"""Starbot chat: Claude + tools over the signed-in user's M365 data.

POST /api/chat (wired in main.py) streams Server-Sent Events. The client owns
the conversation: it sends the full Anthropic-format `messages` array and gets
the updated array back in the final `done` event, so the server stays
stateless between turns (no chat persistence in phase 1).

Tool calls run server-side with the user's delegated Graph token (email,
calendar, SharePoint/OneDrive), the todos table, and the CRM's existing op_*
functions — all scoped to the signed-in user.
"""
import json
import os
from datetime import date as date_cls

import anthropic
from sqlalchemy.orm import Session

import accounts
import email_triage
import graph
import mcp_server
import projects
import telemetry
from database import SessionLocal
from models import Todo, User

CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-sonnet-4-6")
MAX_TOOL_ROUNDS = 10
_MAX_TOKENS = 4096
# Cost/rate-limit guardrails. Full email bodies are the token firehose: cap how
# much of each body enters the context and how many full reads one round may do
# (each read lands in the NEXT request's input, which is what trips ITPM).
_READ_EMAIL_MAX_CHARS = 4000
_MAX_FULL_READS_PER_ROUND = 3
# Cap resent history. Old turns compound into every request's input; trim at a
# safe boundary (a plain-text user turn) so tool_use/tool_result pairs stay intact.
_MAX_HISTORY_MESSAGES = 30

_client: anthropic.Anthropic | None = None


def _anthropic() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = anthropic.Anthropic()
    return _client


# --- Todos (shared by the REST routes in main.py and the chat tools) --------
VALID_STATUSES = ("todo", "in_progress", "done")
VALID_PRIORITIES = ("low", "medium", "high")
_STATUS_ORDER = {"todo": 0, "in_progress": 1, "done": 2}
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2, "": 3}


def serialize_todo(t: Todo) -> dict:
    status = t.status or ("done" if t.done else "todo")
    return {
        "id": t.id,
        "text": t.text,
        "due": t.due or "",
        "source": t.source or "",
        "sourceLink": t.source_link or "",
        "status": status,
        "priority": t.priority or "",
        "done": status == "done",
        "created": t.created_at.date().isoformat() if t.created_at else "",
    }


def op_list_todos(db: Session, user: User, include_done: bool = False) -> list[dict]:
    q = db.query(Todo).filter(Todo.user_id == user.id)
    if not include_done:
        q = q.filter(Todo.status != "done")
    rows = q.all()
    rows.sort(key=lambda t: (
        _STATUS_ORDER.get(t.status or "todo", 0),
        _PRIORITY_ORDER.get(t.priority or "", 3),
        t.due or "9999",
        t.created_at or date_cls.min,
    ))
    return [serialize_todo(t) for t in rows]


def op_add_todos(db: Session, user: User, items: list[dict]) -> list[dict]:
    created = []
    for item in items:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        priority = (item.get("priority") or "").strip().lower()
        t = Todo(
            user_id=user.id,
            text=text,
            due=(item.get("due") or "").strip(),
            source=(item.get("source") or "").strip(),
            source_link=(item.get("source_link") or item.get("sourceLink") or "").strip(),
            priority=priority if priority in VALID_PRIORITIES else None,
            status="todo",
        )
        db.add(t)
        created.append(t)
    db.commit()
    return [serialize_todo(t) for t in created]


def op_update_todo(db: Session, user: User, todo_id: str, **fields) -> dict:
    """Partial update of text/due/status/priority; keeps the legacy done flag
    in sync with status."""
    t = db.query(Todo).filter(Todo.id == todo_id, Todo.user_id == user.id).first()
    if t is None:
        raise ValueError("To-do not found")
    if fields.get("text") is not None:
        if not fields["text"].strip():
            raise ValueError("text cannot be empty")
        t.text = fields["text"].strip()
    if fields.get("due") is not None:
        t.due = fields["due"].strip()
    if fields.get("status") is not None:
        status = fields["status"].strip().lower()
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {', '.join(VALID_STATUSES)}")
        t.status = status
        t.done = status == "done"
    if fields.get("priority") is not None:
        priority = fields["priority"].strip().lower()
        if priority and priority not in VALID_PRIORITIES:
            raise ValueError(f"priority must be one of {', '.join(VALID_PRIORITIES)} or empty")
        t.priority = priority or None
    db.commit()
    return serialize_todo(t)


def op_set_todo_done(db: Session, user: User, todo_id: str, done: bool = True) -> dict:
    return op_update_todo(db, user, todo_id, status="done" if done else "todo")


def op_delete_todo(db: Session, user: User, todo_id: str) -> dict:
    t = db.query(Todo).filter(Todo.id == todo_id, Todo.user_id == user.id).first()
    if t is None:
        raise ValueError("To-do not found")
    db.delete(t)
    db.commit()
    return {"deleted": True, "id": todo_id}


# --- Tool schemas (Anthropic format) ----------------------------------------
_STR = {"type": "string"}
_DATE = {"type": "string", "description": "ISO date YYYY-MM-DD"}

TOOLS = [
    {
        "name": "search_emails",
        "description": (
            "Search the user's Outlook mailbox. Use `query` for free-text search "
            "over subject/body/people; use from_address/since/until to narrow. "
            "Returns metadata + previews; call read_email for a full body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {**_STR, "description": "Free-text search terms"},
                "from_address": {**_STR, "description": "Sender email (full or partial)"},
                "since": _DATE, "until": _DATE,
                "top": {"type": "integer", "description": "Max results (default 15, max 25)"},
            },
        },
    },
    {
        "name": "list_recent_emails",
        "description": (
            "Newest inbox messages with previews — the starting point for inbox "
            "triage and for building a to-do list from recent email."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "How many (default 25, max 50)"},
                "unread_only": {"type": "boolean"},
            },
        },
    },
    {
        "name": "read_email",
        "description": "Full plain-text body of one email by id (from a prior search/list result).",
        "input_schema": {
            "type": "object",
            "properties": {"message_id": _STR},
            "required": ["message_id"],
        },
    },
    {
        "name": "list_calendar_events",
        "description": "Calendar events between two dates (inclusive), in Pacific time.",
        "input_schema": {
            "type": "object",
            "properties": {"start": _DATE, "end": _DATE},
            "required": ["start", "end"],
        },
    },
    {
        "name": "search_files",
        "description": "Search SharePoint and OneDrive files the user can access.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": _STR,
                "top": {"type": "integer", "description": "Max results (default 10, max 25)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_todos",
        "description": (
            "The user's current starbot to-do list. ALWAYS call this before adding "
            "items so you never create duplicates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"include_done": {"type": "boolean"}},
        },
    },
    {
        "name": "add_todos",
        "description": (
            "Add items to the user's task board (they land in the To do column). "
            "Set `source` to a short origin label (e.g. 'Email: \"RE: invoice\" — "
            "Bob, Jun 30') and `source_link` to the item's webLink whenever the "
            "task came from an email/event/file. Set `priority` (high/medium/low) "
            "when urgency is clear — deadlines, unhappy customers, and boss "
            "requests are high."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": _STR,
                            "due": _DATE,
                            "source": _STR,
                            "source_link": _STR,
                            "priority": {**_STR, "description": "low | medium | high"},
                        },
                        "required": ["text"],
                    },
                }
            },
            "required": ["items"],
        },
    },
    {
        "name": "complete_todo",
        "description": "Mark one task done (or reopen it) by id.",
        "input_schema": {
            "type": "object",
            "properties": {"todo_id": _STR, "done": {"type": "boolean", "description": "Default true"}},
            "required": ["todo_id"],
        },
    },
    {
        "name": "update_todo",
        "description": (
            "Update one task: move it between board columns (status: todo / "
            "in_progress / done), set priority (low/medium/high, empty clears), "
            "or change text/due date. Only pass fields you're changing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "todo_id": _STR,
                "status": {**_STR, "description": "todo | in_progress | done"},
                "priority": {**_STR, "description": "low | medium | high | '' to clear"},
                "text": _STR,
                "due": _DATE,
            },
            "required": ["todo_id"],
        },
    },
    {
        "name": "delete_todo",
        "description": "Remove a to-do entirely. Only when the user asks to remove/clear items.",
        "input_schema": {
            "type": "object",
            "properties": {"todo_id": _STR},
            "required": ["todo_id"],
        },
    },
    {
        "name": "search_crm_contacts",
        "description": (
            "Search the user's Star CRM relationship board (name/company/role/email/notes). "
            "Use when a person or company comes up to pull their history and follow-ups."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": _STR},
        },
    },
    {
        "name": "get_crm_contact",
        "description": "One CRM contact by id, including the activity log.",
        "input_schema": {
            "type": "object",
            "properties": {"contact_id": _STR},
            "required": ["contact_id"],
        },
    },
    {
        "name": "search_accounts",
        "description": (
            "Search the SHARED company Accounts board — the sales 'hit list' of property "
            "management companies (~120 rows), visible to the whole team. Returns summary "
            "rows ranked by unit count (biggest opportunity first). Use for questions like "
            "'who owns the CONAM relationship', 'my accounts in La Mesa', 'biggest "
            "prospects we haven't contacted'. Filter by rep to scope to a salesperson. "
            "Call get_account for one account's contacts and full activity history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "free text: company, city, contact, notes"},
                "rep": {"type": "string", "description": "assigned salesperson's first name"},
                "status": {"type": "string", "enum": ["prospect", "contacted", "active", "sold", "dead", "cod"]},
                "limit": {"type": "integer", "description": "max rows (default 15, max 40)"},
            },
        },
    },
    {
        "name": "get_account",
        "description": (
            "One shared account in full: every contact (name, role, email, phone) and the "
            "whole dated activity log. Use after search_accounts to answer 'who do we know "
            "there' or 'when did we last touch them'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"account_id": _STR},
            "required": ["account_id"],
        },
    },
    {
        "name": "log_account_note",
        "description": (
            "Append a dated note to a shared account's activity log, stamped with the "
            "signed-in user's name. Use when the user reports sales activity ('log that I "
            "dropped samples at Hoban today'). The whole team sees it, so keep it factual "
            "and confirm the account first if the name is ambiguous."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _STR,
                "note": _STR,
                "date": {"type": "string", "description": "ISO date; defaults to today"},
            },
            "required": ["account_id", "note"],
        },
    },
    {
        "name": "set_account_status",
        "description": (
            "Move a shared account along the sales pipeline. Only when the user clearly "
            "asks ('mark Fairfield as contacted')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": _STR,
                "status": {"type": "string", "enum": ["prospect", "contacted", "active", "sold", "dead", "cod"]},
            },
            "required": ["account_id", "status"],
        },
    },
    {
        "name": "list_projects",
        "description": (
            "The SHARED Projects board — large commercial construction jobs the PM team is "
            "running (schools, restaurants, retail build-outs), visible to the whole team. "
            "Live jobs first by soonest target completion. Returns a per-stage tally plus "
            "summary rows including each job's latest log entry. Use for 'what's in "
            "progress', 'which jobs are behind', 'what is Marc running'. Stages: bidding, "
            "awarded, in_progress, punch_list, complete, lost. NOTE: this is separate from "
            "the Accounts hit list — projects come through GCs and owners, not management "
            "companies."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stage": {"type": "string", "enum": ["bidding", "awarded", "in_progress", "punch_list", "complete", "lost"]},
                "pm": {"type": "string", "description": "project manager's first name"},
                "query": {"type": "string", "description": "free text: job, client, address, material"},
                "limit": {"type": "integer", "description": "max rows (default 25, max 60)"},
            },
        },
    },
    {
        "name": "get_project",
        "description": (
            "One project in full: scope, dates, contract value, square footage, and the "
            "entire dated job log. Use after list_projects for 'where does that job stand'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"project_id": _STR},
            "required": ["project_id"],
        },
    },
    {
        "name": "log_project_note",
        "description": (
            "Append a dated entry to a project's job log, stamped with the signed-in "
            "user's name. Use for site visits, deliveries, delays, inspections ('log that "
            "the Hoover gym subfloor passed moisture testing')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": _STR,
                "note": _STR,
                "date": {"type": "string", "description": "ISO date; defaults to today"},
            },
            "required": ["project_id", "note"],
        },
    },
    {
        "name": "set_project_stage",
        "description": (
            "Move a job to another stage on the shared board. Only when the user clearly "
            "asks ('move Lincoln High to punch list')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": _STR,
                "stage": {"type": "string", "enum": ["bidding", "awarded", "in_progress", "punch_list", "complete", "lost"]},
            },
            "required": ["project_id", "stage"],
        },
    },
    {
        "name": "list_ranked_emails",
        "description": (
            "The Star Mail tab's ranked view of the USER'S OWN inbox — the same triage "
            "verdicts the tab shows. Use this to answer 'what needs my reply', 'what did "
            "I miss', 'what's in my worth-knowing pile'. Returns compact rows (no bodies) "
            "with each thread's ranking reason so you can explain WHY it landed there. "
            "Prefer this over the raw email tools when the user asks about their triage "
            "queue; use read_email if the user wants the actual message body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["needs_reply", "fyi", "cleanup", "dismissed", "all"],
                    "description": "which lane (default needs_reply)",
                },
                "limit": {"type": "integer", "description": "max rows (default 15, max 40)"},
            },
        },
    },
    {
        "name": "get_email_thread",
        "description": (
            "One triaged thread in full: verdict, reason, category, plus a metadata-only "
            "per-message trail (message ids, directions, senders, timestamps). No bodies — "
            "if the user wants the actual content, call read_email with a message id from "
            "the trail."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"thread_id": _STR},
            "required": ["thread_id"],
        },
    },
]

# Anthropic server-side web search: Claude issues the query, Anthropic runs it
# and returns cited results in the same turn — there's no client tool to
# execute (it never reaches _run_tool). Basic `_20250305` variant on purpose:
# the newer dynamic-filtering `_20260209` needs an Opus/Sonnet tier, and prod
# runs Haiku 4.5 (CHAT_MODEL). max_uses caps spend (~$10 / 1000 searches).
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
_API_TOOLS = [*TOOLS, WEB_SEARCH_TOOL]


def _system_prompt(user: User) -> str:
    today = date_cls.today().isoformat()
    weekday = date_cls.today().strftime("%A")
    return f"""You are starbot, the internal AI assistant for Star Flooring & Remodeling \
(San Diego flooring, remodeling, and flood-restoration company). You are talking to \
{user.name} ({user.email or "email unknown"}). Today is {weekday}, {today} (Pacific time).

You can read the user's Outlook email and calendar, search company SharePoint/OneDrive \
files, manage their starbot to-do list, and look up their Star CRM contacts — those are all \
PRIVATE to this signed-in user. You can also see the STAR MAIL ranking of this user's own \
inbox (list_ranked_emails / get_email_thread) — that is the same triage the Email tab shows, \
and it is the best way to answer 'what needs my reply' without dumping the whole mailbox. \
You can also read and update two SHARED team boards that everyone at Star sees: ACCOUNTS, \
the sales hit list of about 120 property-management companies, and PROJECTS, the PM team's \
large commercial construction jobs such as schools and restaurants. You can also search the \
web for current or general information that isn't in Star's own data.

Rules:
- CITE SOURCES. When an answer draws on an email, event, or file, cite it inline as a \
markdown link, e.g. [RE: 4620 walkthrough](webLink) — use each item's webLink. Never \
invent a link or a fact you didn't read from a tool result.
- WEB SEARCH: use it for external facts the user's M365 data can't answer — current events, \
prices, product specs, codes/regulations, vendor lookups, general reference. Cite web \
results inline as markdown links to the source URL, same as any other source. Prefer the \
user's own email/calendar/files/CRM for anything internal; reach for the web only when the \
answer lives outside Star's data. Don't search for things you already know confidently \
unless the user wants current or verified info.
- SHARED BOARDS ARE TEAM-VISIBLE, so treat writes carefully. Anything you log or change \
on Accounts or Projects is seen and trusted by the whole company, and there is no undo in \
chat. Search first, make sure you have the RIGHT record when a name is ambiguous (ask if \
two could match), and only write when the user clearly asked you to. Never invent a \
contact, a date, or a job detail. You CANNOT create or delete accounts or projects from \
chat by design — for those, point the user at the Accounts or Projects tab.
- KEEP THE TWO BOARDS STRAIGHT. Accounts is SALES prospecting: property-management \
companies, an assigned rep, unit and property counts, a pipeline status. Projects is the PM \
team's CONSTRUCTION work: a job name, the GC or owner as client, a stage, a project \
manager, target completion. They are separate boards with no link between them, so a school \
or restaurant job is a PROJECT, not an account. For either board, use search_accounts or \
list_projects to get the overview, then get_account or get_project for one record's full \
history — do not pull every record to answer a narrow question.
- To-do requests ("make a to-do list from my emails"): call list_todos first, then scan \
list_recent_emails (and the calendar when relevant), propose clear action items with due \
dates when the email implies one, and add them with add_todos including source + \
source_link, plus a priority when urgency is clear. Skip anything already on the list; \
say so briefly. Tasks live on the user's Tasks board (columns: To do / In progress / \
Done) — use update_todo to move or re-prioritize them when asked.
- BE FRUGAL WITH FULL EMAIL READS. The preview from list/search results is usually enough \
to triage or extract a to-do. Call read_email only when the decision truly needs the body \
(e.g. drafting a reply, a specific detail the user asked for), at most 2-3 per request, \
prioritized. Never read every email in a list.
- Inbox triage ("organize/triage my inbox"): group into **Action needed**, **Waiting / \
follow-up**, **FYI — no action**, and **Junk / can ignore**, newest first, each item as \
'[subject](webLink) — sender, date: one-line why'. Recommend, don't nag.
- Email drafting: when asked to draft a reply, read the thread first, then write the \
draft in a fenced block ready to copy. Match a professional, direct tone; sign as \
{user.name.split()[0] if user.name else "the user"}. You cannot send email — say the \
draft is ready to copy into Outlook.
- Be concise and skimmable: short paragraphs, bullets for lists, bold for the load-bearing \
bits. No filler, no restating the question.
- If a tool errors with a sign-in problem, tell the user to sign in again via the chat \
tab. If you lack a capability (sending mail, editing files), say so plainly."""


def _run_tool(name: str, args: dict, user: User, db: Session, token: str):
    if name == "search_emails":
        return graph.search_messages(
            token, query=args.get("query", ""), from_address=args.get("from_address", ""),
            since=args.get("since", ""), until=args.get("until", ""),
            top=int(args.get("top") or 15),
        )
    if name == "list_recent_emails":
        return graph.list_recent_messages(
            token, top=int(args.get("count") or 25),
            unread_only=bool(args.get("unread_only")),
        )
    if name == "read_email":
        return graph.get_message(token, args["message_id"], max_chars=_READ_EMAIL_MAX_CHARS)
    if name == "list_calendar_events":
        return graph.list_calendar_events(token, args["start"], args["end"])
    if name == "search_files":
        return graph.search_files(token, args["query"], top=int(args.get("top") or 10))
    if name == "list_todos":
        return op_list_todos(db, user, include_done=bool(args.get("include_done")))
    if name == "add_todos":
        return op_add_todos(db, user, args.get("items") or [])
    if name == "complete_todo":
        return op_set_todo_done(db, user, args["todo_id"], done=args.get("done", True))
    if name == "update_todo":
        return op_update_todo(
            db, user, args["todo_id"],
            status=args.get("status"), priority=args.get("priority"),
            text=args.get("text"), due=args.get("due"),
        )
    if name == "delete_todo":
        return op_delete_todo(db, user, args["todo_id"])
    if name == "search_crm_contacts":
        return mcp_server.op_search(db, user, args.get("query", ""))
    if name == "get_crm_contact":
        return mcp_server.op_get(db, user, args["contact_id"])
    # --- Shared team boards (company-wide, NOT user-scoped) ---
    if name == "search_accounts":
        return accounts.op_account_search(
            db, query=args.get("query", ""), rep=args.get("rep", ""),
            status=args.get("status", ""), limit=int(args.get("limit") or 0),
        )
    if name == "get_account":
        return accounts.op_account_get(db, args["account_id"])
    if name == "log_account_note":
        return accounts.op_account_log(
            db, user, args["account_id"], args["note"], date=args.get("date"),
        )
    if name == "set_account_status":
        return accounts.op_account_set_status(db, user, args["account_id"], args["status"])
    if name == "list_projects":
        return projects.op_project_list(
            db, stage=args.get("stage", ""), pm=args.get("pm", ""),
            query=args.get("query", ""), limit=int(args.get("limit") or 0),
        )
    if name == "get_project":
        return projects.op_project_get(db, args["project_id"])
    if name == "log_project_note":
        return projects.op_project_log(
            db, user, args["project_id"], args["note"], date=args.get("date"),
        )
    if name == "set_project_stage":
        return projects.op_project_set_stage(db, user, args["project_id"], args["stage"])
    # --- Email triage (user-scoped, read from our stored verdicts) ---
    if name == "list_ranked_emails":
        return email_triage.op_list_ranked_emails(
            db, user, state=args.get("state", "needs_reply"),
            limit=int(args.get("limit") or 15),
        )
    if name == "get_email_thread":
        return email_triage.op_get_email_thread(db, user, args["thread_id"])
    raise ValueError(f"Unknown tool: {name}")


# --- The SSE agent loop ------------------------------------------------------
def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _trim_history(messages: list[dict]) -> list[dict]:
    """Drop the oldest turns once history gets long. Cut only at a plain-text
    user turn so an assistant tool_use never loses its matching tool_result."""
    if len(messages) <= _MAX_HISTORY_MESSAGES:
        return messages
    for i in range(len(messages) - _MAX_HISTORY_MESSAGES, len(messages)):
        if messages[i].get("role") == "user" and isinstance(messages[i].get("content"), str):
            return messages[i:]
    return messages  # no safe cut point found — keep everything


def _apply_cache_breakpoints(messages: list[dict]) -> None:
    """Prompt caching: mark the last content block of the final message so the
    whole prefix (tools + system + history) bills as a cache read on the next
    round/turn. Cached tokens also don't count toward the per-minute input
    rate limit, so this is the rate-limit fix as much as the cost fix. Old
    markers are stripped first (max 4 breakpoints per request)."""
    for m in messages:
        if isinstance(m.get("content"), list):
            for block in m["content"]:
                if isinstance(block, dict):
                    block.pop("cache_control", None)
    last = messages[-1]
    if isinstance(last.get("content"), str):
        last["content"] = [{"type": "text", "text": last["content"]}]
    blocks = last.get("content")
    if isinstance(blocks, list) and blocks and isinstance(blocks[-1], dict):
        blocks[-1]["cache_control"] = {"type": "ephemeral"}


def _clean_blocks(content) -> list[dict]:
    """Project SDK content blocks down to the exact fields the API accepts when
    the conversation is replayed next turn.

    Web-search blocks are preserved faithfully (model_dump): the trailing
    server_tool_use is what lets a paused turn resume, and keeping the
    web_search_tool_result alongside the citations that reference it keeps the
    next turn's replay internally consistent."""
    out = []
    for b in content:
        if b.type == "text":
            block = {"type": "text", "text": b.text}
            cites = getattr(b, "citations", None)
            if cites:
                block["citations"] = [c.model_dump(mode="json", exclude_none=True) for c in cites]
            out.append(block)
        elif b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif b.type in ("server_tool_use", "web_search_tool_result"):
            out.append(b.model_dump(mode="json", exclude_none=True))
    return out


_TODO_TOOLS = {"add_todos", "complete_todo", "update_todo", "delete_todo"}


def stream_chat(messages: list[dict], user_id: str, graph_token: str):
    """Generator of SSE lines: text deltas, tool activity, then a final `done`
    event carrying the updated messages array (the client's state for next turn).

    Opens its own DB session: the generator runs while the response streams,
    after FastAPI may have torn down the request-scoped session."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None:
            yield _sse({"type": "error", "message": "Session user no longer exists"})
            return
        client = _anthropic()
        # cache_control on the system block caches tools + system together
        # (tools render first); the per-request breakpoint below extends the
        # cached prefix over the conversation as it grows.
        system = [{
            "type": "text",
            "text": _system_prompt(user),
            "cache_control": {"type": "ephemeral"},
        }]
        messages[:] = _trim_history(messages)
        for _round in range(MAX_TOOL_ROUNDS):
            _apply_cache_breakpoints(messages)
            with client.messages.stream(
                model=CHAT_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system,
                tools=_API_TOOLS,
                messages=messages,
            ) as stream:
                # Iterate raw events (not text_stream) so a server-side web
                # search surfaces a chip the moment it starts, mid-answer.
                for event in stream:
                    if event.type == "content_block_start":
                        cb = event.content_block
                        if cb.type == "server_tool_use" and cb.name == "web_search":
                            yield _sse({"type": "tool", "name": "web_search"})
                            telemetry.log_event(user.id, "chat", "tool:web_search")
                    elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield _sse({"type": "text", "text": event.delta.text})
                final = stream.get_final_message()

            messages.append({"role": "assistant", "content": _clean_blocks(final.content)})
            # A server-side tool (web search) paused mid-turn: re-send as-is and
            # Anthropic resumes it — no client tool_result to add.
            if final.stop_reason == "pause_turn":
                continue
            if final.stop_reason != "tool_use":
                break

            results = []
            full_reads = 0
            for block in final.content:
                if block.type != "tool_use":
                    continue
                # Guardrail: a burst of full-body reads is what blows past the
                # per-minute input limit (every body lands in the next request).
                if block.name == "read_email":
                    full_reads += 1
                    if full_reads > _MAX_FULL_READS_PER_ROUND:
                        results.append({
                            "type": "tool_result", "tool_use_id": block.id,
                            "content": (
                                f"Skipped: max {_MAX_FULL_READS_PER_ROUND} full email reads "
                                "per step. Work from the previews, or read the most "
                                "important remaining email in your next step."
                            ),
                            "is_error": True,
                        })
                        continue
                yield _sse({"type": "tool", "name": block.name})
                telemetry.log_event(user.id, "chat", f"tool:{block.name}")
                try:
                    output = _run_tool(block.name, block.input or {}, user, db, graph_token)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(output, default=str),
                    })
                    if block.name in _TODO_TOOLS:
                        yield _sse({"type": "todos_changed"})
                except graph.GraphError as e:
                    results.append({
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": str(e), "is_error": True,
                    })
                except Exception as e:
                    results.append({
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": f"Tool failed: {e}", "is_error": True,
                    })
            messages.append({"role": "user", "content": results})
        else:
            yield _sse({"type": "text", "text": "\n\n*(Stopped after too many tool steps — ask me to continue.)*"})

        yield _sse({"type": "done", "messages": messages})
    except anthropic.RateLimitError as e:
        retry_after = ""
        try:
            retry_after = e.response.headers.get("retry-after", "")
        except Exception:
            pass
        wait = f"about {retry_after} seconds" if retry_after else "a minute"
        yield _sse({"type": "error", "message": (
            f"Hit the Anthropic rate limit — wait {wait} and try again. "
            "Shorter questions and fewer full-email reads help."
        )})
    except Exception as e:
        yield _sse({"type": "error", "message": str(e)})
    finally:
        db.close()
