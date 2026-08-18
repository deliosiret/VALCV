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


def score_text(value: float | None) -> str:
    return "Pendiente" if value is None else f"{compact_number(value, 1)}/5"


def critical_score_text(value: float | None, pending_text: str = "Pendiente") -> str:
    if value is None:
        return pending_text
    return "Cumple" if value >= 5 else "No cumple"


def criterion_score_text(criterion: Criterion, value: float | None, pending_text: str = "Pendiente") -> str:
    if criterion.is_critical:
        return critical_score_text(value, pending_text)
    return pending_text if value is None else score_text(value)


def alias_name(index: int) -> str:
    letters = ""
    current = index
    while True:
        letters = chr(ord("A") + (current % 26)) + letters
        current = current // 26 - 1
        if current < 0:
            break
    return letters


def candidate_name_by_id(candidates: list[Candidate]) -> dict[int, str]:
    return {candidate.id: candidate.name for candidate in candidates}


def participant_aliases(candidates: list[Candidate]) -> dict[int, str]:
    return {candidate.id: alias_name(index) for index, candidate in enumerate(candidates)}


def clean_recommendation(value: str) -> str:
    if value == "No concluyente":
        return "No concluyente"
    if value == "No califica por criterio crítico":
        return "No califica para el perfil"
    return value or "Sin recomendación"


def narrative_paragraphs(narrative: dict | None, key: str) -> list[str]:
    values = (narrative or {}).get(key, [])
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def criterion_global_weight(criterion: Criterion) -> float:
    return 0.0 if criterion.is_critical else float(criterion.global_weight or 0)


def evaluated_criteria(candidates: list[Candidate], criteria: list[Criterion]) -> list[Criterion]:
    scored_ids = {score.criterion_id for candidate in candidates for score in candidate.scores}
    return [criterion for criterion in criteria if criterion.id in scored_ids]


def has_pending_scores(candidates: list[Candidate], criteria: list[Criterion]) -> bool:
    if not candidates:
        return True
    required_ids = {criterion.id for criterion in criteria}
    for candidate in candidates:
        scored_ids = {score.criterion_id for score in candidate.scores}
        if required_ids - scored_ids:
            return True
    return False


def score_maps_by_candidate(candidates: list[Candidate]) -> dict[int, dict[int, Score]]:
    return {
        candidate.id: {score.criterion_id: score for score in candidate.scores}
        for candidate in candidates
    }


def recommendation_tone(value: str) -> str:
    if value in {"Altamente recomendable", "Recomendable"}:
        return "good"
    if value == "Requiere revisión":
        return "warn"
    return "bad"


def logo_data_uri() -> str | None:
    if not LOGO_PATH.exists():
        return None
    logo_b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{logo_b64}"


