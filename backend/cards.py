"""Business-card helpers: image downscaling and Claude vision extraction.

Isolated from main.py so the Claude call can be mocked in tests and so the
ANTHROPIC_API_KEY requirement only bites when the scan feature is actually used.
"""
import base64
import io
import json
import os

from PIL import Image

# Vision-capable Claude model. Haiku is cheap and fast — plenty for card text.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

_PROMPT = (
    "You are reading a business card for a flooring & remodeling company's CRM. "
    "Return ONLY a JSON object with exactly these keys: name, company, role, "
    "email, phones, category.\n"
    "- name: the person's full name.\n"
    "- company, role: strings ('role' is the job title). Empty string if absent.\n"
    "- email: string, empty if absent.\n"
    "- phones: an ARRAY of objects {type, number}, one per phone number on the "
    "card. 'type' is one of 'cell', 'work', 'home', 'other' — infer from labels "
    "(C/cell/mobile/M -> cell; O/office/direct/main/tel -> work; H/home -> home; "
    "fax -> other). If a number has no label, use 'work'. Empty array if none.\n"
    "- category: the best-fit CRM relationship type, ONE of: gc (general "
    "contractor), sub (subcontractor/trade), vendor (materials or product "
    "supplier), property (property management company or landlord), client "
    "(end customer/homeowner), designer (designer or architect), insurance "
    "(insurance company or adjuster), healthcare (hospital, clinic, or medical "
    "group such as Sharp, Kaiser, or Scripps), bd (a generic business lead when "
    "genuinely unsure), other. Choose the most specific fit.\n"
    "Do not guess missing details. Return just the JSON, no prose or code fences."
)

# Valid CRM categories and phone types (mirror the frontend). Anything the model
# returns outside these sets falls back to a safe default in coerce_fields.
_CATEGORIES = {
    "bd", "gc", "vendor", "property", "client", "sub",
    "designer", "insurance", "healthcare", "other",
}
_PHONE_TYPES = {"cell", "work", "home", "other"}

_EMPTY = {"name": "", "company": "", "role": "", "email": "", "phone": "", "phones": [], "category": "bd"}


def downscale_to_jpeg(raw: bytes, max_dim: int = 1200, quality: int = 85) -> bytes:
    """Resize an image down to max_dim on its longest side and re-encode as
    JPEG, keeping storage and the vision payload small."""
    img = Image.open(io.BytesIO(raw))
    img = img.convert("RGB")  # normalize mode (drops alpha) for JPEG
    img.thumbnail((max_dim, max_dim))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def to_data_url(jpeg_bytes: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode()


def parse_data_url(data_url: str):
    """Return (raw_bytes, mime) from a data URL, or (None, None) if invalid."""
    if not data_url or not data_url.startswith("data:"):
        return None, None
    header, _, b64 = data_url.partition(",")
    mime = header[5:].split(";")[0] or "application/octet-stream"
    try:
        return base64.b64decode(b64), mime
    except Exception:
        return None, None


def extract_fields(jpeg_bytes: bytes) -> dict:
    """Call Claude vision to pull structured contact fields from a card image.

    Raises RuntimeError if the API key is missing. Always returns a dict with
    the five expected keys (empty strings where the model omits them).
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — the card scanner needs it. Add it to "
            "backend/.env (or Secret Manager in production)."
        )
    import anthropic  # imported lazily so the dep/key only matter when scanning

    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.b64encode(jpeg_bytes).decode()
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    )
    text = "".join(
        b.text for b in msg.content if getattr(b, "type", "") == "text"
    ).strip()
    return coerce_fields(text)


def coerce_fields(text: str) -> dict:
    """Parse the model's JSON, tolerating stray prose or code fences. Validates
    category + phone types against the known sets, and always returns `phone`
    (the primary number) for older callers alongside the `phones` array."""
    fields = {"name": "", "company": "", "role": "", "email": "", "phone": "", "phones": [], "category": "bd"}
    try:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return fields
        data = json.loads(text[start : end + 1])
        for k in ("name", "company", "role", "email"):
            v = data.get(k, "")
            fields[k] = str(v).strip() if v is not None else ""

        cat = str(data.get("category", "") or "").strip().lower()
        fields["category"] = cat if cat in _CATEGORIES else "bd"

        phones = []
        raw = data.get("phones")
        if isinstance(raw, list):
            for p in raw:
                if isinstance(p, dict):
                    num = str(p.get("number", "") or "").strip()
                    typ = str(p.get("type", "") or "").strip().lower()
                else:
                    num, typ = str(p).strip(), ""
                if num:
                    phones.append({"type": typ if typ in _PHONE_TYPES else "work", "number": num})
        elif isinstance(raw, str) and raw.strip():
            phones.append({"type": "work", "number": raw.strip()})
        # Fall back to a legacy single `phone` key if the model used one.
        if not phones and str(data.get("phone", "") or "").strip():
            phones.append({"type": "work", "number": str(data["phone"]).strip()})

        fields["phones"] = phones[:6]
        fields["phone"] = phones[0]["number"] if phones else ""
    except Exception:
        pass
    return fields
