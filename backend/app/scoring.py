from collections import defaultdict

from app.models import Candidate, Criterion, Score, ScoreFileReference, Template


BONUS_GLOBAL_WEIGHT = 0.05
BONUS_CATEGORY_NAME = "Bonificación adicional"
DEFAULT_HIGHLY_RECOMMENDED_THRESHOLD = 0.85
DEFAULT_RECOMMENDED_THRESHOLD = 0.7
DEFAULT_REVIEW_THRESHOLD = 0.55
INCONCLUSIVE_RECOMMENDATION = "No concluyente"


def threshold_value(template: Template | None, field: str, default: float) -> float:
    value = getattr(template, field, None)
    return default if value is None else float(value)


def recommendation_scale(template: Template | None = None) -> dict[str, float]:
    return {
        "highly_recommended": threshold_value(template, "highly_recommended_threshold", DEFAULT_HIGHLY_RECOMMENDED_THRESHOLD),
        "recommended": threshold_value(template, "recommended_threshold", DEFAULT_RECOMMENDED_THRESHOLD),
        "review": threshold_value(template, "review_threshold", DEFAULT_REVIEW_THRESHOLD),
    }


def recommendation(global_score: float, template: Template | None = None) -> str:
    scale = recommendation_scale(template)
    if global_score >= scale["highly_recommended"]:
        return "Altamente recomendable"
    if global_score >= scale["recommended"]:
        return "Recomendable"
    if global_score >= scale["review"]:
        return "Requiere revisión"
    return "No recomendable"


def summarize_candidate(candidate: Candidate, criteria: list[Criterion], template: Template | None = None) -> dict:
    score_by_criterion = {score.criterion_id: score.score for score in candidate.scores}
    pending_criteria = [criterion for criterion in criteria if criterion.id not in score_by_criterion]
    failed_critical = [
        criterion
        for criterion in criteria
        if criterion.is_critical and criterion.id in score_by_criterion and score_by_criterion[criterion.id] < 5.0
    ]
    category_weight = {}
    category_points = defaultdict(float)
    category_max = defaultdict(float)
    global_score = 0.0
    total_global_weight = 0.0

    for criterion in criteria:
        if criterion.is_critical:
            continue
        score = score_by_criterion.get(criterion.id, 0.0)
        normalized = max(0.0, min(score, 5.0)) / 5.0
        global_score += normalized * criterion.global_weight
        total_global_weight += criterion.global_weight
        category_points[criterion.category] += normalized * criterion.within_category_weight
        category_max[criterion.category] += criterion.within_category_weight
        category_weight[criterion.category] = criterion.category_weight

    categories = {
        category: round((category_points[category] / max(weight, 0.00001)), 4)
        for category, weight in category_max.items()
    }
    base_global = global_score / max(total_global_weight, 0.00001)
    bonus_score = max(0.0, min(float(candidate.ai_bonus_score or 0), 5.0))
    bonus_amount = (bonus_score / 5.0) * BONUS_GLOBAL_WEIGHT
    is_complete = not pending_criteria
    normalized_global = 0.0 if is_complete and failed_critical else min(1.0, base_global + bonus_amount)
    if bonus_score > 0 and not failed_critical:
        categories[BONUS_CATEGORY_NAME] = round(bonus_score / 5.0, 4)
    if not is_complete:
        recommendation_text = INCONCLUSIVE_RECOMMENDATION
    elif failed_critical:
        recommendation_text = "No califica por criterio crítico"
    else:
        recommendation_text = recommendation(normalized_global, template)

    return {
        "id": candidate.id,
        "name": candidate.name,
        "document_id": candidate.document_id,
        "global_score": round(normalized_global, 4),
        "base_global_score": round(0.0 if is_complete and failed_critical else base_global, 4),
        "bonus_score": round(bonus_score, 2),
        "bonus_amount": round(0.0 if is_complete and failed_critical else min(bonus_amount, max(0.0, 1.0 - base_global)), 4),
        "bonus_rationale": candidate.ai_bonus_rationale or "",
        "recommendation": recommendation_text,
        "recommendation_scale": recommendation_scale(template),
        "is_complete": is_complete,
        "pending_criteria": len(pending_criteria),
        "categories": categories,
    }


def upsert_score(
    db,
    candidate_id: int,
    criterion_id: int,
    score: float,
    source: str,
    rationale: str,
    file_ids: list[int] | None = None,
    evaluator_note: str | None = None,
):
    current = (
        db.query(Score)
        .filter(Score.candidate_id == candidate_id, Score.criterion_id == criterion_id)
        .first()
    )
    if current:
        current.score = score
        current.source = source
        current.rationale = rationale
        if evaluator_note is not None:
            current.evaluator_note = evaluator_note
        if file_ids is not None:
            current.file_references.clear()
            db.flush()
            for file_id in dict.fromkeys(file_ids):
                current.file_references.append(ScoreFileReference(file_id=file_id))
        return current
    created = Score(
        candidate_id=candidate_id,
        criterion_id=criterion_id,
        score=score,
        source=source,
        rationale=rationale,
        evaluator_note=evaluator_note or "",
    )
    db.add(created)
    db.flush()
    if file_ids is not None:
        for file_id in dict.fromkeys(file_ids):
            created.file_references.append(ScoreFileReference(file_id=file_id))
    return created
