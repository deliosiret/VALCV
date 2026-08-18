from __future__ import annotations

import base64
from collections import defaultdict
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from app.models import Candidate, Criterion, Score, Template
from app.scoring import summarize_candidate


BASE_DIR = Path(__file__).resolve().parent
LOGO_PATH = BASE_DIR / "assets" / "logo-sie.png"
TEMPLATES_DIR = BASE_DIR / "templates"


def compact_number(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".")


def percent(value: float) -> str:
    return f"{compact_number((value or 0) * 100)}%"


def percent_value(value: float) -> str:
    return compact_number((value or 0) * 100)


def score_text(score: float | None) -> str:
    return "Sin evaluar" if score is None else f"{compact_number(score, 1)}/5"


def report_recommendation(value: str) -> str:
    if value == "No concluyente":
        return "No concluyente"
    if value == "No califica por criterio crítico":
        return "No califica para el perfil"
    return value or "Sin recomendación"


def final_decision_text(value: str) -> str:
    if value == "qualifies":
        return "Califica"
    if value == "not_qualifies":
        return "No califica"
    return "No definida"


def recommendation_tone(value: str) -> str:
    if value in {"Altamente recomendable", "Recomendable"}:
        return "good"
    if value == "Requiere revisión":
        return "warn"
    return "bad"


def recommendation_scale(summary: dict, template: Template) -> dict[str, float]:
    scale = summary.get("recommendation_scale") or {}
    return {
        "highly_recommended": scale.get("highly_recommended", template.highly_recommended_threshold),
        "recommended": scale.get("recommended", template.recommended_threshold),
        "review": scale.get("review", template.review_threshold),
    }


def category_results(template: Template, summary: dict) -> list[tuple[str, float, float]]:
    return [
        (category.name, category.weight, summary["categories"].get(category.name, 0))
        for category in template.categories
    ]


def criterion_global_weight(criterion: Criterion) -> float:
    return 0.0 if criterion.is_critical else float(criterion.global_weight or 0)


def logo_data_uri() -> str | None:
    if not LOGO_PATH.exists():
        return None
    return f"data:image/png;base64,{base64.b64encode(LOGO_PATH.read_bytes()).decode('ascii')}"


def conclusion_paragraphs(conclusion: dict | None) -> list[str]:
    values = (conclusion or {}).get("conclusion", [])
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def fallback_conclusion(candidate: Candidate, template: Template, summary: dict) -> list[str]:
    recommendation = summary["recommendation"]
    if recommendation == "No concluyente":
        text = (
            "La evaluación todavía no es concluyente porque existen criterios pendientes de puntuación. "
            "El resultado debe leerse como una referencia preliminar hasta completar la revisión del perfil."
        )
    elif recommendation == "No califica por criterio crítico":
        text = (
            "La evaluación registra al menos un requisito obligatorio no cumplido o no evidenciado para el perfil. "
            "Esta condición impide que el candidato califique globalmente, aun cuando pueda presentar fortalezas parciales."
        )
    elif recommendation == "Altamente recomendable":
        text = "El perfil presenta una correspondencia alta con los criterios definidos."
    elif recommendation == "Recomendable":
        text = "El perfil presenta una correspondencia favorable con los criterios definidos, con aspectos que pueden revisarse en fases posteriores."
    elif recommendation == "Requiere revisión":
        text = "El perfil requiere revisión adicional para determinar si las brechas observadas pueden compensarse mediante validación técnica posterior."
    else:
        text = "El perfil muestra una correspondencia limitada con los criterios definidos para la vacante evaluada."
    return [
        f"{text} El resultado debe analizarse junto con las evidencias documentales, las observaciones del evaluador y los objetivos específicos del perfil {template.name}.",
        (
            f"Para {candidate.name}, este reporte debe utilizarse como soporte documental. La decisión final corresponde a las instancias humanas competentes, "
            "considerando expediente, entrevistas, validaciones y demás elementos institucionales aplicables."
        ),
    ]


