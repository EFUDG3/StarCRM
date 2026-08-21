"""Pydantic request models.

Fields use the camelCase names the frontend already sends (nextAction, nextDue)
so the React component does not have to change its shape. Responses are built
by the serialize() helper in main.py rather than Pydantic, so the JSON the
frontend receives is identical to Bob's original data structure (including the
`log` array).
"""
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ContactIn(BaseModel):
    name: str
    company: str = ""
    role: str = ""
    email: str = ""
    phone: str = ""            # legacy single number; still accepted
    phones: list = []          # list of {type, number} — cell/work/home/other
    category: str = "bd"
    categoryLabel: str = ""  # free-text label used when category == "other"
    nextAction: str = ""
    nextDue: str = ""
    notes: str = ""
    # (cardImage removed 2026-07 — scans prefill fields but photos aren't stored.
    # Older clients may still send the key; pydantic ignores unknown fields.)


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
    priority: str = ""     # low | medium | high | "" (none)


class TodoPatch(BaseModel):
    """Partial task update — send only the fields you're changing."""
    text: Optional[str] = None
    due: Optional[str] = None
    status: Optional[str] = None    # todo | in_progress | done
    priority: Optional[str] = None  # low | medium | high | "" clears it
    done: Optional[bool] = None     # legacy alias for status todo/done


class RouteIn(BaseModel):
    """Ordered addresses for one trip: start, stops…, end."""
    addresses: list


class TripIn(BaseModel):
    """A calculated route being saved to the log. `legs` and `resolved` are
    echoed from the /route response (resolved carries geocoded coords so
    places save without re-geocoding). `rate` is the $/mile chosen in the
    entry form — stored on the trip."""
    date: str
    legs: list
    totalMiles: float
    rate: float = 0.725
    resolved: list = []


class PlaceIn(BaseModel):
    address: str
    label: str = ""


class ScanIn(BaseModel):
    """Calendar scan window (ISO dates, inclusive)."""
    start: str
    end: str


class ReportGenIn(BaseModel):
    """Mileage report download: the window plus which logged days to include
    (empty dates = every logged day in the window)."""
    start: str
    end: str
    dates: list = []


_STATUSES = {"prospect", "contacted", "active", "sold", "dead", "cod"}


class AccountContactIn(BaseModel):
    """One customer person on an account. Field caps are enforced here so a
    malformed or oversized payload is rejected (422) before it reaches the DB."""
    name: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=254)   # RFC 5321 max
    phone: str = Field(default="", max_length=40)
    address: str = Field(default="", max_length=300)


class AccountIn(BaseModel):
    """A shared 'hit list' account. `addresses`/`emails` are string lists (an
    account can hold several); `contacts` is the full customer list (the form
    owns it, so the server replaces the account's contacts on each save).

    Length + count caps live here: oversized text is rejected with a 422 before
    it can bloat the shared table, and `status` is coerced to a known value."""
    name: str = Field(max_length=200)
    rep: str = Field(default="", max_length=120)
    status: str = "prospect"
    website: str = Field(default="", max_length=300)
    phone: str = Field(default="", max_length=40)
    addresses: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    numProperties: Optional[int] = Field(default=None, ge=0)
    totalUnits: Optional[int] = Field(default=None, ge=0)
    notes: str = Field(default="", max_length=5000)
    contacts: list[AccountContactIn] = Field(default_factory=list, max_length=100)

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        v = (v or "prospect").strip().lower()
        return v if v in _STATUSES else "prospect"

    @field_validator("addresses", "emails")
    @classmethod
    def _cap_items(cls, v: list) -> list:
        # Trim each entry to 300 chars and cap the list at 25 (forgiving —
        # truncates rather than 422s, since these come from a repeatable field).
        return [str(x).strip()[:300] for x in (v or [])][:25]


class AccountLogIn(BaseModel):
    """One dated activity-log note on an account. `date` defaults to today on
    the server if omitted."""
    note: str = Field(max_length=2000)
    date: Optional[str] = None


class DismissIn(BaseModel):
    """Dismiss a flagged email from the Tasks 'Replies needed' lane by its
    Graph message id."""
    id: str = Field(max_length=512)


_STAGES = {"bidding", "awarded", "in_progress", "punch_list", "complete", "lost"}
_PROJECT_TYPES = {"school", "restaurant", "retail", "multifamily", "office", "other"}


class ProjectIn(BaseModel):
    """A shared commercial project (PM board). Same cap philosophy as
    AccountIn: length limits are enforced here so one oversized paste can't
    bloat the shared table, and stage/type are coerced to known values so the
    board never grows a phantom column."""
    name: str = Field(max_length=200)
    client: str = Field(default="", max_length=200)
    siteAddress: str = Field(default="", max_length=300)
    projectType: str = "other"
    stage: str = "in_progress"
    pm: str = Field(default="", max_length=120)
    contractValue: Optional[float] = Field(default=None, ge=0)
    startDate: str = Field(default="", max_length=20)   # ISO date, may be empty
    targetDate: str = Field(default="", max_length=20)
    material: str = Field(default="", max_length=120)
    sqFt: Optional[int] = Field(default=None, ge=0)
    description: str = Field(default="", max_length=5000)

    @field_validator("stage")
    @classmethod
    def _valid_stage(cls, v: str) -> str:
        v = (v or "in_progress").strip().lower()
        return v if v in _STAGES else "in_progress"

    @field_validator("projectType")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        v = (v or "other").strip().lower()
        return v if v in _PROJECT_TYPES else "other"


class ProjectLogIn(BaseModel):
    """One dated activity-log note on a project. `date` defaults to today on
    the server if omitted."""
    note: str = Field(max_length=2000)
    date: Optional[str] = None


class EmailPrefIn(BaseModel):
    """Email tab preferences. Both fields optional so the switch can PUT just
    the one it changed."""
    digestEnabled: Optional[bool] = None
    digestHour: Optional[int] = Field(default=None, ge=5, le=12)
