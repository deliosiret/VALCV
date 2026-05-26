from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EvaluationMode(str, Enum):
    manual = "manual"
    automatic = "automatic"


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    ai_evaluation_locked: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    criteria: Mapped[list["Criterion"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="Criterion.order_index"
    )
    categories: Mapped[list["TemplateCategory"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="TemplateCategory.order_index"
    )
    candidates: Mapped[list["Candidate"]] = relationship(back_populates="template")


class TemplateCategory(Base):
    __tablename__ = "template_categories"
    __table_args__ = (UniqueConstraint("template_id", "name", name="uq_template_category_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    weight: Mapped[float] = mapped_column(Float, default=0)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    template: Mapped[Template] = relationship(back_populates="categories")


class Criterion(Base):
    __tablename__ = "criteria"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(12))
    category: Mapped[str] = mapped_column(String(160), index=True)
    aspect: Mapped[str] = mapped_column(Text)
    category_weight: Mapped[float] = mapped_column(Float)
    within_category_weight: Mapped[float] = mapped_column(Float)
    global_weight: Mapped[float] = mapped_column(Float)
    scale: Mapped[str] = mapped_column(String(40), default="0 a 5")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluation_mode: Mapped[EvaluationMode] = mapped_column(
        SAEnum(EvaluationMode, name="evaluation_mode"), default=EvaluationMode.manual
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    template: Mapped[Template] = relationship(back_populates="criteria")
    scores: Mapped[list["Score"]] = relationship(back_populates="criterion", cascade="all, delete-orphan")


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("templates.id"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    document_id: Mapped[str] = mapped_column(String(80), default="")
    evaluator: Mapped[str] = mapped_column(String(120), default="")
    comments: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    template: Mapped[Template] = relationship(back_populates="candidates")
    files: Mapped[list["CandidateFile"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    scores: Mapped[list["Score"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")


class CandidateFile(Base):
    __tablename__ = "candidate_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    original_name: Mapped[str] = mapped_column(String(240))
    stored_name: Mapped[str] = mapped_column(String(260), unique=True)
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    candidate: Mapped[Candidate] = relationship(back_populates="files")


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criteria.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(24), default="manual")
    rationale: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    candidate: Mapped[Candidate] = relationship(back_populates="scores")
    criterion: Mapped[Criterion] = relationship(back_populates="scores")
    file_references: Mapped[list["ScoreFileReference"]] = relationship(
        back_populates="score", cascade="all, delete-orphan"
    )

    @property
    def file_ids(self) -> list[int]:
        return [reference.file_id for reference in self.file_references]


class ScoreFileReference(Base):
    __tablename__ = "score_file_references"
    __table_args__ = (UniqueConstraint("score_id", "file_id", name="uq_score_file_reference"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    score_id: Mapped[int] = mapped_column(ForeignKey("scores.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("candidate_files.id", ondelete="CASCADE"), index=True)

    score: Mapped[Score] = relationship(back_populates="file_references")
    file: Mapped[CandidateFile] = relationship()


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(220))
    is_admin: Mapped[bool] = mapped_column(default=False)
    can_view_all: Mapped[bool] = mapped_column(default=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token: Mapped[str] = mapped_column(String(120), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship()
