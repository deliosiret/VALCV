import hashlib
import secrets
import shutil
import uuid
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session, selectinload

from app.ai import candidate_document_signature, create_candidate_context_cache, evaluate_candidate_bonus_with_gemini, evaluate_candidate_with_gemini, generate_template_with_gemini
from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.models import AICandidateCache, AIInteractionLog, AppSetting, AuthSession, Candidate, CandidateFile, Criterion, EvaluationMode, Score, Template, TemplateCategory, User, UserRole
from app.reports import build_candidate_report
from app.schemas import (
    AISettingsIn,
    AISettingsOut,
    AIInteractionLogsOut,
    CandidateCreate,
    CandidateOut,
    CandidatePatch,
    CriterionIn,
    LoginIn,
    PasswordChangeIn,
    PublicPositionOut,
    ScoreIn,
    ScoreOut,
    SummaryOut,
    TemplateCreate,
    TemplateOut,
    TokenOut,
    UserCreate,
    UserQuotaPatch,
    UserOut,
    UserUpdate,
)
from app.scoring import summarize_candidate, upsert_score
from app.seed import seed_initial_template

app = FastAPI(title="VALCV API", version="0.1.0")
TEMPORARY_PASSWORD = "temporal123"
TERMS_VERSION = "VALCV-2026-06"
POSITION_STATUSES = {"open", "closed"}
COMPETITION_SCOPES = {"internal", "external"}
PUBLIC_EVALUATOR_NAME = "Postulación pública"
ALLOWED_APPLICATION_FILE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}


def serialize_score(score: Score) -> dict:
    return {
        "id": score.id,
        "criterion_id": score.criterion_id,
        "score": score.score,
        "source": score.source,
        "rationale": score.rationale,
        "evaluator_note": score.evaluator_note,
        "file_ids": [reference.file_id for reference in score.file_references],
        "updated_at": score.updated_at,
    }


def clean_file_ids(raw_file_ids, valid_file_ids: set[int]) -> list[int]:
    cleaned = []
    for raw_file_id in raw_file_ids or []:
        try:
            file_id = int(raw_file_id)
        except (TypeError, ValueError):
            continue
        if file_id in valid_file_ids:
            cleaned.append(file_id)
    return list(dict.fromkeys(cleaned))


def clean_template_publication_fields(payload: TemplateCreate) -> tuple[str, str]:
    position_status = (payload.position_status or "closed").strip().lower()
    competition_scope = (payload.competition_scope or "external").strip().lower()
    if position_status not in POSITION_STATUSES:
        raise HTTPException(status_code=400, detail="Estado de posición inválido.")
    if competition_scope not in COMPETITION_SCOPES:
        raise HTTPException(status_code=400, detail="Tipo de concurso inválido.")
    return position_status, competition_scope


def evaluation_snapshot(candidate: Candidate, criteria: list[Criterion]) -> list[dict]:
    score_by_criterion = {score.criterion_id: score for score in candidate.scores}
    rows = []
    for criterion in criteria:
        score = score_by_criterion.get(criterion.id)
        rows.append(
            {
                "criterion_id": criterion.id,
                "code": criterion.code,
                "category": criterion.category,
                "aspect": criterion.aspect,
                "score": score.score if score else None,
                "rationale": score.rationale if score else "",
                "evaluator_note": score.evaluator_note if score else "",
                "source": score.source if score else "",
            }
        )
    return rows


def safe_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(char if char.isalnum() else "_" for char in normalized).strip("_")
    return cleaned or "reporte"


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 150_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _, salt, digest = password_hash.split("$", 2)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), f"pbkdf2_sha256${salt}${digest}")


def authenticate_token(db: Session, token: str) -> User:
    session = db.query(AuthSession).options(selectinload(AuthSession.user)).filter(AuthSession.token == token).first()
    if not session or not session.user.is_active:
        raise HTTPException(status_code=401, detail="Sesión inválida o vencida.")
    return session.user


def get_current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Inicia sesión para continuar.")
    return authenticate_token(db, authorization.removeprefix("Bearer ").strip())


ROLE_PERMISSIONS = {
    UserRole.administrator: {
        "manage_users",
        "manage_ai_settings",
        "manage_templates",
        "manage_candidates",
        "evaluate_candidates",
        "view_results",
    },
    UserRole.template_manager: {"manage_templates", "view_results"},
    UserRole.evaluator: {"manage_candidates", "evaluate_candidates", "view_results"},
    UserRole.hr: {"manage_candidates", "view_results"},
    UserRole.viewer: {"view_results"},
}


def user_permissions(user: User) -> set[str]:
    if user.is_admin:
        return ROLE_PERMISSIONS[UserRole.administrator]
    return ROLE_PERMISSIONS.get(user.role, set())


def require_permission(permission: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.must_change_password:
            raise HTTPException(status_code=403, detail="Debes cambiar tu contraseña temporal antes de continuar.")
        if not user.terms_accepted_at or user.terms_version != TERMS_VERSION:
            raise HTTPException(status_code=403, detail="Debes aceptar las condiciones de uso de la plataforma antes de continuar.")
        if permission not in user_permissions(user):
            raise HTTPException(status_code=403, detail="No tienes permiso para realizar esta acción.")
        return user

    return dependency


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Debes cambiar tu contraseña temporal antes de continuar.")
    if not user.terms_accepted_at or user.terms_version != TERMS_VERSION:
        raise HTTPException(status_code=403, detail="Debes aceptar las condiciones de uso de la plataforma antes de continuar.")
    if "manage_users" not in user_permissions(user):
        raise HTTPException(status_code=403, detail="Solo un usuario administrador puede realizar esta acción.")
    return user


def user_display_name(user: User) -> str:
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    return full_name or user.username


def seed_admin_user(db: Session):
    exists = db.query(User).filter(User.username == settings.admin_username).first()
    if exists:
        return
    db.add(
        User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            must_change_password=False,
            first_name="Administrador",
            last_name="",
            position="Administrador de plataforma",
            area="Sistema",
            employee_code="",
            role=UserRole.administrator,
            is_admin=True,
            can_view_all=True,
            is_active=True,
        )
    )
    db.commit()


def normalized_criteria(criteria: list[CriterionIn]) -> list[dict]:
    rows = []
    counters: dict[str, int] = {}
    for idx, criterion in enumerate(criteria):
        row = criterion.model_dump(exclude={"order_index"})
        category = row["category"].strip()
        counters[category] = counters.get(category, 0) + 1
        prefix = "".join(part[:1] for part in category.split() if part).upper()[:3] or "C"
        row["code"] = row["code"].strip() or f"{prefix}{counters[category]}"
        row["category"] = category
        row["aspect"] = row["aspect"].strip()
        row["category_weight"] = max(0.0, float(row["category_weight"] or 0))
        row["within_category_weight"] = 0.0 if row.get("is_critical") else max(0.0, float(row["within_category_weight"] or 0))
        row["global_weight"] = row["category_weight"] * row["within_category_weight"]
        row["order_index"] = idx
        rows.append(row)
    return rows


