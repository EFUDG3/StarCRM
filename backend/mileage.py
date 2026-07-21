"""Mileage tracker: Azure Maps routing + a self-building address book.

Port of the standalone mileage-tracker-v2 into starbot, with the pieces that
couldn't exist as a static page:

- Routing runs server-side against Azure Maps (subscription key in the secret
  store, same pattern as every other credential — the v1 hardcoded-key mistake
  stays dead). One multi-stop trip = ONE route transaction.
- Places persist per user in Neon with cached coordinates, so repeat visits to
  the same job site never re-geocode (long-project company: that's most visits).
- "Scan my calendar" pulls the signed-in user's own Outlook events (delegated
  token, same as chat) and has Claude Haiku pull out California street
  addresses — no STOP:-format discipline required from drivers. Every address
  is day-stamped: a place_visits row per address+date, so a scanned week
  shows up as day groups and a whole day loads into the trip form in one
  click (the first stage of the calendar → autofill → mileage-report flow).
- Trips (a day's route) persist per user; the log survives refreshes and
  reports dollars at the configurable MILEAGE_RATE (IRS standard).
"""
import calendar
import json
import os
import re
from datetime import date, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

import graph
import m365
import mileage_report
import telemetry
from database import get_db
from models import Place, PlaceVisit, Trip, User
from schemas import PlaceIn, ReportGenIn, RouteIn, ScanIn, TripIn

AZURE_MAPS_KEY = os.getenv("AZURE_MAPS_KEY", "")
ATLAS = "https://atlas.microsoft.com"
# Company reimbursement rate, $/mile — ground truth is HR's mileage sheet
# ($0.7250, matches the Mileage Tracker 2026 template). Override with the
# MILEAGE_RATE env var when HR updates it — no redeploy, just the env value.
MILEAGE_RATE = float(os.getenv("MILEAGE_RATE", "0.725"))
OFFICE = "4610 Alvarado Canyon Rd, San Diego, CA 92120"
# Street part only, lowercased — calendar events list the office with and
# without the ZIP, so prefix-matching catches every variant.
_OFFICE_PREFIX = OFFICE.split(",")[0].lower()
_EXTRACT_MODEL = os.getenv("SCAN_MODEL", "claude-haiku-4-5")
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

router = APIRouter(prefix="/api/mileage", tags=["mileage"])


def _normalize_window(start: str, end: str) -> tuple[date, date]:
    """Shared guard for every date-ranged endpoint. Swapped dates are an
    obvious mistake (start 7/7, end 7/1 used to come back as a raw 500) —
    just fix them. The one-month cap keeps a fat-fingered year-wide scan
    from burning Haiku tokens on hundreds of events."""
    try:
        s = datetime.strptime(start, "%Y-%m-%d").date()
        e = datetime.strptime(end, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Dates must be YYYY-MM-DD")
    if s > e:
        s, e = e, s
    if (e - s).days > 31:
        raise HTTPException(
            status_code=400,
            detail="Range is limited to one month at a time — narrow it",
        )
    return s, e


# --- Azure Maps --------------------------------------------------------------
def _maps_get(path: str, params: dict) -> dict:
    if not AZURE_MAPS_KEY:
        raise HTTPException(status_code=503, detail="Azure Maps key is not configured")
    resp = httpx.get(
        f"{ATLAS}{path}",
        params={**params, "api-version": "1.0", "subscription-key": AZURE_MAPS_KEY},
        timeout=25,
    )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Azure Maps {resp.status_code}: {resp.text[:200]}",
        )
    return resp.json()


def _geocode(address: str) -> tuple[float, float]:
    """Address → (lat, lon). 400s with the offending address so the user can
    fix a typo instead of staring at a generic error."""
    data = _maps_get("/search/address/json", {
        "query": address, "countrySet": "US", "limit": 1,
    })
    results = data.get("results") or []
    if not results:
        raise HTTPException(status_code=400, detail=f"Could not find address: {address}")
    pos = results[0]["position"]
    return pos["lat"], pos["lon"]


