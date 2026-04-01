"""
app/db/models.py  —  SQLAlchemy ORM models

Covers the core SourceIQ domain entities:
  Project, Supplier, RFPQuestion, SupplierResponse,
  Communication, PricingQuote, Drawing, AwardScenario

All tables use UUID primary keys and UTC timestamps.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text, JSON,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


# ── Project ───────────────────────────────────────────────────────────────────

class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    category = Column(String)
    status = Column(String, default="draft")  # draft | active | awarded | closed
    submission_deadline = Column(DateTime)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    questions = relationship("RFPQuestion", back_populates="project", cascade="all, delete")
    suppliers = relationship("ProjectSupplier", back_populates="project", cascade="all, delete")
    communications = relationship("Communication", back_populates="project", cascade="all, delete")
    scenarios = relationship("AwardScenario", back_populates="project", cascade="all, delete")


# ── Supplier ──────────────────────────────────────────────────────────────────

class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    country = Column(String)
    category = Column(String)
    onboarding_status = Column(String, default="pending")  # pending | approved | rejected
    onboarding_score = Column(Float)
    created_at = Column(DateTime, default=_now)

    project_links = relationship("ProjectSupplier", back_populates="supplier")


class ProjectSupplier(Base):
    """Many-to-many: Project ↔ Supplier with per-project status."""
    __tablename__ = "project_suppliers"

    id = Column(String, primary_key=True, default=_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    supplier_id = Column(String, ForeignKey("suppliers.id"), nullable=False)
    status = Column(String, default="invited")  # invited | submitted | evaluated | awarded | regret
    invited_at = Column(DateTime)
    submitted_at = Column(DateTime)

    project = relationship("Project", back_populates="suppliers")
    supplier = relationship("Supplier", back_populates="project_links")
    responses = relationship("SupplierResponse", back_populates="project_supplier", cascade="all, delete")
    pricing_quotes = relationship("PricingQuote", back_populates="project_supplier", cascade="all, delete")


# ── RFP Questions ─────────────────────────────────────────────────────────────

class RFPQuestion(Base):
    __tablename__ = "rfp_questions"

    id = Column(String, primary_key=True, default=_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    question_id = Column(String, nullable=False)  # short code e.g. "Q1"
    text = Column(Text, nullable=False)
    category = Column(String)
    weight = Column(Float, default=1.0)
    is_mandatory = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_now)

    project = relationship("Project", back_populates="questions")
    drawings = relationship("Drawing", back_populates="question")


# ── Supplier Responses ────────────────────────────────────────────────────────

class SupplierResponse(Base):
    __tablename__ = "supplier_responses"

    id = Column(String, primary_key=True, default=_uuid)
    project_supplier_id = Column(String, ForeignKey("project_suppliers.id"), nullable=False)
    question_id = Column(String, nullable=False)
    answer_text = Column(Text)
    ai_score = Column(Float)
    ai_rationale = Column(Text)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=_now)

    project_supplier = relationship("ProjectSupplier", back_populates="responses")


# ── Communications ────────────────────────────────────────────────────────────

class Communication(Base):
    __tablename__ = "communications"

    id = Column(String, primary_key=True, default=_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    supplier_id = Column(String, ForeignKey("suppliers.id"))
    email_type = Column(String)  # rfp_invite | deadline_reminder | etc.
    subject = Column(String)
    body = Column(Text)
    recipient_email = Column(String)
    sent_at = Column(DateTime)
    status = Column(String, default="drafted")  # drafted | sent | failed

    project = relationship("Project", back_populates="communications")


# ── Pricing Quotes ────────────────────────────────────────────────────────────

class PricingQuote(Base):
    __tablename__ = "pricing_quotes"

    id = Column(String, primary_key=True, default=_uuid)
    project_supplier_id = Column(String, ForeignKey("project_suppliers.id"), nullable=False)
    line_items = Column(JSON)  # list of {description, qty, unit_price, uom, currency}
    total_cost = Column(Float)
    currency = Column(String, default="USD")
    valid_until = Column(DateTime)
    validity_status = Column(String)  # valid | expiring_soon | expired
    created_at = Column(DateTime, default=_now)

    project_supplier = relationship("ProjectSupplier", back_populates="pricing_quotes")


# ── Drawings ──────────────────────────────────────────────────────────────────

class Drawing(Base):
    __tablename__ = "drawings"

    id = Column(String, primary_key=True, default=_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    question_id = Column(String, ForeignKey("rfp_questions.id"))
    filename = Column(String, nullable=False)
    file_url = Column(String, nullable=False)  # S3 / R2 URL
    part_number = Column(String)
    revision = Column(String, default="A")
    file_type = Column(String)  # pdf | dwg | dxf | png | svg | tiff
    uploaded_at = Column(DateTime, default=_now)

    question = relationship("RFPQuestion", back_populates="drawings")


# ── Award Scenarios ───────────────────────────────────────────────────────────

class AwardScenario(Base):
    __tablename__ = "award_scenarios"

    id = Column(String, primary_key=True, default=_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    description = Column(String)
    award_split = Column(JSON)   # {supplier_name: awarded_value}
    total_cost = Column(Float)
    tech_scores = Column(JSON)
    narrative = Column(Text)
    approval_status = Column(String, default="draft")  # draft | pending_approval | approved | rejected
    created_at = Column(DateTime, default=_now)

    project = relationship("Project", back_populates="scenarios")