def normalized_categories(payload: TemplateCreate) -> list[dict]:
    rows = []
    seen = set()
    source = payload.categories or []
    if not source:
        by_name: dict[str, float] = {}
        for criterion in payload.criteria:
            name = criterion.category.strip()
            if name and name not in by_name:
                by_name[name] = float(criterion.category_weight or 0)
        source = [type("CategoryLike", (), {"name": name, "weight": weight}) for name, weight in by_name.items()]
    for index, category in enumerate(source):
        name = category.name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append({"name": name, "weight": max(0.0, float(category.weight or 0)), "order_index": index})
    return rows


def normalized_template_parts(payload: TemplateCreate) -> tuple[list[dict], list[dict]]:
    categories = normalized_categories(payload)
    weights = {category["name"]: category["weight"] for category in categories}
    criteria = []
    counters: dict[str, int] = {}
    for idx, criterion in enumerate(payload.criteria):
        row = criterion.model_dump(exclude={"order_index"})
        category = row["category"].strip()
        counters[category] = counters.get(category, 0) + 1
        prefix = "".join(part[:1] for part in category.split() if part).upper()[:3] or "C"
        row["code"] = row["code"].strip() or f"{prefix}{counters[category]}"
        row["category"] = category
        row["aspect"] = row["aspect"].strip()
        row["category_weight"] = weights.get(category, max(0.0, float(row["category_weight"] or 0)))
        row["within_category_weight"] = 0.0 if row.get("is_critical") else max(0.0, float(row["within_category_weight"] or 0))
        row["global_weight"] = row["category_weight"] * row["within_category_weight"]
        row["order_index"] = idx
        criteria.append(row)
    return categories, criteria


def validate_template_structure(categories: list[dict], criteria: list[dict]) -> None:
    if len(categories) < MIN_TEMPLATE_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"La plantilla debe tener al menos {MIN_TEMPLATE_CATEGORIES} categorías.",
        )
    criteria_by_category: dict[str, int] = {category["name"]: 0 for category in categories}
    for criterion in criteria:
        if criterion["category"] in criteria_by_category and criterion["aspect"]:
            criteria_by_category[criterion["category"]] += 1
    missing = [name for name, count in criteria_by_category.items() if count < MIN_CRITERIA_PER_CATEGORY]
    if missing:
        raise HTTPException(
            status_code=400,
            detail="Cada categoría debe tener al menos un criterio. Revisa: " + ", ".join(missing),
        )


def validate_recommendation_scale(payload: TemplateCreate) -> None:
    if not (payload.review_threshold <= payload.recommended_threshold <= payload.highly_recommended_threshold):
        raise HTTPException(
            status_code=400,
            detail="La escala debe cumplir: Requiere revisión <= Recomendable <= Altamente recomendable.",
        )


def criterion_requires_score_reset(criterion: Criterion, row: dict) -> bool:
    comparable_fields = (
        "category",
        "aspect",
        "scale",
        "notes",
        "is_critical",
        "evaluation_mode",
    )
    return any(getattr(criterion, field) != row[field] for field in comparable_fields)


def delete_scores_for_criterion(db: Session, criterion_id: int):
    for score in db.query(Score).filter(Score.criterion_id == criterion_id).all():
        db.delete(score)


def normalize_weight_rows(rows: list[dict], weight_key: str) -> list[dict]:
    if not rows:
        return rows
    weights = [max(0.0, float(row.get(weight_key) or 0)) for row in rows]
    total = sum(weights)
    if total <= 0:
        even = 1 / len(rows)
        for row in rows:
            row[weight_key] = even
        return rows
    for row, weight in zip(rows, weights):
        row[weight_key] = weight / total
    return rows


def clean_generated_template(raw: dict) -> TemplateCreate:
    categories = []
    category_names: set[str] = set()
    for index, category in enumerate(raw.get("categories") or []):
        name = str(category.get("name", "")).strip()
        if not name or name in category_names:
            continue
        category_names.add(name)
        categories.append({"name": name, "weight": category.get("weight", 0), "order_index": index})
    normalize_weight_rows(categories, "weight")

    criteria = []
    category_weights = {category["name"]: category["weight"] for category in categories}
    for index, criterion in enumerate(raw.get("criteria") or []):
        category = str(criterion.get("category", "")).strip()
        aspect = str(criterion.get("aspect", "")).strip()
        if not category or not aspect:
            continue
        if category not in category_weights:
            category_weights[category] = 0
            categories.append({"name": category, "weight": 0, "order_index": len(categories)})
        is_critical = bool(criterion.get("is_critical", False))
        mode = str(criterion.get("evaluation_mode", "manual")).lower()
        if mode not in {"manual", "automatic"}:
            mode = "manual"
        within_weight = 0 if is_critical else max(0.0, float(criterion.get("within_category_weight") or 0))
        criteria.append(
            {
                "code": "",
                "category": category,
                "aspect": aspect,
                "category_weight": category_weights.get(category, 0),
                "within_category_weight": within_weight,
                "global_weight": 0,
                "scale": str(criterion.get("scale") or "0 a 5").strip() or "0 a 5",
                "notes": str(criterion.get("notes") or "").strip(),
                "is_critical": is_critical,
                "evaluation_mode": mode,
                "order_index": index,
            }
        )

    if not categories:
        categories = [{"name": "Requisitos generales", "weight": 1, "order_index": 0}]
    normalize_weight_rows(categories, "weight")
    category_weights = {category["name"]: category["weight"] for category in categories}
    for category in categories:
        child_rows = [criterion for criterion in criteria if criterion["category"] == category["name"] and not criterion["is_critical"]]
        normalize_weight_rows(child_rows, "within_category_weight")
    for criterion in criteria:
        criterion["category_weight"] = category_weights.get(criterion["category"], 0)
        criterion["global_weight"] = 0 if criterion["is_critical"] else criterion["category_weight"] * criterion["within_category_weight"]

    payload = {
        "name": str(raw.get("name") or "Plantilla generada con IA").strip(),
        "description": str(raw.get("description") or "Borrador generado con IA a partir de requisitos de la posición.").strip(),
        "ai_evaluation_locked": bool(raw.get("ai_evaluation_locked", True)),
        "categories": categories,
        "criteria": criteria,
    }
    return TemplateCreate.model_validate(payload)


