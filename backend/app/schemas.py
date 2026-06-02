from datetime import datetime

from pydantic import BaseModel, Field

from app.models import EvaluationMode, UserRole


class CriterionIn(BaseModel):
    id: int | None = None
    code: str = ""
    category: str
    aspect: str
    category_weight: float = 0
    within_category_weight: float = 0
    global_weight: float = 0
    scale: str = "0 a 5"
    notes: str = ""
    is_critical: bool = False
    evaluation_mode: EvaluationMode = EvaluationMode.manual
    order_index: int = 0


class CriterionOut(CriterionIn):
    id: int

    class Config:
        from_attributes = True


class CategoryIn(BaseModel):
    name: str
    weight: float = 0
    order_index: int = 0


class CategoryOut(CategoryIn):
    id: int

    class Config:
        from_attributes = True


class TemplateCreate(BaseModel):
    name: str
    description: str = ""
    ai_evaluation_locked: bool = True
    categories: list[CategoryIn] = Field(default_factory=list)
    criteria: list[CriterionIn] = Field(default_factory=list)


class TemplateOut(BaseModel):
    id: int
    name: str
    description: str
    ai_evaluation_locked: bool = True
    is_archived: bool = False
    created_at: datetime
    categories: list[CategoryOut] = Field(default_factory=list)
    criteria: list[CriterionOut]

    class Config:
        from_attributes = True


class CandidateCreate(BaseModel):
    name: str
    document_id: str = ""
    comments: str = ""
    template_id: int


class CandidatePatch(BaseModel):
    name: str | None = None
    document_id: str | None = None
    evaluator: str | None = None
    comments: str | None = None
    final_decision: str | None = None
    template_id: int | None = None


class FileOut(BaseModel):
    id: int
    original_name: str
    mime_type: str
    size_bytes: int
    created_at: datetime

    class Config:
        from_attributes = True


class ScoreIn(BaseModel):
    criterion_id: int
    score: float = Field(ge=0, le=5)
    rationale: str = ""
    evaluator_note: str = ""
    file_ids: list[int] = Field(default_factory=list)


class ScoreOut(BaseModel):
    id: int
    criterion_id: int
    score: float
    source: str
    rationale: str
    evaluator_note: str
    file_ids: list[int] = Field(default_factory=list)
    updated_at: datetime

    class Config:
        from_attributes = True


class CandidateOut(BaseModel):
    id: int
    template_id: int
    name: str
    document_id: str
    evaluator: str
    evaluator_user_id: int | None = None
    comments: str
    final_decision: str = ""
    ai_bonus_score: float = 0
    ai_bonus_rationale: str = ""
    created_at: datetime
    files: list[FileOut]
    scores: list[ScoreOut]

    class Config:
        from_attributes = True


class SummaryCandidate(BaseModel):
    id: int
    name: str
    document_id: str
    global_score: float
    recommendation: str
    categories: dict[str, float]


class SummaryOut(BaseModel):
    candidates: list[SummaryCandidate]
    categories: list[str]


class AISettingsIn(BaseModel):
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.1-flash-lite"


class AISettingsOut(BaseModel):
    gemini_api_key_configured: bool
    gemini_api_key_masked: str = ""
    gemini_model: str


class LoginIn(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    first_name: str = ""
    last_name: str = ""
    position: str = ""
    area: str = ""
    employee_code: str = ""
    role: UserRole = UserRole.evaluator


class UserOut(BaseModel):
    id: int
    username: str
    first_name: str = ""
    last_name: str = ""
    position: str = ""
    area: str = ""
    employee_code: str = ""
    role: UserRole = UserRole.evaluator
    is_admin: bool
    can_view_all: bool
    is_active: bool

    class Config:
        from_attributes = True


class TokenOut(BaseModel):
    token: str
    user: UserOut
