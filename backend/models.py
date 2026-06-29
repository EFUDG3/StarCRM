"""SQLAlchemy models.

Note on dates: next_due and Interaction.date are stored as ISO date strings
(YYYY-MM-DD) to match exactly what the React frontend produces and consumes.
This keeps the POC simple. Move these to real DATE columns when you harden for
production if you want server-side date math.
"""
import uuid

from sqlalchemy import Column, String, Text, DateTime, ForeignKey, LargeBinary, func
from sqlalchemy.orm import relationship

from database import Base


def _contact_id() -> str:
    return "c" + uuid.uuid4().hex[:12]


def _interaction_id() -> str:
    return "i" + uuid.uuid4().hex[:12]


def _user_id() -> str:
    return "u" + uuid.uuid4().hex[:12]


class User(Base):
    """A profile that owns its own set of contacts. No password — selecting a
    user is a client-side switch for now. When SSO lands, this table gains the
    Microsoft object id / email and the rest of the model is unchanged."""

    __tablename__ = "users"

    id = Column(String, primary_key=True, default=_user_id)
    name = Column(String, nullable=False)
    # Set once the profile is linked to a Microsoft Entra (M365) identity.
    microsoft_oid = Column(String, nullable=True, unique=True, index=True)
    email = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    contacts = relationship(
        "Contact",
        back_populates="owner",
        cascade="all, delete-orphan",
    )


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(String, primary_key=True, default=_contact_id)
    user_id = Column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String, nullable=False)
    company = Column(String, default="")
    role = Column(String, default="")
    email = Column(String, default="")
    phone = Column(String, default="")
    category = Column(String, default="bd")  # bd|gc|vendor|property|client|sub|designer|insurance|other
    category_label = Column(String, nullable=True)  # free-text label when category == "other"
    next_action = Column(Text, default="")
    next_due = Column(String, default="")    # ISO date string, may be empty
    notes = Column(Text, default="")
    # Optional business-card photo (downscaled JPEG) the contact was created from.
    card_image = Column(LargeBinary, nullable=True)
    card_image_type = Column(String, nullable=True)  # e.g. "image/jpeg"
    created_at = Column(DateTime, server_default=func.now())

    owner = relationship("User", back_populates="contacts")
    interactions = relationship(
        "Interaction",
        back_populates="contact",
        cascade="all, delete-orphan",
    )


class Interaction(Base):
    __tablename__ = "interactions"

    id = Column(String, primary_key=True, default=_interaction_id)
    contact_id = Column(String, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    date = Column(String, nullable=False)  # ISO date string
    note = Column(Text, nullable=False)

    contact = relationship("Contact", back_populates="interactions")
