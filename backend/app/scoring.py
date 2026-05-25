from collections import defaultdict

from app.models import Candidate, Criterion, Score, ScoreFileReference


def recommendation(global_score: float) -> str:
    if global_score >= 0.85:
        return "Altamente recomendable"
    if global_score >= 0.7:
        return "Recomendable"
    if global_score >= 0.55:
        return "Requiere revisión"
    return "No recomendable"


def summarize_candidate(candidate: Candidate, criteria: list[Criterion]) -> dict:
    score_by_criterion = {score.criterion_id: score.score for score in candidate.scores}
    category_weight = {}
    category_points = defaultdict(float)
    category_max = defaultdict(float)
    global_score = 0.0
    total_global_weight = 0.0

    for criterion in criteria:
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
    normalized_global = global_score / max(total_global_weight, 0.00001)

    return {
        "id": candidate.id,
        "name": candidate.name,
        "document_id": candidate.document_id,
        "global_score": round(normalized_global, 4),
        "recommendation": recommendation(normalized_global),
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
    )
    db.add(created)
    db.flush()
    if file_ids is not None:
        for file_id in dict.fromkeys(file_ids):
            created.file_references.append(ScoreFileReference(file_id=file_id))
    return created
