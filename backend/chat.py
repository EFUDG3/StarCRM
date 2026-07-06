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

import graph
import mcp_server
from database import SessionLocal
from models import Todo, User

CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-sonnet-4-6")
MAX_TOOL_ROUNDS = 10
_MAX_TOKENS = 4096

_client: anthropic.Anthropic | None = None


def _anthropic() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = anthropic.Anthropic()
    return _client


# --- Todos (shared by the REST routes in main.py and the chat tools) --------
def serialize_todo(t: Todo) -> dict:
    return {
        "id": t.id,
        "text": t.text,
        "due": t.due or "",
        "source": t.source or "",
        "sourceLink": t.source_link or "",
        "done": bool(t.done),
        "created": t.created_at.date().isoformat() if t.created_at else "",
    }


def op_list_todos(db: Session, user: User, include_done: bool = False) -> list[dict]:
    q = db.query(Todo).filter(Todo.user_id == user.id)
    if not include_done:
        q = q.filter(Todo.done.is_(False))
    rows = q.all()
    rows.sort(key=lambda t: (bool(t.done), t.due or "9999", t.created_at or date_cls.min))
    return [serialize_todo(t) for t in rows]


def op_add_todos(db: Session, user: User, items: list[dict]) -> list[dict]:
    created = []
    for item in items:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        t = Todo(
            user_id=user.id,
            text=text,
            due=(item.get("due") or "").strip(),
            source=(item.get("source") or "").strip(),
            source_link=(item.get("source_link") or item.get("sourceLink") or "").strip(),
        )
        db.add(t)
        created.append(t)
    db.commit()
    return [serialize_todo(t) for t in created]


def op_set_todo_done(db: Session, user: User, todo_id: str, done: bool = True) -> dict:
    t = db.query(Todo).filter(Todo.id == todo_id, Todo.user_id == user.id).first()
    if t is None:
        raise ValueError("To-do not found")
    t.done = done
    db.commit()
    return serialize_todo(t)


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
            "Add items to the user's to-do list. Set `source` to a short origin label "
            "(e.g. 'Email: \"RE: invoice\" — Bob, Jun 30') and `source_link` to the "
            "item's webLink whenever the to-do came from an email/event/file."
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
        "description": "Mark one to-do done (or not done) by id.",
        "input_schema": {
            "type": "object",
            "properties": {"todo_id": _STR, "done": {"type": "boolean", "description": "Default true"}},
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
]


def _system_prompt(user: User) -> str:
    today = date_cls.today().isoformat()
    weekday = date_cls.today().strftime("%A")
    return f"""You are starbot, the internal AI assistant for Star Flooring & Remodeling \
(San Diego flooring, remodeling, and flood-restoration company). You are talking to \
{user.name} ({user.email or "email unknown"}). Today is {weekday}, {today} (Pacific time).

You can read the user's Outlook email and calendar, search company SharePoint/OneDrive \
files, manage their starbot to-do list, and look up their Star CRM contacts — via tools, \
always scoped to this signed-in user.

Rules:
- CITE SOURCES. When an answer draws on an email, event, or file, cite it inline as a \
markdown link, e.g. [RE: 4620 walkthrough](webLink) — use each item's webLink. Never \
invent a link or a fact you didn't read from a tool result.
- To-do requests ("make a to-do list from my emails"): call list_todos first, then scan \
list_recent_emails (and the calendar when relevant), propose clear action items with due \
dates when the email implies one, and add them with add_todos including source + \
source_link. Skip anything already on the list; say so briefly.
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
        return graph.get_message(token, args["message_id"])
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
    if name == "delete_todo":
        return op_delete_todo(db, user, args["todo_id"])
    if name == "search_crm_contacts":
        return mcp_server.op_search(db, user, args.get("query", ""))
    if name == "get_crm_contact":
        return mcp_server.op_get(db, user, args["contact_id"])
    raise ValueError(f"Unknown tool: {name}")


# --- The SSE agent loop ------------------------------------------------------
def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _clean_blocks(content) -> list[dict]:
    """Project SDK content blocks down to the exact fields the API accepts when
    the conversation is replayed next turn."""
    out = []
    for b in content:
        if b.type == "text":
            out.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out


_TODO_TOOLS = {"add_todos", "complete_todo", "delete_todo"}


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
        system = _system_prompt(user)
        for _round in range(MAX_TOOL_ROUNDS):
            with client.messages.stream(
                model=CHAT_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system,
                tools=TOOLS,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    yield _sse({"type": "text", "text": text})
                final = stream.get_final_message()

            messages.append({"role": "assistant", "content": _clean_blocks(final.content)})
            if final.stop_reason != "tool_use":
                break

            results = []
            for block in final.content:
                if block.type != "tool_use":
                    continue
                yield _sse({"type": "tool", "name": block.name})
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
    except Exception as e:
        yield _sse({"type": "error", "message": str(e)})
    finally:
        db.close()
