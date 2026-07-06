"""Pydantic request models.

Fields use the camelCase names the frontend already sends (nextAction, nextDue)
so the React component does not have to change its shape. Responses are built
by the serialize() helper in main.py rather than Pydantic, so the JSON the
frontend receives is identical to Bob's original data structure (including the
`log` array).
"""
from typing import Optional

from pydantic import BaseModel


class ContactIn(BaseModel):
    name: str
    company: str = ""
    role: str = ""
    email: str = ""
    phone: str = ""
    category: str = "bd"
    categoryLabel: str = ""  # free-text label used when category == "other"
    nextAction: str = ""
    nextDue: str = ""
    notes: str = ""
    # Optional data URL ("data:image/jpeg;base64,...") of a scanned card to store.
    cardImage: Optional[str] = None


class LogIn(BaseModel):
    note: str
    date: Optional[str] = None  # defaults to today on the server if omitted


class UserIn(BaseModel):
    name: str


class ChatIn(BaseModel):
    """One starbot chat turn. `messages` is the full Anthropic-format history
    (the client stores what the previous turn's `done` event returned, plus the
    new user message) — the server holds no chat state between turns."""
    messages: list


class TodoIn(BaseModel):
    text: str
    due: str = ""          # ISO date string, may be empty
    source: str = ""       # origin label, e.g. 'Email: "RE: invoice" — Bob'
    source_link: str = ""  # webLink to the originating email/event/file


class TodoPatch(BaseModel):
    done: bool
