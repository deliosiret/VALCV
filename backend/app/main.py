import shutil
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, selectinload

from app.ai import evaluate_candidate_with_gemini
from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.models import AppSetting, Candidate, CandidateFile, Criterion, EvaluationMode, Score, Template
from app.schemas import (
    AISettingsIn,
    AISettingsOut,
    CandidateCreate,
    CandidateOut,
    CandidatePatch,
    CriterionIn,
    ScoreIn,
    ScoreOut,
    SummaryOut,
    TemplateCreate,
    TemplateOut,
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
    with SessionLocal() as db:
        seed_initial_template(db)


def get_template_or_404(db: Session, template_id: int) -> Template:
    template = (
        db.query(Template)
        .options(selectinload(Template.criteria))
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


@app.get("/settings/ai", response_model=AISettingsOut)
def read_ai_settings(db: Session = Depends(get_db)):
    api_key, model = get_ai_config(db)
    return {
        "gemini_api_key_configured": bool(api_key),
        "gemini_api_key_masked": mask_key(api_key or ""),
        "gemini_model": model,
    }


@app.put("/settings/ai", response_model=AISettingsOut)
def save_ai_settings(payload: AISettingsIn, db: Session = Depends(get_db)):
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
def list_ai_models():
    return AI_MODEL_OPTIONS


@app.get("/templates", response_model=list[TemplateOut])
def list_templates(db: Session = Depends(get_db)):
    return db.query(Template).options(selectinload(Template.criteria)).order_by(Template.id).all()


@app.post("/templates", response_model=TemplateOut)
def create_template(payload: TemplateCreate, db: Session = Depends(get_db)):
    template = Template(name=payload.name, description=payload.description)
    db.add(template)
    db.flush()
    for idx, criterion in enumerate(payload.criteria):
        db.add(Criterion(template_id=template.id, order_index=idx, **criterion.model_dump(exclude={"order_index"})))
    db.commit()
    return get_template_or_404(db, template.id)


@app.put("/templates/{template_id}", response_model=TemplateOut)
def replace_template(template_id: int, payload: TemplateCreate, db: Session = Depends(get_db)):
    template = get_template_or_404(db, template_id)
    template.name = payload.name
    template.description = payload.description
    template.criteria.clear()
    db.flush()
    for idx, criterion in enumerate(payload.criteria):
        template.criteria.append(Criterion(order_index=idx, **criterion.model_dump(exclude={"order_index"})))
    db.commit()
    return get_template_or_404(db, template_id)


@app.patch("/criteria/{criterion_id}", response_model=TemplateOut)
def update_criterion(criterion_id: int, payload: CriterionIn, db: Session = Depends(get_db)):
    criterion = db.query(Criterion).filter(Criterion.id == criterion_id).first()
    if not criterion:
        raise HTTPException(status_code=404, detail="Criterio no encontrado")
    for key, value in payload.model_dump().items():
        setattr(criterion, key, value)
    db.commit()
    return get_template_or_404(db, criterion.template_id)


@app.get("/candidates", response_model=list[CandidateOut])
def list_candidates(db: Session = Depends(get_db)):
    return (
        db.query(Candidate)
        .options(selectinload(Candidate.files), selectinload(Candidate.scores).selectinload(Score.file_references))
        .order_by(Candidate.created_at.desc(), Candidate.id.desc())
        .all()
    )


@app.post("/candidates", response_model=CandidateOut)
def create_candidate(payload: CandidateCreate, db: Session = Depends(get_db)):
    get_template_or_404(db, payload.template_id)
    candidate = Candidate(**payload.model_dump())
    db.add(candidate)
    db.commit()
    return get_candidate_or_404(db, candidate.id)


@app.patch("/candidates/{candidate_id}", response_model=CandidateOut)
def update_candidate(candidate_id: int, payload: CandidatePatch, db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(candidate, key, value)
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.delete("/candidates/{candidate_id}")
def delete_candidate(candidate_id: int, db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    delete_uploaded_files(candidate.files)
    db.delete(candidate)
    db.commit()
    return {"ok": True}


@app.post("/candidates/{candidate_id}/reset", response_model=CandidateOut)
def reset_candidate_evaluation(candidate_id: int, db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    delete_uploaded_files(candidate.files)
    candidate.files.clear()
    candidate.scores.clear()
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.post("/candidates/{candidate_id}/files", response_model=CandidateOut)
def upload_candidate_files(candidate_id: int, files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
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
def delete_candidate_file(candidate_id: int, file_id: int, db: Session = Depends(get_db)):
    candidate_file = get_candidate_file_or_404(db, candidate_id, file_id)
    delete_uploaded_files([candidate_file])
    db.delete(candidate_file)
    db.commit()
    return get_candidate_or_404(db, candidate_id)


@app.post("/candidates/{candidate_id}/scores", response_model=list[ScoreOut])
def save_scores(candidate_id: int, payload: list[ScoreIn], db: Session = Depends(get_db)):
    candidate = get_candidate_or_404(db, candidate_id)
    valid_file_ids = {candidate_file.id for candidate_file in candidate.files}
    for item in payload:
        criterion = db.query(Criterion).filter(Criterion.id == item.criterion_id).first()
        if not criterion or criterion.template_id != candidate.template_id:
            raise HTTPException(status_code=400, detail="Criterio inválido para este candidato.")
        file_ids = clean_file_ids(item.file_ids, valid_file_ids)
        source = "automatic" if criterion.evaluation_mode == EvaluationMode.automatic else "manual"
        upsert_score(db, candidate_id, item.criterion_id, item.score, source, item.rationale, file_ids)
    db.commit()
    return [serialize_score(score) for score in get_candidate_or_404(db, candidate_id).scores]


@app.post("/candidates/{candidate_id}/evaluate-ai", response_model=CandidateOut)
def evaluate_ai(candidate_id: int, db: Session = Depends(get_db)):
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


@app.get("/summary", response_model=SummaryOut)
def summary(template_id: int | None = None, db: Session = Depends(get_db)):
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