def build_report_context(
    template: Template,
    candidates: list[Candidate],
    criteria: list[Criterion],
    narrative: dict | None,
) -> dict[str, Any]:
    candidates = sorted(candidates, key=lambda candidate: candidate.name)
    names_by_id = candidate_name_by_id(candidates)
    summaries = [summarize_candidate(candidate, criteria, template) for candidate in candidates]
    summaries.sort(key=lambda row: row["global_score"], reverse=True)
    ranked_candidate_ids = [summary["id"] for summary in summaries]
    ranked_candidates = sorted(
        candidates,
        key=lambda candidate: (
            ranked_candidate_ids.index(candidate.id)
            if candidate.id in ranked_candidate_ids
            else len(ranked_candidate_ids),
            candidate.name,
        ),
    )
    aliases = participant_aliases(ranked_candidates)
    preliminary = has_pending_scores(candidates, criteria)
    score_maps = score_maps_by_candidate(candidates)

    grouped: dict[str, list[Criterion]] = defaultdict(list)
    for criterion in criteria:
        grouped[criterion.category].append(criterion)

    structure = []
    for category in template.categories:
        structure.append(
            {
                "name": category.name,
                "weight": percent(category.weight),
                "criteria": [
                    {
                        "aspect": criterion.aspect,
                        "weight": "Crítico" if criterion.is_critical else percent(criterion_global_weight(criterion)),
                        "critical": criterion.is_critical,
                    }
                    for criterion in grouped.get(category.name, [])
                ],
            }
        )

    ranking = []
    for index, summary in enumerate(summaries, start=1):
        recommendation = clean_recommendation(summary["recommendation"])
        ranking.append(
            {
                "position": index,
                "name": names_by_id.get(summary["id"], "Participante"),
                "score": percent(summary["global_score"]),
                "bar": percent_value(summary["global_score"]),
                "recommendation": recommendation,
                "tone": recommendation_tone(summary["recommendation"]),
            }
        )

    categories = [category.name for category in template.categories]
    category_matrix = []
    for summary in summaries:
        category_matrix.append(
            {
                "name": names_by_id.get(summary["id"], "Participante"),
                "category_values": [percent(summary["categories"].get(category, 0)) for category in categories],
                "global": percent(summary["global_score"]),
            }
        )

    evaluated = evaluated_criteria(candidates, criteria)
    criterion_sections = []
    for category in dict.fromkeys(criterion.category for criterion in evaluated):
        rows = []
        for criterion in [item for item in evaluated if item.category == category]:
            rows.append(
                {
                    "aspect": criterion.aspect,
                    "weight": "Crítico" if criterion.is_critical else percent(criterion_global_weight(criterion)),
                    "scores": [
                        criterion_score_text(
                            criterion,
                            score_maps[candidate.id].get(criterion.id).score
                            if score_maps[candidate.id].get(criterion.id)
                            else None,
                            "-",
                        )
                        for candidate in ranked_candidates
                    ],
                }
            )
        criterion_sections.append({"category": category, "rows": rows})

    criteria_by_id = {criterion.id: criterion for criterion in criteria}
    participant_profiles = []
    for candidate in candidates:
        scored = [
            (score, criteria_by_id.get(score.criterion_id))
            for score in candidate.scores
            if criteria_by_id.get(score.criterion_id)
        ]
        scored.sort(key=lambda row: row[0].score, reverse=True)
        strengths = [row for row in scored if row[0].score >= 4][:3]
        gaps = [row for row in sorted(scored, key=lambda row: row[0].score) if row[0].score < 3][:3]
        summary = next((item for item in summaries if item["id"] == candidate.id), None)
        participant_profiles.append(
            {
                "name": candidate.name,
                "global_score": percent(summary["global_score"]) if summary else "Pendiente",
                "strengths": [
                    f"{criterion.aspect} ({criterion_score_text(criterion, score.score)})"
                    for score, criterion in strengths
                ],
                "gaps": [
                    f"{criterion.aspect} ({criterion_score_text(criterion, score.score)})"
                    for score, criterion in gaps
                ],
                "comments": candidate.comments or "",
            }
        )

    synthesis = narrative_paragraphs(narrative, "synthesis")
    conclusion = narrative_paragraphs(narrative, "conclusion")
    if not synthesis:
        synthesis, conclusion = fallback_synthesis(template, summaries, preliminary)

    return {
        "logo_data_uri": logo_data_uri(),
        "generated_at": datetime.now().strftime("%d/%m/%Y · %H:%M"),
        "template_name": template.name,
        "preliminary": preliminary,
        "summary": {
            "participants": len(candidates),
            "categories": len(template.categories),
            "criteria": len(criteria),
            "evaluated_criteria": len(evaluated),
        },
        "structure": structure,
        "ranking": ranking,
        "leader": ranking[0] if ranking else None,
        "categories": categories,
        "category_matrix": category_matrix,
        "participants": [{"alias": aliases[candidate.id], "name": candidate.name} for candidate in ranked_candidates],
        "criterion_headers": [aliases[candidate.id] for candidate in ranked_candidates],
        "criterion_sections": criterion_sections,
        "participant_profiles": participant_profiles,
        "synthesis": synthesis,
        "conclusion": conclusion or ["La conclusión debe completarse con la validación humana responsable del proceso."],
    }


def fallback_synthesis(template: Template, summaries: list[dict], preliminary: bool) -> tuple[list[str], list[str]]:
    if not summaries:
        return (["No hay información suficiente para elaborar una síntesis comparativa."], [])

    top = summaries[0]
    bottom = summaries[-1]
    synthesis = [
        (
            f"Para el perfil {template.name}, la comparación disponible muestra un rango de resultados entre "
            f"{percent(bottom['global_score'])} y {percent(top['global_score'])}. La diferencia entre participantes "
            "debe interpretarse junto con la naturaleza de cada categoría, la evidencia documental disponible y las "
            "etapas posteriores del proceso."
        )
    ]
    category_names = [category.name for category in template.categories]
    if category_names:
        averages = []
        for category in category_names:
            average = sum(summary["categories"].get(category, 0) for summary in summaries) / max(len(summaries), 1)
            averages.append((category, average))
        averages.sort(key=lambda row: row[1], reverse=True)
        synthesis.append(
            f"En promedio, la categoría con mejor desempeño relativo es {averages[0][0]}, con {percent(averages[0][1])}. "
            f"La categoría que requiere mayor atención comparativa es {averages[-1][0]}, con {percent(averages[-1][1])}."
        )
    if preliminary:
        synthesis.append(
            "Dado que la valoración aún es preliminar, se recomienda completar los criterios pendientes antes de utilizar "
            "estos resultados como insumo definitivo para la decisión del concurso."
        )
    else:
        synthesis.append(
            "Al estar completas las puntuaciones registradas para la estructura actual, el informe puede utilizarse como "
            "insumo consolidado para la deliberación del proceso, sin sustituir la decisión humana competente."
        )
    conclusion = [
        "La lectura final debe integrarse con la verificación documental, las entrevistas, las pruebas técnicas y cualquier validación institucional que corresponda al proceso."
    ]
    return synthesis, conclusion