def build_candidate_report_context(
    candidate: Candidate,
    template: Template,
    criteria: list[Criterion],
    conclusion: dict | None = None,
) -> dict[str, Any]:
    summary = summarize_candidate(candidate, criteria, template)
    score_by_criterion: dict[int, Score] = {score.criterion_id: score for score in candidate.scores}
    scale = recommendation_scale(summary, template)

    categories = []
    for name, weight, result in category_results(template, summary):
        categories.append(
            {
                "name": name,
                "weight": percent(weight),
                "result": percent(result),
                "bar": percent_value(result),
            }
        )
    if summary.get("bonus_score", 0) > 0:
        categories.append(
            {
                "name": "Bonificación adicional",
                "weight": f"Hasta {percent(0.05)}",
                "result": percent(summary.get("categories", {}).get("Bonificación adicional", 0)),
                "bar": percent_value(summary.get("categories", {}).get("Bonificación adicional", 0)),
            }
        )

    grouped: dict[str, list[Criterion]] = defaultdict(list)
    for criterion in criteria:
        grouped[criterion.category].append(criterion)

    criteria_sections = []
    evaluated_criteria = 0
    for category in dict.fromkeys(criterion.category for criterion in criteria):
        rows = []
        for criterion in grouped.get(category, []):
            score = score_by_criterion.get(criterion.id)
            if score:
                evaluated_criteria += 1
            rows.append(
                {
                    "aspect": criterion.aspect,
                    "weight": "Crítico" if criterion.is_critical else percent(criterion_global_weight(criterion)),
                    "score": score_text(score.score if score else None),
                    "rationale": score.rationale if score else "",
                    "note": score.evaluator_note if score else "",
                }
            )
        criteria_sections.append({"category": category, "rows": rows})

    scored = [
        (score, next((criterion for criterion in criteria if criterion.id == score.criterion_id), None))
        for score in candidate.scores
    ]
    scored = [(score, criterion) for score, criterion in scored if criterion is not None]
    strengths = [
        f"{criterion.aspect} ({score_text(score.score)})"
        for score, criterion in sorted(scored, key=lambda row: row[0].score, reverse=True)
        if score.score >= 4
    ][:3]
    gaps = [
        f"{criterion.aspect} ({score_text(score.score)})"
        for score, criterion in sorted(scored, key=lambda row: row[0].score)
        if score.score < 3
    ][:3]
    pending = [criterion.aspect for criterion in criteria if criterion.id not in score_by_criterion]

    paragraphs = conclusion_paragraphs(conclusion) or fallback_conclusion(candidate, template, summary)

    return {
        "logo_data_uri": logo_data_uri(),
        "generated_at": datetime.now().strftime("%d/%m/%Y · %H:%M"),
        "candidate_name": candidate.name,
        "template_name": template.name,
        "document_id": candidate.document_id or "No registrado",
        "evaluator": candidate.evaluator or "No registrado",
        "final_decision": final_decision_text(candidate.final_decision),
        "global_score": percent(summary["global_score"]),
        "global_bar": percent_value(summary["global_score"]),
        "recommendation": report_recommendation(summary["recommendation"]),
        "recommendation_tone": recommendation_tone(summary["recommendation"]),
        "is_preliminary": summary["recommendation"] == "No concluyente",
        "summary": {
            "categories": len(template.categories),
            "criteria": len(criteria),
            "evaluated_criteria": evaluated_criteria,
            "documents": len(candidate.files),
            "pending_criteria": len(pending),
        },
        "scale_text": (
            f"Altamente recomendable desde {percent(scale['highly_recommended'])}; "
            f"Recomendable desde {percent(scale['recommended'])}; "
            f"Requiere revisión desde {percent(scale['review'])}."
        ),
        "categories": categories,
        "criteria_sections": criteria_sections,
        "documents": [file.original_name for file in candidate.files],
        "strengths": strengths,
        "gaps": gaps,
        "pending": pending,
        "comments": candidate.comments or "",
        "bonus_score": score_text(summary.get("bonus_score")) if summary.get("bonus_score", 0) > 0 else "",
        "bonus_amount": percent(summary.get("bonus_amount", 0)),
        "bonus_rationale": summary.get("bonus_rationale", ""),
        "conclusion": paragraphs,
    }


def candidate_report_source_text(candidate: Candidate, template: Template, criteria: list[Criterion]) -> str:
    summary = summarize_candidate(candidate, criteria, template)
    score_by_criterion = {score.criterion_id: score for score in candidate.scores}
    lines = [
        "REPORTE INDIVIDUAL DE EVALUACIÓN CURRICULAR",
        f"Candidato: {candidate.name}",
        f"Perfil evaluado: {template.name}",
        f"Cédula / ID: {candidate.document_id or 'No registrado'}",
        f"Evaluador asignado: {candidate.evaluator or 'No registrado'}",
        f"Resultado global: {percent(summary['global_score'])}",
        f"Recomendación: {report_recommendation(summary['recommendation'])}",
        f"Decisión del evaluador: {final_decision_text(candidate.final_decision)}",
        f"Observaciones generales: {candidate.comments or 'No registradas'}",
        "",
        "RESULTADOS POR CATEGORÍA",
    ]
    for name, weight, result in category_results(template, summary):
        lines.append(f"{name}: peso {percent(weight)}, resultado {percent(result)}")
    if summary.get("bonus_score", 0) > 0:
        lines.append(
            f"Bonificación adicional: {score_text(summary.get('bonus_score'))}; impacto global {percent(summary.get('bonus_amount', 0))}; "
            f"justificación {summary.get('bonus_rationale', '')}"
        )

    lines.extend(["", "CRITERIOS"])
    for criterion in criteria:
        score = score_by_criterion.get(criterion.id)
        lines.append(
            f"{criterion.category} / {criterion.aspect}: peso {'crítico' if criterion.is_critical else percent(criterion_global_weight(criterion))}; "
            f"resultado {score_text(score.score if score else None)}"
        )
        if score and score.rationale:
            lines.append(f"Comentario de evaluación: {score.rationale}")
        if score and score.evaluator_note:
            lines.append(f"Observación del evaluador: {score.evaluator_note}")

    lines.extend(["", "DOCUMENTOS"])
    if candidate.files:
        for file in candidate.files:
            lines.append(file.original_name)
    else:
        lines.append("No se registraron documentos.")
    return "\n".join(lines)


def build_candidate_report(
    candidate: Candidate,
    template: Template,
    criteria: list[Criterion],
    conclusion: dict | None = None,
) -> BytesIO:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
    )
    tpl = env.get_template("candidate_report.html.j2")
    html = tpl.render(**build_candidate_report_context(candidate, template, criteria, conclusion))
    return BytesIO(HTML(string=html, base_url=str(TEMPLATES_DIR)).write_pdf())
