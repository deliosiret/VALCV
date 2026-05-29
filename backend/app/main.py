import hashlib
import secrets
import shutil
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, selectinload

from app.ai import evaluate_candidate_with_gemini
from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.models import AppSetting, AuthSession, Candidate, CandidateFile, Criterion, EvaluationMode, Score, Template, TemplateCategory, User
from app.schemas import (
    AISettingsIn,
    AISettingsOut,
    CandidateCreate,
    CandidateOut,
    CandidatePatch,
    CriterionIn,
    LoginIn,
    ScoreIn,
    ScoreOut,
    SummaryOut,
    TemplateCreate,
    TemplateOut,
    TokenOut,
    UserCreate,
    UserOut,
)
from app.scoring import summarize_candidate, upsert_score
from app.seed import seed_initial_template

app = FastAPI(title="VALCV API", version="0.1.0")


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


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Solo un usuario administrador puede realizar esta acción.")
    return user


def seed_admin_user(db: Session):
    exists = db.query(User).filter(User.username == settings.admin_username).first()
    if exists:
        return
    db.add(
        User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
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
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]


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
    template_columns = {column["name"] for column in inspector.get_columns("templates")}
    if "ai_evaluation_locked" not in template_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE templates ADD COLUMN ai_evaluation_locked BOOLEAN NOT NULL DEFAULT TRUE"))
    criterion_columns = {column["name"] for column in inspector.get_columns("criteria")}
    if "is_critical" not in criterion_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE criteria ADD COLUMN is_critical BOOLEAN NOT NULL DEFAULT FALSE"))
    score_columns = {column["name"] for column in inspector.get_columns("scores")}
    if "evaluator_note" not in score_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE scores ADD COLUMN evaluator_note TEXT NOT NULL DEFAULT ''"))


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
    if not username or len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Usuario requerido y contraseña mínima de 6 caracteres.")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Ya existe un usuario con ese nombre.")
    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin,
        can_view_all=payload.can_view_all,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.get("/settings/ai", response_model=AISettingsOut)