def general_report_source_text(template: Template, candidates: list[Candidate], criteria: list[Criterion]) -> str:
    candidates = sorted(candidates, key=lambda candidate: candidate.name)
    summaries = [summarize_candidate(candidate, criteria, template) for candidate in candidates]
    summaries.sort(key=lambda row: row["global_score"], reverse=True)
    preliminary = has_pending_scores(candidates, criteria)
    candidate_names = candidate_name_by_id(candidates)
    score_maps = score_maps_by_candidate(candidates)
    lines = [
        "REPORTE GENERAL DE EVALUACIÓN CURRICULAR",
        f"Perfil evaluado: {template.name}",
        f"Estado de la valoración: {'preliminar/no concluyente' if preliminary else 'completa'}",
        f"Participantes: {len(candidates)}",
        f"Categorías: {len(template.categories)}",
        f"Criterios: {len(criteria)}",
        "",
        "IDENTIFICACIÓN DE PARTICIPANTES",
    ]
    for candidate in candidates:
        lines.append(candidate.name)

    lines.extend(["", "ESTRUCTURA DE EVALUACIÓN"])
    grouped = defaultdict(list)
    for criterion in criteria:
        grouped[criterion.category].append(criterion)
    for category in template.categories:
        lines.append(f"Categoría: {category.name} | Peso del perfil: {percent(category.weight)}")
        for criterion in grouped.get(category.name, []):
            weight = "requisito de cumplimiento obligatorio" if criterion.is_critical else percent(criterion_global_weight(criterion))
            lines.append(f"- {criterion.aspect} | Peso global: {weight}")

    lines.extend(["", "RANKING GLOBAL"])
    for index, summary in enumerate(summaries, start=1):
        lines.append(
            f"{index}. {candidate_names.get(summary['id'], 'Participante')} | Resultado: {percent(summary['global_score'])} | "
            f"Lectura: {clean_recommendation(summary['recommendation'])}"
        )

    lines.extend(["", "COMPARACIÓN POR CATEGORÍA"])
    for summary in summaries:
        values = ", ".join(f"{category.name}: {percent(summary['categories'].get(category.name, 0))}" for category in template.categories)
        lines.append(f"{candidate_names.get(summary['id'], 'Participante')}: {values}")

    lines.extend(["", "CRITERIOS EVALUADOS"])
    for criterion in evaluated_criteria(candidates, criteria):
        values = []
        for candidate in candidates:
            score = score_maps.get(candidate.id, {}).get(criterion.id)
            values.append(f"{candidate.name}={criterion_score_text(criterion, score.score if score else None)}")
        lines.append(f"{criterion.category} / {criterion.aspect}: {', '.join(values)}")

    lines.extend(["", "LECTURA POR PARTICIPANTE"])
    criteria_by_id = {criterion.id: criterion for criterion in criteria}
    for candidate in candidates:
        summary = next((item for item in summaries if item["id"] == candidate.id), None)
        lines.append(f"{candidate.name} | Resultado global: {percent(summary['global_score']) if summary else 'Pendiente'}")
        scored = [
            (score, criteria_by_id.get(score.criterion_id))
            for score in candidate.scores
            if criteria_by_id.get(score.criterion_id)
        ]
        scored.sort(key=lambda row: row[0].score, reverse=True)
        strengths = [row for row in scored if row[0].score >= 4][:3]
        gaps = [row for row in sorted(scored, key=lambda row: row[0].score) if row[0].score < 3][:3]
        if strengths:
            lines.append("Aspectos favorables: " + "; ".join(f"{criterion.aspect} ({criterion_score_text(criterion, score.score)})" for score, criterion in strengths))
        if gaps:
            lines.append("Puntos a revisar: " + "; ".join(f"{criterion.aspect} ({criterion_score_text(criterion, score.score)})" for score, criterion in gaps))
        if candidate.comments:
            lines.append(f"Observación general: {candidate.comments}")

    return "\n".join(lines)


def build_template_general_report(
    template: Template,
    candidates: list[Candidate],
    criteria: list[Criterion],
    narrative: dict | None = None,
) -> BytesIO:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
    )
    tpl = env.get_template("general_report.html.j2")
    html = tpl.render(**build_report_context(template, candidates, criteria, narrative))
    pdf_bytes = HTML(string=html, base_url=str(TEMPLATES_DIR)).write_pdf()
    return BytesIO(pdf_bytes)