def _coords_for(db: Session, user: User, address: str) -> tuple[float, float]:
    """Cached coordinates from the user's places when available (repeat job
    sites), otherwise a fresh geocode."""
    place = (
        db.query(Place)
        .filter(Place.user_id == user.id, Place.address.ilike(address.strip()))
        .first()
    )
    if place is not None and place.lat is not None and place.lon is not None:
        return place.lat, place.lon
    return _geocode(address.strip())


def _route_legs(coords: list[tuple[float, float]], addresses: list[str]) -> list[dict]:
    query = ":".join(f"{lat},{lon}" for lat, lon in coords)
    data = _maps_get("/route/directions/json", {
        "query": query, "travelMode": "car", "routeType": "fastest",
    })
    routes = data.get("routes") or []
    if not routes:
        raise HTTPException(status_code=502, detail="Azure Maps returned no route")
    legs = routes[0].get("legs") or []
    out = []
    for i, leg in enumerate(legs):
        s = leg.get("summary") or {}
        out.append({
            "from": addresses[i],
            "to": addresses[i + 1],
            "miles": round(s.get("lengthInMeters", 0) * 0.000621371, 1),
            "minutes": round(s.get("travelTimeInSeconds", 0) / 60),
        })
    return out


# --- Serializers -------------------------------------------------------------
def _ser_place(p: Place) -> dict:
    return {
        "id": p.id,
        "label": p.label or "",
        "address": p.address,
        "source": p.source or "manual",
        "timesUsed": p.times_used or 0,
        "lastUsed": p.last_used or "",
    }


def _ser_visit(v: PlaceVisit, p: Place) -> dict:
    return {
        "id": v.id,
        "date": v.date,
        "placeId": p.id,
        "address": p.address,
        "label": v.label or p.label or "",
    }


def _ser_trip(t: Trip) -> dict:
    legs = json.loads(t.legs_json)
    rate = t.rate if t.rate is not None else MILEAGE_RATE
    return {
        "id": t.id,
        "date": t.date,
        "legs": legs,
        "totalMiles": t.total_miles,
        "rate": round(rate, 4),
        "dollars": round(t.total_miles * rate, 2),
    }


def _upsert_place(db: Session, user: User, address: str, date: str,
                  lat: float | None = None, lon: float | None = None,
                  label: str = "", source: str = "trip") -> Place:
    place = (
        db.query(Place)
        .filter(Place.user_id == user.id, Place.address.ilike(address.strip()))
        .first()
    )
    if place is None:
        place = Place(
            user_id=user.id, address=address.strip(), label=label,
            lat=lat, lon=lon, source=source, times_used=0,
        )
        db.add(place)
    if lat is not None and place.lat is None:
        place.lat, place.lon = lat, lon
    if label and not place.label:
        place.label = label
    if source == "trip":
        place.times_used = (place.times_used or 0) + 1
        if date and date > (place.last_used or ""):
            place.last_used = date
    return place