def sync_template_categories(db: Session):
    templates = db.query(Template).options(selectinload(Template.criteria), selectinload(Template.categories)).all()
    changed = False
    for template in templates:
        if template.categories:
            continue
        seen: dict[str, float] = {}
        for criterion in template.criteria:
            if criterion.category not in seen:
                seen[criterion.category] = criterion.category_weight
        for index, (name, weight) in enumerate(seen.items()):
            template.categories.append(TemplateCategory(name=name, weight=weight, order_index=index))
            changed = True
    if changed:
        db.commit()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


AI_MODEL_OPTIONS = [
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]
MIN_TEMPLATE_CATEGORIES = 3
MIN_CRITERIA_PER_CATEGORY = 1


@app.on_event("startup")
def startup():
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    with SessionLocal() as db:
        seed_initial_template(db)
        seed_admin_user(db)
        sync_template_categories(db)


def ensure_schema():
    inspector = inspect(engine)
    enum_values = ", ".join(f"'{role.value}'" for role in UserRole)
    with engine.begin() as connection:
        connection.execute(
            text(
                "DO $$ BEGIN "
                f"CREATE TYPE user_role AS ENUM ({enum_values}); "
                "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
            )
        )
    template_columns = {column["name"] for column in inspector.get_columns("templates")}
    if "ai_evaluation_locked" not in template_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE templates ADD COLUMN ai_evaluation_locked BOOLEAN NOT NULL DEFAULT TRUE"))
    if "is_archived" not in template_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE templates ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE"))
    template_publication_columns = {
        "position_status": "VARCHAR(24) NOT NULL DEFAULT 'closed'",
        "competition_scope": "VARCHAR(24) NOT NULL DEFAULT 'external'",
    }
    for column_name, definition in template_publication_columns.items():
        if column_name not in template_columns:
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE templates ADD COLUMN {column_name} {definition}"))
    template_thresholds = {
        "highly_recommended_threshold": "DOUBLE PRECISION NOT NULL DEFAULT 0.85",
        "recommended_threshold": "DOUBLE PRECISION NOT NULL DEFAULT 0.7",
        "review_threshold": "DOUBLE PRECISION NOT NULL DEFAULT 0.55",
    }
    for column_name, definition in template_thresholds.items():
        if column_name not in template_columns:
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE templates ADD COLUMN {column_name} {definition}"))
    criterion_columns = {column["name"] for column in inspector.get_columns("criteria")}
    if "is_critical" not in criterion_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE criteria ADD COLUMN is_critical BOOLEAN NOT NULL DEFAULT FALSE"))
    score_columns = {column["name"] for column in inspector.get_columns("scores")}
    if "evaluator_note" not in score_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE scores ADD COLUMN evaluator_note TEXT NOT NULL DEFAULT ''"))
    candidate_columns = {column["name"] for column in inspector.get_columns("candidates")}
    if "evaluator_user_id" not in candidate_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE candidates ADD COLUMN evaluator_user_id INTEGER REFERENCES users(id)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_candidates_evaluator_user_id ON candidates(evaluator_user_id)"))
    if "final_decision" not in candidate_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE candidates ADD COLUMN final_decision VARCHAR(24) NOT NULL DEFAULT ''"))
    if "ai_bonus_score" not in candidate_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE candidates ADD COLUMN ai_bonus_score DOUBLE PRECISION NOT NULL DEFAULT 0"))
    if "ai_bonus_rationale" not in candidate_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE candidates ADD COLUMN ai_bonus_rationale TEXT NOT NULL DEFAULT ''"))
    candidate_application_columns = {
        "applicant_email": "VARCHAR(160) NOT NULL DEFAULT ''",
        "applicant_phone": "VARCHAR(80) NOT NULL DEFAULT ''",
        "applicant_employee_code": "VARCHAR(80) NOT NULL DEFAULT ''",
        "application_source": "VARCHAR(40) NOT NULL DEFAULT 'manual'",
    }
    for column_name, definition in candidate_application_columns.items():
        if column_name not in candidate_columns:
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE candidates ADD COLUMN {column_name} {definition}"))
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    must_seed_temporary_passwords = "must_change_password" not in user_columns
    user_column_defaults = {
        "must_change_password": "BOOLEAN NOT NULL DEFAULT TRUE",
        "first_name": "VARCHAR(80) NOT NULL DEFAULT ''",
        "last_name": "VARCHAR(80) NOT NULL DEFAULT ''",
        "position": "VARCHAR(140) NOT NULL DEFAULT ''",
        "area": "VARCHAR(160) NOT NULL DEFAULT ''",
        "employee_code": "VARCHAR(60) NOT NULL DEFAULT ''",
        "role": "user_role NOT NULL DEFAULT 'evaluator'",
        "monthly_ai_quota_usd": "DOUBLE PRECISION NOT NULL DEFAULT 5",
        "terms_accepted_at": "TIMESTAMP NULL",
        "terms_version": "VARCHAR(40) NOT NULL DEFAULT ''",
    }
    for column_name, definition in user_column_defaults.items():
        if column_name not in user_columns:
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {definition}"))
    with engine.begin() as connection:
        connection.execute(text("UPDATE users SET role = 'administrator' WHERE is_admin = TRUE"))
        connection.execute(text("UPDATE users SET role = 'viewer' WHERE is_admin = FALSE AND can_view_all = TRUE AND role = 'evaluator'"))
        connection.execute(text("UPDATE users SET monthly_ai_quota_usd = 0 WHERE is_admin = TRUE OR role = 'administrator'"))
        connection.execute(text("UPDATE users SET must_change_password = FALSE WHERE username = :admin_username"), {"admin_username": settings.admin_username})
        if must_seed_temporary_passwords:
            connection.execute(
                text("UPDATE users SET password_hash = :password_hash, must_change_password = TRUE WHERE username <> :admin_username"),
                {"password_hash": hash_password(TEMPORARY_PASSWORD), "admin_username": settings.admin_username},
            )
    if inspector.has_table("ai_interaction_logs"):
        ai_log_columns = {column["name"] for column in inspector.get_columns("ai_interaction_logs")}
        ai_log_column_defaults = {
            "cached_content_name": "VARCHAR(220) NOT NULL DEFAULT ''",
            "cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "billable_input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "output_text_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cache_cost_usd": "DOUBLE PRECISION NOT NULL DEFAULT 0",
            "thinking_cost_usd": "DOUBLE PRECISION NOT NULL DEFAULT 0",
            "pricing_cache_usd_per_1m": "DOUBLE PRECISION NOT NULL DEFAULT 0",
        }
        for column_name, definition in ai_log_column_defaults.items():
            if column_name not in ai_log_columns:
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE ai_interaction_logs ADD COLUMN {column_name} {definition}"))
        with engine.begin() as connection:
            connection.execute(text("UPDATE ai_interaction_logs SET billable_input_tokens = input_tokens WHERE billable_input_tokens = 0 AND cached_input_tokens = 0"))
            connection.execute(text("UPDATE ai_interaction_logs SET output_text_tokens = GREATEST(output_tokens - thinking_tokens, 0) WHERE output_text_tokens = 0"))
            connection.execute(
                text(
                    "UPDATE ai_interaction_logs "
                    "SET thinking_cost_usd = thinking_tokens * pricing_output_usd_per_1m / 1000000 "
                    "WHERE thinking_cost_usd = 0 AND thinking_tokens > 0"
                )
            )


