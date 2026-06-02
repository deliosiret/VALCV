from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import selectinload

from app.ai import evaluate_candidate_with_gemini
from app.config import settings
from app.database import SessionLocal
from app.models import AppSetting, Candidate, Criterion, EvaluationMode, Score, Template
from app.scoring import recommendation


TEMPLATE_NAME = "Gerente de Normas Eléctricas"


def app_setting(db, key: str) -> str:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else ""


def summarize_with_overrides(candidate: Candidate, criteria: list[Criterion], overrides: dict[int, float]) -> dict:
    stored_scores = {score.criterion_id: score.score for score in candidate.scores}
    score_by_criterion = {**stored_scores, **overrides}
    failed_critical = [
        criterion
        for criterion in criteria
        if criterion.is_critical and score_by_criterion.get(criterion.id, 0.0) < 5.0
    ]
    category_points = defaultdict(float)
    category_max = defaultdict(float)
    global_score = 0.0
    total_global_weight = 0.0

    for criterion in criteria:
        if criterion.is_critical:
            continue
        score = max(0.0, min(score_by_criterion.get(criterion.id, 0.0), 5.0))
        normalized = score / 5.0
        global_score += normalized * criterion.global_weight
        total_global_weight += criterion.global_weight
        category_points[criterion.category] += normalized * criterion.within_category_weight
        category_max[criterion.category] += criterion.within_category_weight

    normalized_global = 0.0 if failed_critical else global_score / max(total_global_weight, 0.00001)
    return {
        "global_score": round(normalized_global, 4),
        "recommendation": "No califica para el perfil" if failed_critical else recommendation(normalized_global),
        "categories": {
            category: round(category_points[category] / max(weight, 0.00001), 4)
            for category, weight in category_max.items()
        },
    }


def review_flags(criteria_by_code: dict[str, Criterion], scores: dict[int, dict]) -> list[str]:
    flags: list[str] = []
    f2 = criteria_by_code.get("F2")
    f3 = criteria_by_code.get("F3")
    f4 = criteria_by_code.get("F4")
    f5 = criteria_by_code.get("F5")
    if f2 and f3:
        f2_score = float(scores.get(f2.id, {}).get("score", 0) or 0)
        f3_score = float(scores.get(f3.id, {}).get("score", 0) or 0)
        if f2_score >= 4 and f3_score >= 4:
            flags.append("Revisar posible doble conteo: F2 y F3 puntúan alto; confirmar que existan maestrías distintas.")
    if f4:
        rationale = str(scores.get(f4.id, {}).get("rationale", "")).lower()
        if "curso" in rationale and "certific" not in rationale:
            flags.append("Revisar F4: la explicación menciona curso sin evidencia clara de certificación.")
    if f5:
        rationale = str(scores.get(f5.id, {}).get("rationale", "")).lower()
        if "certific" in rationale and "diplom" not in rationale:
            flags.append("Revisar F5: la explicación parece mezclar certificaciones con diplomados.")
    return flags


def missing_manual_criteria(candidate: Candidate, criteria: list[Criterion]) -> list[str]:
    scored_ids = {score.criterion_id for score in candidate.scores}
    return [
        f"{criterion.code} - {criterion.aspect}"
        for criterion in criteria
        if criterion.evaluation_mode == EvaluationMode.manual and criterion.id not in scored_ids
    ]


def main() -> None:
    output_dir = Path("/tmp")
    with SessionLocal() as db:
        template = (
            db.query(Template)
            .options(selectinload(Template.criteria), selectinload(Template.categories))
            .filter(Template.name == TEMPLATE_NAME)
            .first()
        )
        if not template:
            raise RuntimeError(f"No existe el perfil {TEMPLATE_NAME}.")

        candidates = (
            db.query(Candidate)
            .options(
                selectinload(Candidate.files),
                selectinload(Candidate.scores).selectinload(Score.file_references),
            )
            .filter(Candidate.template_id == template.id)
            .order_by(Candidate.name)
            .all()
        )
        automatic_criteria = [criterion for criterion in template.criteria if criterion.evaluation_mode == EvaluationMode.automatic]
        criteria_by_code = {criterion.code: criterion for criterion in template.criteria}
        api_key = app_setting(db, "gemini_api_key")
        model = app_setting(db, "gemini_model") or "gemini-3.1-flash-lite"

        rows = []
        for candidate in candidates:
            raw_scores = evaluate_candidate_with_gemini(candidate, automatic_criteria, settings.upload_dir, api_key, model)
            cleaned_scores = {}
            for item in raw_scores:
                criterion_id = int(item.get("criterion_id", 0) or 0)
                if criterion_id not in {criterion.id for criterion in automatic_criteria}:
                    continue
                score = max(0.0, min(float(item.get("score", 0) or 0), 5.0))
                cleaned_scores[criterion_id] = {
                    "score": score,
                    "rationale": str(item.get("rationale", "")),
                    "file_ids": item.get("file_ids", []) or [],
                }
            summary = summarize_with_overrides(
                candidate,
                list(template.criteria),
                {criterion_id: item["score"] for criterion_id, item in cleaned_scores.items()},
            )
            missing_manual = missing_manual_criteria(candidate, list(template.criteria))
            flags = review_flags(criteria_by_code, cleaned_scores)
            if missing_manual:
                flags.append(
                    f"Evaluación manual incompleta: faltan {len(missing_manual)} criterio(s); el resultado global no es plenamente comparable."
                )
            rows.append(
                {
                    "candidate_id": candidate.id,
                    "candidate": candidate.name,
                    "global_score": summary["global_score"],
                    "recommendation": summary["recommendation"],
                    "categories": summary["categories"],
                    "flags": flags,
                    "missing_manual_criteria": missing_manual,
                    "automatic_scores": cleaned_scores,
                }
            )

    rows.sort(key=lambda row: row["global_score"], reverse=True)
    payload = {
        "profile": TEMPLATE_NAME,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": "dry_run_no_database_writes",
        "candidates": rows,
    }
    output_path = output_dir / f"valcv_gne_ai_consistency_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nArchivo generado: {output_path}")


if __name__ == "__main__":
    main()