# --- Routing + trips ---------------------------------------------------------
@router.post("/route")
def calc_route(
    payload: RouteIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    addresses = [a.strip() for a in payload.addresses if a and a.strip()]
    if len(addresses) < 2:
        raise HTTPException(status_code=400, detail="Need a start and at least one stop")
    if len(addresses) > 26:
        raise HTTPException(status_code=400, detail="Too many stops for one trip (max 25)")
    coords = [_coords_for(db, user, a) for a in addresses]
    legs = _route_legs(coords, addresses)
    total = round(sum(l["miles"] for l in legs), 1)
    telemetry.log_event(user.id, "mileage", "route_calculated", f"{len(addresses)} stops")
    return {
        "legs": legs,
        "totalMiles": total,
        "rate": MILEAGE_RATE,
        "dollars": round(total * MILEAGE_RATE, 2),
        # Client echoes these back on save so places get coords without a
        # second geocode round.
        "resolved": [
            {"address": a, "lat": c[0], "lon": c[1]} for a, c in zip(addresses, coords)
        ],
    }


@router.get("/trips")
def list_trips(
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    rows = (
        db.query(Trip)
        .filter(Trip.user_id == user.id)
        .order_by(Trip.date.desc(), Trip.created_at.desc())
        .limit(500)
        .all()
    )
    return {"rate": MILEAGE_RATE, "trips": [_ser_trip(t) for t in rows]}


@router.post("/trips", status_code=201)
def save_trip(
    payload: TripIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    if not payload.legs:
        raise HTTPException(status_code=400, detail="No legs to save")
    if not (0 < payload.rate <= 5):
        raise HTTPException(status_code=400, detail="Rate must be between $0 and $5 per mile")
    trip = Trip(
        user_id=user.id,
        date=payload.date or "",
        legs_json=json.dumps(payload.legs),
        total_miles=round(float(payload.totalMiles), 1),
        # 4 decimals, not 2 — the company rate is $0.7250 and a 2-decimal
        # round would silently store it as $0.72.
        rate=round(float(payload.rate), 4),
    )
    db.add(trip)
    # Every visited stop becomes/updates a saved place — the office itself is
    # skipped (it would top the list while never being useful to click).
    for r in payload.resolved:
        addr = (r.get("address") or "").strip()
        if not addr or addr.lower() == OFFICE.lower():
            continue
        _upsert_place(db, user, addr, payload.date, r.get("lat"), r.get("lon"))
    db.commit()
    db.refresh(trip)
    telemetry.log_event(user.id, "mileage", "trip_saved", f"{trip.total_miles} mi")
    return _ser_trip(trip)


@router.delete("/trips/{trip_id}", status_code=204)
def delete_trip(
    trip_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> None:
    t = db.query(Trip).filter(Trip.id == trip_id, Trip.user_id == user.id).first()
    if t is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    db.delete(t)
    db.commit()


# --- Places ------------------------------------------------------------------
@router.get("/places")
def list_places(
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> list:
    rows = db.query(Place).filter(Place.user_id == user.id).all()
    # Stack, not queue: newest-added first, so places from a fresh calendar
    # scan (and new manual adds) land at the top of the rail instead of
    # burying themselves under the long-standing address book.
    rows.sort(key=lambda p: p.created_at or datetime.min, reverse=True)
    return [_ser_place(p) for p in rows]


@router.post("/places", status_code=201)
def add_place(
    payload: PlaceIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    address = payload.address.strip()
    if not address:
        raise HTTPException(status_code=400, detail="address is required")
    lat, lon = _geocode(address)  # validates the address is real
    place = _upsert_place(db, user, address, date="", lat=lat, lon=lon,
                          label=payload.label.strip(), source="manual")
    db.commit()
    db.refresh(place)
    return _ser_place(place)


@router.delete("/places/{place_id}", status_code=204)
def delete_place(
    place_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> None:
    p = db.query(Place).filter(Place.id == place_id, Place.user_id == user.id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Place not found")
    db.delete(p)
    db.commit()


# --- Visits (day-stamped addresses from calendar scans) ----------------------
@router.get("/visits")
def list_visits(
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> list:
    """Flat list, newest day first; within a day, calendar-event order (scan
    inserts follow the event feed, which Graph sorts by start time). The
    frontend groups these into day cards."""
    rows = (
        db.query(PlaceVisit, Place)
        .join(Place, PlaceVisit.place_id == Place.id)
        .filter(PlaceVisit.user_id == user.id)
        .order_by(PlaceVisit.date.desc(), PlaceVisit.created_at.asc())
        .limit(400)
        .all()
    )
    return [_ser_visit(v, p) for v, p in rows]


@router.delete("/visits/{visit_id}", status_code=204)
def delete_visit(
    visit_id: str,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> None:
    v = (
        db.query(PlaceVisit)
        .filter(PlaceVisit.id == visit_id, PlaceVisit.user_id == user.id)
        .first()
    )
    if v is None:
        raise HTTPException(status_code=404, detail="Visit not found")
    db.delete(v)
    db.commit()


# --- Calendar scan -----------------------------------------------------------
def _extract_visits(events: list[dict]) -> list[dict]:
    """One cheap Haiku call: free-form event text in, structured DAY-STAMPED
    addresses out. Each address carries the date of the event it came from —
    that pairing is what lets the UI rebuild a whole day's route later.
    This is what replaces the 'make everyone write STOP: lines' idea."""
    import chat  # local import: chat pulls in the whole tool stack

    client = chat._anthropic()
    prompt = (
        "Below are Outlook calendar events (subject, date, location field, body) "
        "for a flooring company employee in Southern California. Extract every "
        "PHYSICAL STREET ADDRESS in California that the person likely drove to, "
        "each paired with the date of the event it appears in.\n"
        "Rules:\n"
        "- Only real street addresses (street number + street name, city if present). "
        "If city/state are missing but it's clearly a San Diego-area address, append "
        "'San Diego, CA'.\n"
        "- Skip: Teams/Zoom links, phone numbers, emails, PO boxes, vague place names "
        "with no street address.\n"
        "- date: the containing event's date, exactly as given (YYYY-MM-DD).\n"
        "- label: a short business-purpose label for the trip (it goes on mileage "
        "reports). Use the event's subject when it is already clear and specific; "
        "when the subject is vague, mistyped, or blank, compose the label from the "
        "event's own content instead (e.g. 'Hernandez flooring install'). NEVER "
        "invent names, jobs, or details that are not in the event.\n"
        "- Deduplicate identical address+date pairs. The same address on different "
        "dates is one entry PER date.\n"
        'Reply with ONLY a JSON array: [{"date": "YYYY-MM-DD", "label": "...", '
        '"address": "..."}] — no prose. Reply [] if none.\n\n'
        + json.dumps(events, ensure_ascii=False)
    )
    resp = client.messages.create(
        model=_EXTRACT_MODEL,
        # Month-wide scans can surface 60+ address+date pairs; 1500 tokens
        # truncated the JSON array mid-list.
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        items = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [
        {
            "label": str(i.get("label", ""))[:120],
            "address": str(i.get("address", ""))[:200],
            "date": str(i.get("date", ""))[:10],
        }
        for i in items
        if isinstance(i, dict) and i.get("address")
    ]


@router.post("/scan")
def scan_calendar(
    payload: ScanIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    s, e = _normalize_window(payload.start, payload.end)
    return _scan_window(user, db, s, e)


def _scan_window(user: User, db: Session, s: date, e: date) -> dict:
    """The scan core, shared by /scan and the report preview: pull events,
    extract day-stamped addresses, upsert places, record visits."""
    token = m365.get_graph_token(user, db)
    events = graph.list_calendar_events_for_scan(token, s.isoformat(), e.isoformat())
    if not events:
        return {"scanned": 0, "new": [], "visits": [], "skipped": []}

    known = {
        (p.address or "").strip().lower(): p
        for p in db.query(Place).filter(Place.user_id == user.id).all()
    }
    seen_visits = {
        (v.place_id, v.date)
        for v in db.query(PlaceVisit).filter(PlaceVisit.user_id == user.id).all()
    }
    new_places, new_visits, skipped = [], [], []
    for item in _extract_visits(events):
        addr = item["address"].strip()
        # Events AT the office aren't drives — without this, office-only
        # meetings ("piece rate" etc.) became 0-mile days in the report.
        if addr.lower().startswith(_OFFICE_PREFIX):
            continue
        place = known.get(addr.lower())
        if place is None:
            try:
                lat, lon = _geocode(addr)
            except HTTPException:
                skipped.append(addr)  # extraction found it, the map can't — surface it
                continue
            place = _upsert_place(db, user, addr, date="", lat=lat, lon=lon,
                                  label=item["label"], source="calendar")
            db.flush()  # assigns place.id, which the visit row needs
            known[addr.lower()] = place
            new_places.append(place)
        # Day-stamp the address even when the place itself is old news — a
        # known job site visited again is exactly what the day groups exist
        # to capture. (address, date) pairs are recorded once, ever.
        date = item.get("date") or ""
        if not _ISO_DAY.match(date) or (place.id, date) in seen_visits:
            continue
        visit = PlaceVisit(user_id=user.id, place_id=place.id, date=date,
                           label=item["label"])
        db.add(visit)
        seen_visits.add((place.id, date))
        new_visits.append((visit, place))
    db.commit()
    telemetry.log_event(
        user.id, "mileage", "calendar_scanned",
        f"{len(events)} events, {len(new_places)} new places, "
        f"{len(new_visits)} visits",
    )
    return {
        "scanned": len(events),
        "new": [_ser_place(p) for p in new_places],
        "visits": [_ser_visit(v, p) for v, p in new_visits],
        "skipped": skipped,
    }


# --- Report (calendar -> day routes -> HR-format workbook) --------------------
def _visit_purposes(db: Session, user: User, lo: str, hi: str) -> dict:
    """(date, lowercased address) -> business-purpose label, from the window's
    day-stamped visits. This is how event titles reach the report's PURPOSE
    column for trips that were saved from Load day."""
    rows = (
        db.query(PlaceVisit, Place)
        .join(Place, PlaceVisit.place_id == Place.id)
        .filter(PlaceVisit.user_id == user.id,
                PlaceVisit.date >= lo, PlaceVisit.date <= hi)
        .all()
    )
    return {
        (v.date, (p.address or "").strip().lower()): (v.label or p.label or "")
        for v, p in rows
    }


def _leg_purpose(leg: dict, i: int, n: int, start_addr: str,
                 day: str, purposes: dict) -> str:
    to = (leg.get("to") or "").strip()
    if i == n - 1 and n > 1 and to.lower() == (start_addr or "").strip().lower():
        return "Return to office"
    return purposes.get((day, to.lower()), "")


@router.post("/report/preview")
def report_preview(
    payload: ScanIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> dict:
    """The one-button pull: scan the window's calendar, then hand back every
    day in it — days already in the log as-is (human-reviewed; no recalc, no
    extra Maps spend), days with only calendar visits route-calculated as
    'pending'. Nothing is saved here; the client saves the pending days the
    user keeps checked when they hit Generate."""
    s, e = _normalize_window(payload.start, payload.end)
    scan = _scan_window(user, db, s, e)
    lo, hi = s.isoformat(), e.isoformat()

    vis_rows = (
        db.query(PlaceVisit, Place)
        .join(Place, PlaceVisit.place_id == Place.id)
        .filter(PlaceVisit.user_id == user.id,
                PlaceVisit.date >= lo, PlaceVisit.date <= hi)
        .order_by(PlaceVisit.date.asc(), PlaceVisit.created_at.asc())
        .all()
    )
    vis_by_day: dict[str, list] = {}
    for v, p in vis_rows:
        vis_by_day.setdefault(v.date, []).append((v, p))
    purposes = {
        (v.date, (p.address or "").strip().lower()): (v.label or p.label or "")
        for v, p in vis_rows
    }

    trips = (
        db.query(Trip)
        .filter(Trip.user_id == user.id, Trip.date >= lo, Trip.date <= hi)
        .order_by(Trip.date.asc(), Trip.created_at.asc())
        .all()
    )
    trips_by_day: dict[str, list] = {}
    for t in trips:
        trips_by_day.setdefault(t.date, []).append(t)

    days = []
    for d in sorted(set(vis_by_day) | set(trips_by_day)):
        if d in trips_by_day:
            ser, miles, dollars = [], 0.0, 0.0
            for t in trips_by_day[d]:
                st = _ser_trip(t)
                start_addr = st["legs"][0]["from"] if st["legs"] else ""
                for i, leg in enumerate(st["legs"]):
                    leg["purpose"] = _leg_purpose(leg, i, len(st["legs"]),
                                                  start_addr, d, purposes)
                ser.append(st)
                miles += st["totalMiles"]
                dollars += st["dollars"]
            days.append({"date": d, "status": "saved", "trips": ser,
                         "totalMiles": round(miles, 1),
                         "dollars": round(dollars, 2)})
            continue
        pairs = vis_by_day[d]
        addrs = [OFFICE] + [p.address for _, p in pairs] + [OFFICE]
        try:
            coords = [_coords_for(db, user, a) for a in addrs]
            legs = _route_legs(coords, addrs)
        except HTTPException as ex:
            # One bad address shouldn't sink the whole month — surface the
            # day as failed and keep going.
            days.append({"date": d, "status": "error", "error": str(ex.detail),
                         "stops": [p.address for _, p in pairs]})
            continue
        for i, leg in enumerate(legs):
            leg["purpose"] = _leg_purpose(leg, i, len(legs), addrs[0], d, purposes)
        total = round(sum(l["miles"] for l in legs), 1)
        days.append({
            "date": d, "status": "pending", "legs": legs,
            "totalMiles": total, "rate": MILEAGE_RATE,
            "dollars": round(total * MILEAGE_RATE, 2),
            "resolved": [
                {"address": a, "lat": c[0], "lon": c[1]}
                for a, c in zip(addrs, coords)
            ],
        })

    rates = {round(t.rate if t.rate is not None else MILEAGE_RATE, 4) for t in trips}
    if any(day["status"] == "pending" for day in days):
        rates.add(round(MILEAGE_RATE, 4))
    telemetry.log_event(user.id, "mileage", "report_previewed", f"{len(days)} days")
    return {
        "days": days,
        "scanned": scan["scanned"],
        "newPlaces": len(scan["new"]),
        "newVisits": len(scan["visits"]),
        "skipped": scan["skipped"],
        "mixedRates": len(rates) > 1,
    }


def _report_filename(name: str, s: date, e: date) -> str:
    if s.day == 1 and e == date(s.year, s.month,
                                calendar.monthrange(s.year, s.month)[1]):
        span = s.strftime("%B %Y")
    else:
        span = f"{s.isoformat()} to {e.isoformat()}"
    safe = "".join(ch for ch in (name or "") if ch.isalnum() or ch in " -_").strip()
    return f"Mileage Report - {safe or 'mileage'} - {span}.xlsx"


@router.post("/report/generate")
def report_generate(
    payload: ReportGenIn,
    user: User = Depends(m365.get_session_user),
    db: Session = Depends(get_db),
) -> Response:
    """Build the HR-format workbook from LOGGED trips only — the log is the
    source of truth, so the same report can be regenerated identically later.
    The client saves any still-pending preview days before calling this."""
    s, e = _normalize_window(payload.start, payload.end)
    q = db.query(Trip).filter(Trip.user_id == user.id)
    if payload.dates:
        # Explicit day list — "Add from log" days may sit outside the picked
        # window, so the list wins over the window filter.
        q = q.filter(Trip.date.in_([str(d) for d in payload.dates]))
    else:
        q = q.filter(Trip.date >= s.isoformat(), Trip.date <= e.isoformat())
    trips = q.order_by(Trip.date.asc(), Trip.created_at.asc()).all()
    if not trips:
        raise HTTPException(
            status_code=400,
            detail="No logged trips for the selected days — pull & calculate first",
        )

    dates_present = sorted({t.date for t in trips})
    purposes = _visit_purposes(db, user, dates_present[0], dates_present[-1])
    by_day: dict[str, list] = {}
    for t in trips:
        by_day.setdefault(t.date, []).append(t)

    days, rates, total_dollars = [], set(), 0.0
    for d in sorted(by_day):
        rows, day_rate = [], MILEAGE_RATE
        for t in by_day[d]:
            legs = json.loads(t.legs_json)
            start_addr = legs[0]["from"] if legs else ""
            for i, leg in enumerate(legs):
                rows.append({
                    "purpose": _leg_purpose(leg, i, len(legs), start_addr, d, purposes),
                    "frm": leg["from"],
                    "to": leg["to"],
                    "miles": leg["miles"],
                })
            rate = t.rate if t.rate is not None else MILEAGE_RATE
            day_rate = round(rate, 4)
            rates.add(day_rate)
            total_dollars += t.total_miles * rate
        days.append({"date": d, "rows": rows, "rate": day_rate})

    header_rate = next(iter(rates)) if len(rates) == 1 else None
    xlsx = mileage_report.build_report(user.name or "", days, header_rate,
                                       total_dollars)
    telemetry.log_event(user.id, "mileage", "report_generated",
                        f"{len(days)} days, {len(trips)} trips")
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition":
                f'attachment; filename="{_report_filename(user.name, s, e)}"',
        },
    )