def read_ai_settings(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    api_key, model = get_ai_config(db)
    return {
        "gemini_api_key_configured": bool(api_key),
        "gemini_api_key_masked": mask_key(api_key or ""),
        "gemini_model": model,
    }


@app.put("/settings/ai", response_model=AISettingsOut)
def save_ai_settings(payload: AISettingsIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
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
def list_ai_models(_: User = Depends(get_current_user)):
    return AI_MODEL_OPTIONS


@app.get("/templates", response_model=list[TemplateOut])
def list_templates(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Template).options(selectinload(Template.criteria), selectinload(Template.categories)).order_by(Template.id).all()


@app.post("/templates", response_model=TemplateOut)
def create_template(payload: TemplateCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    template = Template(name=payload.name, description=payload.description, ai_evaluation_locked=payload.ai_evaluation_locked)
    db.add(template)
    db.flush()
    categories, criteria = normalized_template_parts(payload)
    for category in categories:
        db.add(TemplateCategory(template_id=template.id, **category))
    for criterion in criteria:
        criterion.pop("id", None)
        db.add(Criterion(template_id=template.id, **criterion))
    db.commit()
    return get_template_or_404(db, template.id)


@app.put("/templates/{template_id}", response_model=TemplateOut)
def replace_template(template_id: int, payload: TemplateCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    template = get_template_or_404(db, template_id)
    template.name = payload.name
    template.description = payload.description
    template.ai_evaluation_locked = payload.ai_evaluation_locked
    template.categories.clear()
    db.flush()
    categories, criteria = normalized_template_parts(payload)
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


@app.patch("/criteria/{criterion_id}", response_model=TemplateOut)
def update_criterion(criterion_id: int, payload: CriterionIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
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
def list_candidates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.can_view_all:
        raise HTTPException(status_code=403, detail="Este usuario no puede ver resultados.")
    return (
        db.query(Candidate)
        .options(selectinload(Candidate.files), selectinload(Candidate.scores).selectinload(Score.file_references))
        .order_by(Candidate.created_at.desc(), Candidate.id.desc())
        .all()
    )


@app.post("/candidates", response_model=CandidateOut)
def create_candidate(payload: CandidateCreate, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_template_or_404(db, payload.template_id)
    candidate = Candidate(**payload.model_dump())
    db.add(candidate)
    db.commit()
    return get_candidate_or_404(db, candidate.id)


@app.patch("/candidates/{candidate_id}", response_model=CandidateOut)
def update_candidate(candidate_id: int, payload: CandidatePatch, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(candidate, key, value)
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.delete("/candidates/{candidate_id}")
def delete_candidate(candidate_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    delete_uploaded_files(candidate.files)
    db.delete(candidate)
    db.commit()
    return {"ok": True}


@app.post("/candidates/{candidate_id}/reset", response_model=CandidateOut)
def reset_candidate_evaluation(candidate_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    delete_uploaded_files(candidate.files)
    candidate.files.clear()
    candidate.scores.clear()
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.post("/candidates/{candidate_id}/files", response_model=CandidateOut)
def upload_candidate_files(candidate_id: int, files: list[UploadFile] = File(...), _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    allowed = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}
    upload_path = Path(settings.upload_dir)
    upload_path.mkdir(parents=True, exist_ok=True)

    for uploaded in files:
        mime_type = uploaded.content_type or "application/octet-stream"
        if mime_type not in allowed:
            raise HTTPException(status_code=400, detail=f"Tipo no soportado: {mime_type}")
        suffix = Path(uploaded.filename or "").suffix.lower()
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        destination = upload_path / stored_name
        with destination.open("wb") as buffer:
            shutil.copyfileobj(uploaded.file, buffer)
        db.add(
            CandidateFile(
                candidate_id=candidate.id,
                original_name=uploaded.filename or stored_name,
                stored_name=stored_name,
                mime_type=mime_type,
                size_bytes=destination.stat().st_size,
            )
        )
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.delete("/candidates/{candidate_id}/files/{file_id}", response_model=CandidateOut)
def delete_candidate_file(candidate_id: int, file_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
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


@app.post("/candidates/{candidate_id}/scores", response_model=list[ScoreOut])
def save_scores(candidate_id: int, payload: list[ScoreIn], _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    template = get_template_or_404(db, candidate.template_id)
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
def evaluate_ai(candidate_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    template = get_template_or_404(db, candidate.template_id)
    automatic_criteria = [c for c in template.criteria if c.evaluation_mode == EvaluationMode.automatic]
    if not automatic_criteria:
        raise HTTPException(status_code=400, detail="La plantilla no tiene criterios automáticos.")
    try:
        api_key, model = get_ai_config(db)
        results = evaluate_candidate_with_gemini(candidate, automatic_criteria, settings.upload_dir, api_key, model)
    except Exception as exc:
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
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.post("/candidates/{candidate_id}/criteria/{criterion_id}/evaluate-ai", response_model=CandidateOut)
def evaluate_single_ai_criterion(candidate_id: int, criterion_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    criterion = db.query(Criterion).filter(Criterion.id == criterion_id).first()
    if not criterion or criterion.template_id != candidate.template_id:
        raise HTTPException(status_code=400, detail="Criterio inválido para este candidato.")
    if criterion.evaluation_mode != EvaluationMode.automatic:
        raise HTTPException(status_code=400, detail="Este criterio no está configurado para evaluación con IA.")
    try:
        api_key, model = get_ai_config(db)
        results = evaluate_candidate_with_gemini(candidate, [criterion], settings.upload_dir, api_key, model)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    valid_file_ids = {candidate_file.id for candidate_file in candidate.files}
    for item in results:
        if int(item.get("criterion_id", 0)) != criterion.id:
            continue
        score = max(0.0, min(float(item.get("score", 0)), 5.0))
        file_ids = clean_file_ids(item.get("file_ids", []), valid_file_ids)
        upsert_score(db, candidate.id, criterion.id, score, "automatic", str(item.get("rationale", "")), file_ids)
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.get("/summary", response_model=SummaryOut)
def summary(template_id: int | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.can_view_all:
        raise HTTPException(status_code=403, detail="Este usuario no puede ver resultados.")
    query = db.query(Candidate).options(selectinload(Candidate.scores))
    if template_id:
        query = query.filter(Candidate.template_id == template_id)
    candidates = query.order_by(Candidate.name).all()
    if not candidates:
        return {"candidates": [], "categories": []}

    criteria_by_template: dict[int, list[Criterion]] = {}
    for candidate in candidates:
        if candidate.template_id not in criteria_by_template:
            criteria_by_template[candidate.template_id] = (
                db.query(Criterion)
                .filter(Criterion.template_id == candidate.template_id)
                .order_by(Criterion.order_index)
                .all()
            )

    rows = [summarize_candidate(candidate, criteria_by_template[candidate.template_id]) for candidate in candidates]
    categories = sorted({category for row in rows for category in row["categories"]})
    return {"candidates": rows, "categories": categories}
