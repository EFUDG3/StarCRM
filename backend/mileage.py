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
import json
import os
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import graph
import m365
import telemetry
from database import get_db
from models import Place, PlaceVisit, Trip, User
from schemas import PlaceIn, RouteIn, ScanIn, TripIn

AZURE_MAPS_KEY = os.getenv("AZURE_MAPS_KEY", "")
ATLAS = "https://atlas.microsoft.com"
# IRS standard mileage rate, $/mile. Override with the MILEAGE_RATE env var
# when the IRS updates it — no redeploy of code, just the env value.
MILEAGE_RATE = float(os.getenv("MILEAGE_RATE", "0.70"))
OFFICE = "4610 Alvarado Canyon Rd, San Diego, CA 92120"
_EXTRACT_MODEL = os.getenv("SCAN_MODEL", "claude-haiku-4-5")
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

router = APIRouter(prefix="/api/mileage", tags=["mileage"])


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
        "rate": round(rate, 2),
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
        rate=round(float(payload.rate), 2),
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
        max_tokens=1500,
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
    token = m365.get_graph_token(user, db)
    events = graph.list_calendar_events_for_scan(token, payload.start, payload.end)
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