def get_template_or_404(db: Session, template_id: int) -> Template:
    template = (
        db.query(Template)
        .options(selectinload(Template.criteria), selectinload(Template.categories))
        .filter(Template.id == template_id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    return template


def get_candidate_or_404(db: Session, candidate_id: int) -> Candidate:
    candidate = (
        db.query(Candidate)
        .options(selectinload(Candidate.files), selectinload(Candidate.scores).selectinload(Score.file_references))
        .filter(Candidate.id == candidate_id)
        .first()
    )
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidato no encontrado")
    return candidate


def get_candidate_file_or_404(db: Session, candidate_id: int, file_id: int) -> CandidateFile:
    candidate_file = (
        db.query(CandidateFile)
        .filter(CandidateFile.id == file_id, CandidateFile.candidate_id == candidate_id)
        .first()
    )
    if not candidate_file:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return candidate_file


def delete_uploaded_files(candidate_files: list[CandidateFile]):
    for candidate_file in candidate_files:
        file_path = Path(settings.upload_dir) / candidate_file.stored_name
        file_path.unlink(missing_ok=True)


def save_candidate_uploads(db: Session, candidate: Candidate, files: list[UploadFile] | None) -> list[CandidateFile]:
    created_files = []
    upload_path = Path(settings.upload_dir)
    upload_path.mkdir(parents=True, exist_ok=True)
    for uploaded in files or []:
        if not uploaded.filename:
            continue
        mime_type = uploaded.content_type or "application/octet-stream"
        if mime_type not in ALLOWED_APPLICATION_FILE_TYPES:
            raise HTTPException(status_code=400, detail=f"Tipo no soportado: {mime_type}")
        suffix = Path(uploaded.filename or "").suffix.lower()
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        destination = upload_path / stored_name
        with destination.open("wb") as buffer:
            shutil.copyfileobj(uploaded.file, buffer)
        candidate_file = CandidateFile(
            candidate_id=candidate.id,
            original_name=uploaded.filename or stored_name,
            stored_name=stored_name,
            mime_type=mime_type,
            size_bytes=destination.stat().st_size,
        )
        created_files.append(candidate_file)
        db.add(candidate_file)
    return created_files


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else default


def set_setting(db: Session, key: str, value: str):
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def get_ai_config(db: Session) -> tuple[str | None, str]:
    api_key = get_setting(db, "gemini_api_key", settings.gemini_api_key or "")
    model = get_setting(db, "gemini_model", settings.gemini_model)
    return api_key or None, model or settings.gemini_model


def month_start() -> datetime:
    now = datetime.utcnow()
    return datetime(now.year, now.month, 1)


def monthly_ai_usage_usd(db: Session, user: User) -> float:
    if not user.id:
        return 0.0
    return float(
        db.query(AIInteractionLog)
        .filter(AIInteractionLog.user_id == user.id, AIInteractionLog.created_at >= month_start())
        .with_entities(func.coalesce(func.sum(AIInteractionLog.total_cost_usd), 0))
        .scalar()
        or 0
    )


def enforce_ai_quota(db: Session, user: User):
    if user.is_admin or user.role == UserRole.administrator:
        return
    quota = float(user.monthly_ai_quota_usd or 0)
    used = monthly_ai_usage_usd(db, user)
    if used >= quota:
        raise HTTPException(
            status_code=403,
            detail=f"El usuario alcanzó su cuota mensual de IA: {used:.4f} USD de {quota:.2f} USD.",
        )


def get_or_create_candidate_cache(db: Session, candidate: Candidate, api_key: str | None, model: str) -> str:
    signature = candidate_document_signature(candidate)
    now = datetime.utcnow()
    current = (
        db.query(AICandidateCache)
        .filter(
            AICandidateCache.candidate_id == candidate.id,
            AICandidateCache.model == model,
            AICandidateCache.document_signature == signature,
            AICandidateCache.expires_at > now,
        )
        .order_by(AICandidateCache.expires_at.desc())
        .first()
    )
    if current:
        return current.cache_name
    try:
        cache_name = create_candidate_context_cache(candidate, settings.upload_dir, api_key, model)
    except Exception:
        return ""
    expires_at = now + timedelta(hours=1)
    db.add(
        AICandidateCache(
            candidate_id=candidate.id,
            model=model,
            document_signature=signature,
            cache_name=cache_name,
            expires_at=expires_at,
        )
    )
    db.flush()
    return cache_name


def record_ai_interaction(
    db: Session,
    action: str,
    model: str,
    result,
    user: User | None = None,
    candidate_id: int | None = None,
    template_id: int | None = None,
):
    db.add(
        AIInteractionLog(
            action=action,
            model=model,
            status="success",
            cached_content_name=result.cached_content_name,
            prompt_text=result.prompt_text,
            response_text=result.response_text,
            input_tokens=result.input_tokens,
            cached_input_tokens=result.cached_input_tokens,
            billable_input_tokens=result.billable_input_tokens,
            output_text_tokens=result.output_text_tokens,
            output_tokens=result.output_tokens,
            thinking_tokens=result.thinking_tokens,
            total_tokens=result.total_tokens,
            input_cost_usd=result.input_cost_usd,
            cache_cost_usd=result.cache_cost_usd,
            output_cost_usd=result.output_cost_usd,
            thinking_cost_usd=result.thinking_cost_usd,
            total_cost_usd=result.total_cost_usd,
            pricing_input_usd_per_1m=result.pricing_input_usd_per_1m,
            pricing_cache_usd_per_1m=result.pricing_cache_usd_per_1m,
            pricing_output_usd_per_1m=result.pricing_output_usd_per_1m,
            pricing_source=result.pricing_source,
            pricing_reference_date=result.pricing_reference_date,
            user_id=user.id if user else None,
            candidate_id=candidate_id,
            template_id=template_id,
        )
    )


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/auth/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username.strip()).first()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Usuario o contraseña inválidos.")
    token = secrets.token_urlsafe(42)
    db.add(AuthSession(token=token, user_id=user.id))
    db.commit()
    return {"token": token, "user": user}


@app.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@app.post("/auth/change-password", response_model=UserOut)
def change_password(payload: PasswordChangeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    new_password = payload.new_password
    if not new_password:
        raise HTTPException(status_code=400, detail="Escribe una nueva contraseña.")
    if new_password == TEMPORARY_PASSWORD:
        raise HTTPException(status_code=400, detail="La nueva contraseña debe ser diferente a la temporal.")
    current = db.query(User).filter(User.id == user.id).first()
    if not current:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    current.password_hash = hash_password(new_password)
    current.must_change_password = False
    db.commit()
    db.refresh(current)
    return current


@app.post("/auth/accept-terms", response_model=UserOut)
def accept_terms(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    current = db.query(User).filter(User.id == user.id).first()
    if not current:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if current.must_change_password:
        raise HTTPException(status_code=400, detail="Primero debes cambiar tu contraseña temporal.")
    current.terms_accepted_at = datetime.utcnow()
    current.terms_version = TERMS_VERSION
    db.commit()
    db.refresh(current)
    return current


@app.post("/auth/logout")
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        session = db.query(AuthSession).filter(AuthSession.token == token).first()
        if session:
            db.delete(session)
            db.commit()
    return {"ok": True}


@app.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.query(User).order_by(User.username).all()


@app.post("/users", response_model=UserOut)
def create_user(payload: UserCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Usuario requerido.")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Ya existe un usuario con ese nombre.")
    user = User(
        username=username,
        password_hash=hash_password(TEMPORARY_PASSWORD),
        must_change_password=True,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        position=payload.position.strip(),
        area=payload.area.strip(),
        employee_code=payload.employee_code.strip(),
        role=payload.role,
        is_admin=payload.role == UserRole.administrator,
        can_view_all="view_results" in ROLE_PERMISSIONS.get(payload.role, set()),
        monthly_ai_quota_usd=0 if payload.role == UserRole.administrator else payload.monthly_ai_quota_usd,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.patch("/users/{user_id}/ai-quota", response_model=UserOut)
def update_user_ai_quota(user_id: int, payload: UserQuotaPatch, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if user.is_admin or user.role == UserRole.administrator:
        user.monthly_ai_quota_usd = 0
    else:
        user.monthly_ai_quota_usd = payload.monthly_ai_quota_usd
    db.commit()
    db.refresh(user)
    return user


@app.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Usuario requerido.")
    if db.query(User).filter(User.username == username, User.id != user_id).first():
        raise HTTPException(status_code=400, detail="Ya existe un usuario con ese nombre.")
    will_be_admin = payload.role == UserRole.administrator
    will_be_active = payload.is_active
    if user_id == current_user.id and not will_be_active:
        raise HTTPException(status_code=400, detail="No puedes desactivar tu propio usuario activo.")
    if user.is_admin and (not will_be_admin or not will_be_active):
        other_active_admins = db.query(User).filter(User.is_admin == True, User.is_active == True, User.id != user_id).count()
        if other_active_admins == 0:
            raise HTTPException(status_code=400, detail="Debe quedar al menos un administrador activo.")
    user.username = username
    user.first_name = payload.first_name.strip()
    user.last_name = payload.last_name.strip()
    user.position = payload.position.strip()
    user.area = payload.area.strip()
    user.employee_code = payload.employee_code.strip()
    user.role = payload.role
    user.is_admin = will_be_admin
    user.can_view_all = "view_results" in ROLE_PERMISSIONS.get(payload.role, set())
    user.monthly_ai_quota_usd = 0 if will_be_admin else payload.monthly_ai_quota_usd
    user.is_active = will_be_active
    db.commit()
    db.refresh(user)
    return user


@app.post("/users/{user_id}/reset-password", response_model=UserOut)
def reset_user_password(user_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    user.password_hash = hash_password(TEMPORARY_PASSWORD)
    user.must_change_password = True
    db.commit()
    db.refresh(user)
    return user


@app.get("/public/positions", response_model=list[PublicPositionOut])
def list_public_positions(db: Session = Depends(get_db)):
    return (
        db.query(Template)
        .filter(Template.is_archived.is_(False), Template.position_status == "open")
        .order_by(Template.name)
        .all()
    )


@app.post("/public/applications", response_model=CandidateOut)
def submit_public_application(
    template_id: int = Form(...),
    name: str = Form(...),
    document_id: str = Form(default=""),
    applicant_email: str = Form(...),
    applicant_phone: str = Form(default=""),
    applicant_employee_code: str = Form(default=""),
    comments: str = Form(default=""),
    files: list[UploadFile] | None = File(default=None),
    db: Session = Depends(get_db),
):
    template = get_template_or_404(db, template_id)
    if template.is_archived or template.position_status != "open":
        raise HTTPException(status_code=400, detail="Esta posición no está abierta para postulaciones.")
    clean_name = name.strip()
    clean_email = applicant_email.strip().lower()
    clean_employee_code = applicant_employee_code.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio.")
    if not clean_email:
        raise HTTPException(status_code=400, detail="El correo electrónico es obligatorio.")
    if template.competition_scope == "internal":
        if not clean_email.endswith("@sie.gov.do"):
            raise HTTPException(status_code=400, detail="Este concurso interno requiere un correo institucional @sie.gov.do.")
        if not clean_employee_code:
            raise HTTPException(status_code=400, detail="Este concurso interno requiere el código de empleado.")
    candidate = Candidate(
        template_id=template.id,
        name=clean_name,
        document_id=document_id.strip(),
        applicant_email=clean_email,
        applicant_phone=applicant_phone.strip(),
        applicant_employee_code=clean_employee_code,
        application_source="public_internal" if template.competition_scope == "internal" else "public_external",
        evaluator=PUBLIC_EVALUATOR_NAME,
        comments=comments.strip(),
    )
    db.add(candidate)
    db.flush()
    created_files = []
    try:
        created_files = save_candidate_uploads(db, candidate, files)
        db.commit()
    except Exception:
        db.rollback()
        delete_uploaded_files(created_files)
        raise
    return get_candidate_or_404(db, candidate.id)


@app.delete("/users/{user_id}")
def delete_user(user_id: int, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="No puedes eliminar tu propio usuario activo.")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if user.is_admin and db.query(User).filter(User.is_admin == True, User.is_active == True, User.id != user_id).count() == 0:
        raise HTTPException(status_code=400, detail="Debe quedar al menos un administrador activo.")
    db.delete(user)
    db.commit()
    return {"ok": True}


@app.get("/settings/ai", response_model=AISettingsOut)
def read_ai_settings(_: User = Depends(require_permission("view_results")), db: Session = Depends(get_db)):
    api_key, model = get_ai_config(db)
    return {
        "gemini_api_key_configured": bool(api_key),
        "gemini_api_key_masked": mask_key(api_key or ""),
        "gemini_model": model,
    }


@app.put("/settings/ai", response_model=AISettingsOut)
def save_ai_settings(payload: AISettingsIn, _: User = Depends(require_permission("manage_ai_settings")), db: Session = Depends(get_db)):
    model = payload.gemini_model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="Selecciona un modelo de Gemini.")
    set_setting(db, "gemini_model", model)
    if payload.gemini_api_key is not None and payload.gemini_api_key.strip():
        set_setting(db, "gemini_api_key", payload.gemini_api_key.strip())
    db.commit()
    api_key, saved_model = get_ai_config(db)
    return {
        "gemini_api_key_configured": bool(api_key),
        "gemini_api_key_masked": mask_key(api_key or ""),
        "gemini_model": saved_model,
    }


@app.get("/settings/ai/models", response_model=list[str])
def list_ai_models(_: User = Depends(require_permission("view_results"))):
    return AI_MODEL_OPTIONS


@app.get("/settings/ai/logs", response_model=AIInteractionLogsOut)
def list_ai_logs(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=80, ge=1, le=300),
):
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Debes cambiar tu contraseña temporal antes de continuar.")
    query = db.query(AIInteractionLog)
    if not (user.is_admin or user.role == UserRole.administrator):
        query = query.filter(AIInteractionLog.user_id == user.id)
    logs = query.order_by(AIInteractionLog.created_at.desc(), AIInteractionLog.id.desc()).limit(limit).all()
    all_logs = query.all()
    summary = {
        "total_interactions": len(all_logs),
        "total_input_tokens": sum(row.input_tokens or 0 for row in all_logs),
        "total_cached_input_tokens": sum(row.cached_input_tokens or 0 for row in all_logs),
        "total_billable_input_tokens": sum(row.billable_input_tokens or 0 for row in all_logs),
        "total_output_text_tokens": sum(row.output_text_tokens or 0 for row in all_logs),
        "total_output_tokens": sum(row.output_tokens or 0 for row in all_logs),
        "total_thinking_tokens": sum(row.thinking_tokens or 0 for row in all_logs),
        "total_tokens": sum(row.total_tokens or 0 for row in all_logs),
        "total_input_cost_usd": sum(row.input_cost_usd or 0 for row in all_logs),
        "total_cache_cost_usd": sum(row.cache_cost_usd or 0 for row in all_logs),
        "total_output_cost_usd": sum(row.output_cost_usd or 0 for row in all_logs),
        "total_thinking_cost_usd": sum(row.thinking_cost_usd or 0 for row in all_logs),
        "total_cost_usd": sum(row.total_cost_usd or 0 for row in all_logs),
    }
    return {"summary": summary, "logs": logs}


@app.get("/templates", response_model=list[TemplateOut])
def list_templates(_: User = Depends(require_permission("view_results")), db: Session = Depends(get_db)):
    return (
        db.query(Template)
        .options(selectinload(Template.criteria), selectinload(Template.categories))
        .filter(Template.is_archived.is_(False))
        .order_by(Template.id)
        .all()
    )


@app.post("/templates", response_model=TemplateOut)
def create_template(payload: TemplateCreate, _: User = Depends(require_permission("manage_templates")), db: Session = Depends(get_db)):
    categories, criteria = normalized_template_parts(payload)
    validate_template_structure(categories, criteria)
    validate_recommendation_scale(payload)
    position_status, competition_scope = clean_template_publication_fields(payload)
    template = Template(
        name=payload.name,
        description=payload.description,
        ai_evaluation_locked=payload.ai_evaluation_locked,
        position_status=position_status,
        competition_scope=competition_scope,
        highly_recommended_threshold=payload.highly_recommended_threshold,
        recommended_threshold=payload.recommended_threshold,
        review_threshold=payload.review_threshold,
    )
    db.add(template)
    db.flush()
    for category in categories:
        db.add(TemplateCategory(template_id=template.id, **category))
    for criterion in criteria:
        criterion.pop("id", None)
        db.add(Criterion(template_id=template.id, **criterion))
    db.commit()
    return get_template_or_404(db, template.id)


@app.post("/templates/generate-ai", response_model=TemplateCreate)
async def generate_template_ai(
    requirements_text: str = Form(default=""),
    requirements_file: UploadFile | None = File(default=None),
    user: User = Depends(require_permission("manage_templates")),
    db: Session = Depends(get_db),
):
    file_bytes = None
    file_name = None
    file_mime_type = None
    if requirements_file and requirements_file.filename:
        file_mime_type = requirements_file.content_type or "application/octet-stream"
        if file_mime_type != "application/pdf":
            raise HTTPException(status_code=400, detail="Por ahora la generación de plantilla acepta requisitos en PDF.")
        file_bytes = await requirements_file.read()
        file_name = requirements_file.filename
    try:
        enforce_ai_quota(db, user)
        api_key, model = get_ai_config(db)
        result = generate_template_with_gemini(requirements_text, file_name, file_bytes, file_mime_type, api_key, model)
        record_ai_interaction(db, "generate_template", model, result, user=user)
        db.commit()
        return clean_generated_template(result.payload)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/templates/{template_id}", response_model=TemplateOut)
def replace_template(template_id: int, payload: TemplateCreate, _: User = Depends(require_permission("manage_templates")), db: Session = Depends(get_db)):
    template = get_template_or_404(db, template_id)
    categories, criteria = normalized_template_parts(payload)
    validate_template_structure(categories, criteria)
    validate_recommendation_scale(payload)
    position_status, competition_scope = clean_template_publication_fields(payload)
    template.name = payload.name
    template.description = payload.description
    template.ai_evaluation_locked = payload.ai_evaluation_locked
    template.position_status = position_status
    template.competition_scope = competition_scope
    template.highly_recommended_threshold = payload.highly_recommended_threshold
    template.recommended_threshold = payload.recommended_threshold
    template.review_threshold = payload.review_threshold
    template.categories.clear()
    db.flush()
    for category in categories:
        template.categories.append(TemplateCategory(**category))
    existing_criteria = {criterion.id: criterion for criterion in template.criteria}
    kept_criterion_ids: set[int] = set()
    for criterion in criteria:
        criterion_id = criterion.pop("id", None)
        if criterion_id and criterion_id in existing_criteria:
            current = existing_criteria[criterion_id]
            was_automatic = current.evaluation_mode == EvaluationMode.automatic
            changed = criterion_requires_score_reset(current, criterion)
            for key, value in criterion.items():
                setattr(current, key, value)
            kept_criterion_ids.add(current.id)
            if changed and (was_automatic or current.evaluation_mode == EvaluationMode.automatic):
                delete_scores_for_criterion(db, current.id)
        else:
            template.criteria.append(Criterion(**criterion))
    for criterion in list(template.criteria):
        if criterion.id and criterion.id not in kept_criterion_ids and criterion.id in existing_criteria:
            db.delete(criterion)
    db.commit()
    return get_template_or_404(db, template_id)


@app.delete("/templates/{template_id}")
def delete_template(template_id: int, _: User = Depends(require_permission("manage_templates")), db: Session = Depends(get_db)):
    template = get_template_or_404(db, template_id)
    if template.candidates:
        template.is_archived = True
        db.commit()
        return {"status": "archived", "message": "La plantilla tiene candidatos asociados y fue archivada."}
    db.delete(template)
    db.commit()
    return {"status": "deleted", "message": "Plantilla eliminada."}


@app.patch("/criteria/{criterion_id}", response_model=TemplateOut)
def update_criterion(criterion_id: int, payload: CriterionIn, _: User = Depends(require_permission("manage_templates")), db: Session = Depends(get_db)):
    criterion = db.query(Criterion).filter(Criterion.id == criterion_id).first()
    if not criterion:
        raise HTTPException(status_code=404, detail="Criterio no encontrado")
    row = payload.model_dump(exclude={"id"})
    row["code"] = row["code"].strip() or criterion.code
    row["category"] = row["category"].strip()
    row["aspect"] = row["aspect"].strip()
    row["category_weight"] = max(0.0, float(row["category_weight"] or 0))
    row["within_category_weight"] = 0.0 if row.get("is_critical") else max(0.0, float(row["within_category_weight"] or 0))
    row["global_weight"] = row["category_weight"] * row["within_category_weight"]
    was_automatic = criterion.evaluation_mode == EvaluationMode.automatic
    changed = criterion_requires_score_reset(criterion, row)
    for key, value in row.items():
        setattr(criterion, key, value)
    if changed and (was_automatic or criterion.evaluation_mode == EvaluationMode.automatic):
        delete_scores_for_criterion(db, criterion.id)
    db.commit()
    return get_template_or_404(db, criterion.template_id)


@app.get("/candidates", response_model=list[CandidateOut])
def list_candidates(_: User = Depends(require_permission("view_results")), db: Session = Depends(get_db)):
    return (
        db.query(Candidate)
        .options(selectinload(Candidate.files), selectinload(Candidate.scores).selectinload(Score.file_references))
        .order_by(Candidate.created_at.desc(), Candidate.id.desc())
        .all()
    )


@app.post("/candidates", response_model=CandidateOut)
def create_candidate(payload: CandidateCreate, user: User = Depends(require_permission("manage_candidates")), db: Session = Depends(get_db)):
    template = get_template_or_404(db, payload.template_id)
    if template.is_archived:
        raise HTTPException(status_code=400, detail="No se pueden crear candidatos en un perfil archivado.")
    candidate = Candidate(**payload.model_dump(), evaluator=user_display_name(user), evaluator_user_id=user.id)
    db.add(candidate)
    db.commit()
    return get_candidate_or_404(db, candidate.id)


@app.patch("/candidates/{candidate_id}", response_model=CandidateOut)
def update_candidate(candidate_id: int, payload: CandidatePatch, _: User = Depends(require_permission("manage_candidates")), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    row = payload.model_dump(exclude_unset=True)
    if "final_decision" in row and row["final_decision"] not in {"", "qualifies", "not_qualifies"}:
        raise HTTPException(status_code=400, detail="Decisión final inválida.")
    for key, value in row.items():
        setattr(candidate, key, value)
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.delete("/candidates/{candidate_id}")
def delete_candidate(candidate_id: int, _: User = Depends(require_permission("manage_candidates")), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    delete_uploaded_files(candidate.files)
    db.delete(candidate)
    db.commit()
    return {"ok": True}


@app.post("/candidates/{candidate_id}/reset", response_model=CandidateOut)
def reset_candidate_evaluation(candidate_id: int, _: User = Depends(require_permission("evaluate_candidates")), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    delete_uploaded_files(candidate.files)
    candidate.files.clear()
    candidate.scores.clear()
    candidate.final_decision = ""
    candidate.ai_bonus_score = 0
    candidate.ai_bonus_rationale = ""
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.post("/candidates/{candidate_id}/files", response_model=CandidateOut)
def upload_candidate_files(candidate_id: int, files: list[UploadFile] = File(...), _: User = Depends(require_permission("evaluate_candidates")), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    save_candidate_uploads(db, candidate, files)
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.delete("/candidates/{candidate_id}/files/{file_id}", response_model=CandidateOut)
def delete_candidate_file(candidate_id: int, file_id: int, _: User = Depends(require_permission("evaluate_candidates")), db: Session = Depends(get_db)):
    candidate_file = get_candidate_file_or_404(db, candidate_id, file_id)
    delete_uploaded_files([candidate_file])
    db.delete(candidate_file)
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.get("/candidates/{candidate_id}/files/{file_id}/view")
def view_candidate_file(candidate_id: int, file_id: int, token: str = Query(...), db: Session = Depends(get_db)):
    authenticate_token(db, token)
    candidate_file = get_candidate_file_or_404(db, candidate_id, file_id)
    file_path = Path(settings.upload_dir) / candidate_file.stored_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(
        file_path,
        media_type=candidate_file.mime_type,
        filename=candidate_file.original_name,
        content_disposition_type="inline",
    )


@app.get("/candidates/{candidate_id}/report")
def candidate_report(candidate_id: int, _: User = Depends(require_permission("view_results")), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    template = get_template_or_404(db, candidate.template_id)
    criteria = (
        db.query(Criterion)
        .filter(Criterion.template_id == candidate.template_id)
        .order_by(Criterion.order_index)
        .all()
    )
    report = build_candidate_report(candidate, template, criteria)
    filename = f"reporte_evaluacion_{safe_filename(candidate.name)}.docx"
    return StreamingResponse(
        report,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/candidates/{candidate_id}/scores", response_model=list[ScoreOut])
def save_scores(candidate_id: int, payload: list[ScoreIn], _: User = Depends(require_permission("evaluate_candidates")), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    template = get_template_or_404(db, candidate.template_id)
    if payload:
        candidate.ai_bonus_score = 0
        candidate.ai_bonus_rationale = ""
    valid_file_ids = {candidate_file.id for candidate_file in candidate.files}
    for item in payload:
        criterion = db.query(Criterion).filter(Criterion.id == item.criterion_id).first()
        if not criterion or criterion.template_id != candidate.template_id:
            raise HTTPException(status_code=400, detail="Criterio inválido para este candidato.")
        if template.ai_evaluation_locked and criterion.evaluation_mode == EvaluationMode.automatic:
            current = (
                db.query(Score)
                .filter(Score.candidate_id == candidate_id, Score.criterion_id == item.criterion_id)
                .first()
            )
            if current:
                current.evaluator_note = item.evaluator_note
            elif item.evaluator_note.strip():
                upsert_score(
                    db,
                    candidate_id,
                    item.criterion_id,
                    item.score,
                    "automatic",
                    "",
                    [],
                    evaluator_note=item.evaluator_note,
                )
            continue
        file_ids = clean_file_ids(item.file_ids, valid_file_ids)
        source = "automatic" if criterion.evaluation_mode == EvaluationMode.automatic else "manual"
        upsert_score(db, candidate_id, item.criterion_id, item.score, source, item.rationale, file_ids, item.evaluator_note)
    db.commit()
    return [serialize_score(score) for score in get_candidate_or_404(db, candidate_id).scores]


@app.post("/candidates/{candidate_id}/evaluate-ai", response_model=CandidateOut)
def evaluate_ai(candidate_id: int, user: User = Depends(require_permission("evaluate_candidates")), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    template = get_template_or_404(db, candidate.template_id)
    automatic_criteria = [c for c in template.criteria if c.evaluation_mode == EvaluationMode.automatic]
    if not automatic_criteria:
        raise HTTPException(status_code=400, detail="La plantilla no tiene criterios automáticos.")
    try:
        enforce_ai_quota(db, user)
        api_key, model = get_ai_config(db)
        cached_content_name = get_or_create_candidate_cache(db, candidate, api_key, model)
        evaluation_result = evaluate_candidate_with_gemini(candidate, automatic_criteria, settings.upload_dir, api_key, model, cached_content_name)
        record_ai_interaction(db, "evaluate_candidate", model, evaluation_result, user=user, candidate_id=candidate.id, template_id=template.id)
        results = evaluation_result.payload.get("scores", [])
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    valid_ids = {criterion.id for criterion in automatic_criteria}
    valid_file_ids = {candidate_file.id for candidate_file in candidate.files}
    for item in results:
        criterion_id = int(item.get("criterion_id", 0))
        if criterion_id not in valid_ids:
            continue
        score = float(item.get("score", 0))
        score = max(0.0, min(score, 5.0))
        file_ids = clean_file_ids(item.get("file_ids", []), valid_file_ids)
        upsert_score(db, candidate.id, criterion_id, score, "automatic", str(item.get("rationale", "")), file_ids)
    db.flush()
    refreshed_candidate = get_candidate_or_404(db, candidate_id)
    try:
        enforce_ai_quota(db, user)
        cached_content_name = get_or_create_candidate_cache(db, refreshed_candidate, api_key, model)
        bonus_result = evaluate_candidate_bonus_with_gemini(
            refreshed_candidate,
            list(template.criteria),
            evaluation_snapshot(refreshed_candidate, list(template.criteria)),
            settings.upload_dir,
            api_key,
            model,
            cached_content_name,
        )
        record_ai_interaction(db, "evaluate_bonus", model, bonus_result, user=user, candidate_id=candidate.id, template_id=template.id)
        bonus = bonus_result.payload
        refreshed_candidate.ai_bonus_score = max(0.0, min(float(bonus.get("bonus_score", 0) or 0), 5.0))
        refreshed_candidate.ai_bonus_rationale = str(bonus.get("rationale", "") or "")
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"No se pudo calcular la bonificación adicional: {exc}") from exc
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.post("/candidates/{candidate_id}/criteria/{criterion_id}/evaluate-ai", response_model=CandidateOut)
def evaluate_single_ai_criterion(candidate_id: int, criterion_id: int, user: User = Depends(require_permission("evaluate_candidates")), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    template = get_template_or_404(db, candidate.template_id)
    criterion = db.query(Criterion).filter(Criterion.id == criterion_id).first()
    if not criterion or criterion.template_id != candidate.template_id:
        raise HTTPException(status_code=400, detail="Criterio inválido para este candidato.")
    if criterion.evaluation_mode != EvaluationMode.automatic:
        raise HTTPException(status_code=400, detail="Este criterio no está configurado para evaluación con IA.")
    automatic_criteria = [c for c in template.criteria if c.evaluation_mode == EvaluationMode.automatic]
    try:
        enforce_ai_quota(db, user)
        api_key, model = get_ai_config(db)
        cached_content_name = get_or_create_candidate_cache(db, candidate, api_key, model)
        evaluation_result = evaluate_candidate_with_gemini(candidate, automatic_criteria, settings.upload_dir, api_key, model, cached_content_name)
        record_ai_interaction(db, "reevaluate_criterion", model, evaluation_result, user=user, candidate_id=candidate.id, template_id=template.id)
        results = evaluation_result.payload.get("scores", [])
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    valid_file_ids = {candidate_file.id for candidate_file in candidate.files}
    for item in results:
        if int(item.get("criterion_id", 0)) != criterion.id:
            continue
        score = max(0.0, min(float(item.get("score", 0)), 5.0))
        file_ids = clean_file_ids(item.get("file_ids", []), valid_file_ids)
        upsert_score(db, candidate.id, criterion.id, score, "automatic", str(item.get("rationale", "")), file_ids)
        candidate.ai_bonus_score = 0
        candidate.ai_bonus_rationale = ""
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.get("/summary", response_model=SummaryOut)
def summary(template_id: int | None = None, user: User = Depends(require_permission("view_results")), db: Session = Depends(get_db)):
    query = db.query(Candidate).options(selectinload(Candidate.scores))
    if template_id:
        query = query.filter(Candidate.template_id == template_id)
    candidates = query.order_by(Candidate.name).all()
    if not candidates:
        return {"candidates": [], "categories": []}

    criteria_by_template: dict[int, list[Criterion]] = {}
    templates_by_id: dict[int, Template | None] = {}
    for candidate in candidates:
        if candidate.template_id not in criteria_by_template:
            templates_by_id[candidate.template_id] = db.query(Template).filter(Template.id == candidate.template_id).first()
            criteria_by_template[candidate.template_id] = (
                db.query(Criterion)
                .filter(Criterion.template_id == candidate.template_id)
                .order_by(Criterion.order_index)
                .all()
            )

    rows = [
        summarize_candidate(candidate, criteria_by_template[candidate.template_id], templates_by_id.get(candidate.template_id))
        for candidate in candidates
    ]
    categories = sorted({category for row in rows for category in row["categories"]})
    return {"candidates": rows, "categories": categories}
