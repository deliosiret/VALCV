from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models import Candidate, Criterion, Score, Template
from app.scoring import summarize_candidate


LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo-sie.png"
BLUE = colors.HexColor("#25464a")
TEAL = colors.HexColor("#16697a")
SOFT_TEAL = colors.HexColor("#e6f1ef")
LINE = colors.HexColor("#cfe0df")
MUTED = colors.HexColor("#486366")
INK = colors.HexColor("#1d2f32")
GOLD = colors.HexColor("#d69b2d")
RED = colors.HexColor("#9a3412")
LIGHT_RED = colors.HexColor("#fff1ed")


def compact_number(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".")


def percent(value: float) -> str:
    return f"{compact_number((value or 0) * 100)}%"


def score_text(value: float | None) -> str:
    return "Pendiente" if value is None else f"{compact_number(value, 1)}/5"


def clean_recommendation(value: str) -> str:
    if value == "No califica por criterio crítico":
        return "No califica para el perfil"
    return value or "Sin recomendación"


def safe(value: str) -> str:
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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


def recommendation_color(value: str):
    if value in {"Altamente recomendable", "Recomendable"}:
        return TEAL
    if value == "Requiere revisión":
        return GOLD
    return RED


class HorizontalBar(Flowable):
    def __init__(self, value: float, width: float = 2.05 * inch, height: float = 0.13 * inch, fill=TEAL):
        super().__init__()
        self.value = max(0.0, min(value or 0, 1.0))
        self.width = width
        self.height = height
        self.fill = fill

    def draw(self):
        self.canv.setFillColor(colors.HexColor("#eef6f5"))
        self.canv.roundRect(0, 0, self.width, self.height, 4, fill=1, stroke=0)
        self.canv.setFillColor(self.fill)
        self.canv.roundRect(0, 0, self.width * self.value, self.height, 4, fill=1, stroke=0)


def make_styles():
    base = getSampleStyleSheet()
    base.add(ParagraphStyle("CoverTitle", parent=base["Title"], fontName="Helvetica-Bold", fontSize=24, leading=28, textColor=BLUE, alignment=TA_CENTER, spaceAfter=10))
    base.add(ParagraphStyle("CoverSub", parent=base["Normal"], fontName="Helvetica", fontSize=12, leading=17, textColor=MUTED, alignment=TA_CENTER, spaceAfter=8))
    base.add(ParagraphStyle("H1x", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=BLUE, spaceBefore=8, spaceAfter=8))
    base.add(ParagraphStyle("H2x", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=TEAL, spaceBefore=7, spaceAfter=5))
    base.add(ParagraphStyle("Bodyx", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13.2, textColor=INK, alignment=TA_JUSTIFY, spaceAfter=6))
    base.add(ParagraphStyle("Smallx", parent=base["BodyText"], fontName="Helvetica", fontSize=8, leading=10.5, textColor=MUTED, spaceAfter=3))
    base.add(ParagraphStyle("TableHeader", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=BLUE, alignment=TA_CENTER))
    base.add(ParagraphStyle("TableCell", parent=base["BodyText"], fontName="Helvetica", fontSize=7.3, leading=8.8, textColor=INK))
    base.add(ParagraphStyle("TableRight", parent=base["TableCell"], alignment=TA_RIGHT))
    base.add(ParagraphStyle("Kpi", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=14, leading=16, textColor=TEAL, alignment=TA_CENTER))
    base.add(ParagraphStyle("KpiLabel", parent=base["BodyText"], fontName="Helvetica", fontSize=7.6, leading=9, textColor=MUTED, alignment=TA_CENTER))
    base.add(ParagraphStyle("Note", parent=base["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=RED, backColor=LIGHT_RED, borderColor=colors.HexColor("#f0d7c5"), borderWidth=0.6, borderPadding=6, spaceAfter=8))
    return base


def paragraph(text: str, style) -> Paragraph:
    return Paragraph(safe(text), style)


def table(data, col_widths, style_commands=None):
    result = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), SOFT_TEAL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLUE),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if style_commands:
        commands.extend(style_commands)
    result.setStyle(TableStyle(commands))
    return result


def add_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(doc.leftMargin, 0.55 * inch, letter[0] - doc.rightMargin, 0.55 * inch)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 0.37 * inch, "Reporte general de evaluación curricular")
    canvas.drawRightString(letter[0] - doc.rightMargin, 0.37 * inch, f"Página {doc.page}")
    canvas.restoreState()


def cover_story(styles, template: Template, candidates: list[Candidate], preliminary: bool):
    story = []
    if LOGO_PATH.exists():
        story.append(Image(str(LOGO_PATH), width=1.85 * inch, height=0.75 * inch, kind="proportional"))
        story.append(Spacer(1, 0.22 * inch))
    story.append(Paragraph("Reporte general de evaluación curricular", styles["CoverTitle"]))
    story.append(Paragraph(f"Perfil evaluado: <b>{safe(template.name)}</b>", styles["CoverSub"]))
    story.append(Paragraph(datetime.now().strftime("Generado el %d/%m/%Y a las %H:%M"), styles["CoverSub"]))
    story.append(Spacer(1, 0.18 * inch))
    intro = (
        "Este informe presenta una lectura comparativa del concurso a partir de la estructura de evaluación definida para el perfil, "
        "los participantes registrados y las puntuaciones disponibles. Su propósito es facilitar una revisión ejecutiva, clara y "
        "ordenada de fortalezas, brechas y resultados preliminares o finales según el estado de avance de la evaluación."
    )
    story.append(Paragraph(intro, styles["Bodyx"]))
    if preliminary:
        story.append(Paragraph("Valoración preliminar: existen criterios o participantes con puntuaciones pendientes. Las conclusiones deben leerse como una fotografía de avance y no como cierre definitivo del proceso.", styles["Note"]))

    kpis = [
        [Paragraph(str(len(candidates)), styles["Kpi"]), Paragraph(str(len(template.categories)), styles["Kpi"]), Paragraph(str(len(template.criteria)), styles["Kpi"])],
        [Paragraph("Participantes", styles["KpiLabel"]), Paragraph("Categorías", styles["KpiLabel"]), Paragraph("Criterios", styles["KpiLabel"])],
    ]
    kpi_table = Table(kpis, colWidths=[1.55 * inch, 1.55 * inch, 1.55 * inch], hAlign="CENTER")
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fbfa")),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(kpi_table)
    story.append(PageBreak())
    return story


def structure_story(styles, template: Template, criteria: list[Criterion]):
    story = [Paragraph("Estructura de evaluación", styles["H1x"])]
    story.append(Paragraph("La ponderación muestra la incidencia global de cada categoría y criterio dentro del perfil evaluado. Los requisitos críticos se presentan como condiciones de cumplimiento, sin peso porcentual propio.", styles["Bodyx"]))
    grouped = defaultdict(list)
    for criterion in criteria:
        grouped[criterion.category].append(criterion)
    for category in template.categories:
        rows = [[Paragraph("Criterio", styles["TableHeader"]), Paragraph("Peso global", styles["TableHeader"])]]
        for criterion in grouped.get(category.name, []):
            weight = "Crítico" if criterion.is_critical else percent(criterion_global_weight(criterion))
            rows.append([Paragraph(safe(criterion.aspect), styles["TableCell"]), Paragraph(weight, styles["TableRight"])])
        block = [
            Paragraph(f"{safe(category.name)} · {percent(category.weight)} del perfil", styles["H2x"]),
            table(rows, [5.55 * inch, 0.95 * inch]),
            Spacer(1, 0.08 * inch),
        ]
        story.append(KeepTogether(block))
    story.append(PageBreak())
    return story


def ranking_story(styles, template: Template, summaries: list[dict], preliminary: bool):
    story = [Paragraph("Resultados comparativos", styles["H1x"])]
    if not summaries:
        story.append(Paragraph("No hay participantes registrados para este perfil.", styles["Bodyx"]))
        return story
    leader = summaries[0]
    story.append(Paragraph(
        f"El ranking ordena los participantes de mayor a menor resultado global. La lectura debe considerar que el resultado puede variar si se completan puntuaciones pendientes o si se incorporan nuevas validaciones del expediente.",
        styles["Bodyx"],
    ))
    if preliminary:
        story.append(Paragraph("El ranking es preliminar porque no todas las puntuaciones están completas.", styles["Note"]))
    rows = [[Paragraph("Pos.", styles["TableHeader"]), Paragraph("Participante", styles["TableHeader"]), Paragraph("Resultado", styles["TableHeader"]), Paragraph("Visual", styles["TableHeader"]), Paragraph("Lectura", styles["TableHeader"])]]
    for index, summary in enumerate(summaries, start=1):
        rec = clean_recommendation(summary["recommendation"])
        rows.append([
            Paragraph(str(index), styles["TableCell"]),
            Paragraph(safe(summary["name"]), styles["TableCell"]),
            Paragraph(percent(summary["global_score"]), styles["TableRight"]),
            HorizontalBar(summary["global_score"], fill=recommendation_color(summary["recommendation"])),
            Paragraph(safe(rec), styles["TableCell"]),
        ])
    story.append(table(rows, [0.35 * inch, 1.75 * inch, 0.68 * inch, 2.1 * inch, 1.25 * inch]))
    story.append(Spacer(1, 0.08 * inch))
    story.append(Paragraph(
        f"El mejor resultado registrado corresponde a {safe(leader['name'])}, con {percent(leader['global_score'])}. Esta posición refleja la suma ponderada de los criterios evaluados y debe complementarse con la revisión cualitativa del expediente.",
        styles["Bodyx"],
    ))
    story.append(PageBreak())
    return story


def category_matrix_story(styles, template: Template, summaries: list[dict]):
    story = [Paragraph("Comparación por categoría", styles["H1x"])]
    if not summaries:
        return story
    categories = [category.name for category in template.categories]
    header = [Paragraph("Participante", styles["TableHeader"])] + [Paragraph(safe(category), styles["TableHeader"]) for category in categories] + [Paragraph("Global", styles["TableHeader"])]
    rows = [header]
    for summary in summaries:
        rows.append(
            [Paragraph(safe(summary["name"]), styles["TableCell"])]
            + [Paragraph(percent(summary["categories"].get(category, 0)), styles["TableRight"]) for category in categories]
            + [Paragraph(percent(summary["global_score"]), styles["TableRight"])]
        )
    width = 6.65 * inch
    first = 1.35 * inch
    other = (width - first) / max(len(categories) + 1, 1)
    story.append(table(rows, [first] + [other] * (len(categories) + 1)))
    story.append(Spacer(1, 0.08 * inch))
    story.append(Paragraph(
        "Esta matriz permite observar de forma rápida dónde se concentran las principales fortalezas y brechas relativas. Un valor alto en una categoría no sustituye los requisitos obligatorios ni la validación experta del proceso.",
        styles["Bodyx"],
    ))
    story.append(PageBreak())
    return story


def criterion_matrix_story(styles, candidates: list[Candidate], criteria: list[Criterion]):
    evaluated = evaluated_criteria(candidates, criteria)
    story = [Paragraph("Cuadro comparativo de criterios evaluados", styles["H1x"])]
    if not evaluated:
        story.append(Paragraph("Aún no hay criterios con puntuaciones registradas para comparar.", styles["Bodyx"]))
        return story
    score_maps: dict[int, dict[int, Score]] = {
        candidate.id: {score.criterion_id: score for score in candidate.scores}
        for candidate in candidates
    }
    story.append(Paragraph(
        "El siguiente cuadro se limita a los criterios que tienen al menos una puntuación registrada. La escala se expresa de 0 a 5 para facilitar la lectura comparativa.",
        styles["Bodyx"],
    ))
    for category in dict.fromkeys(criterion.category for criterion in evaluated):
        children = [criterion for criterion in evaluated if criterion.category == category]
        header = [Paragraph("Criterio", styles["TableHeader"]), Paragraph("Peso global", styles["TableHeader"])] + [Paragraph(safe(candidate.name), styles["TableHeader"]) for candidate in candidates]
        rows = [header]
        for criterion in children:
            rows.append(
                [Paragraph(safe(criterion.aspect), styles["TableCell"]), Paragraph("Crítico" if criterion.is_critical else percent(criterion_global_weight(criterion)), styles["TableRight"])]
                + [Paragraph(score_text(score_maps[candidate.id].get(criterion.id).score if score_maps[candidate.id].get(criterion.id) else None), styles["TableRight"]) for candidate in candidates]
            )
        width = 6.65 * inch
        fixed = 2.55 * inch
        candidate_width = (width - fixed) / max(len(candidates), 1)
        story.append(KeepTogether([Paragraph(safe(category), styles["H2x"]), table(rows, [1.95 * inch, 0.6 * inch] + [candidate_width] * len(candidates)), Spacer(1, 0.09 * inch)]))
    return story


def participant_profiles_story(styles, candidates: list[Candidate], criteria: list[Criterion]):
    story = [PageBreak(), Paragraph("Lectura por participante", styles["H1x"])]
    if not candidates:
        story.append(Paragraph("No hay participantes registrados para este perfil.", styles["Bodyx"]))
        return story
    criteria_by_id = {criterion.id: criterion for criterion in criteria}
    story.append(Paragraph(
        "Esta lectura resume, para cada participante, los aspectos con mejor evidencia y los puntos que requieren mayor revisión. Solo se consideran criterios con puntuación registrada.",
        styles["Bodyx"],
    ))
    for candidate in sorted(candidates, key=lambda row: row.name):
        scored = [
            (score, criteria_by_id.get(score.criterion_id))
            for score in candidate.scores
            if criteria_by_id.get(score.criterion_id)
        ]
        scored.sort(key=lambda row: row[0].score, reverse=True)
        strengths = [row for row in scored if row[0].score >= 4][:3]
        gaps = [row for row in sorted(scored, key=lambda row: row[0].score) if row[0].score < 3][:3]
        block = [Paragraph(safe(candidate.name), styles["H2x"])]
        if strengths:
            block.append(Paragraph("Aspectos favorables observados:", styles["Smallx"]))
            for score, criterion in strengths:
                block.append(Paragraph(f"• {safe(criterion.aspect)} ({score_text(score.score)}).", styles["Smallx"]))
        else:
            block.append(Paragraph("No se identifican todavía criterios con puntuación alta registrada.", styles["Smallx"]))
        if gaps:
            block.append(Paragraph("Puntos a revisar o fortalecer:", styles["Smallx"]))
            for score, criterion in gaps:
                block.append(Paragraph(f"• {safe(criterion.aspect)} ({score_text(score.score)}).", styles["Smallx"]))
        else:
            block.append(Paragraph("No se registran brechas marcadas entre los criterios evaluados hasta el momento.", styles["Smallx"]))
        if candidate.comments:
            block.append(Paragraph(f"Observación general registrada: {safe(candidate.comments)}", styles["Smallx"]))
        block.append(Spacer(1, 0.08 * inch))
        story.append(KeepTogether(block))
    return story


def synthesis_story(styles, template: Template, summaries: list[dict], preliminary: bool):
    story = [PageBreak(), Paragraph("Síntesis interpretativa", styles["H1x"])]
    if not summaries:
        story.append(Paragraph("No hay información suficiente para elaborar una síntesis comparativa.", styles["Bodyx"]))
        return story
    top = summaries[0]
    bottom = summaries[-1]
    story.append(Paragraph(
        f"Para el perfil {safe(template.name)}, la comparación disponible muestra un rango de resultados entre {percent(bottom['global_score'])} y {percent(top['global_score'])}. "
        "La diferencia entre participantes debe interpretarse junto con la naturaleza de cada categoría, la evidencia documental disponible y las etapas posteriores del proceso.",
        styles["Bodyx"],
    ))
    category_names = [category.name for category in template.categories]
    if category_names:
        averages = []
        for category in category_names:
            average = sum(summary["categories"].get(category, 0) for summary in summaries) / max(len(summaries), 1)
            averages.append((category, average))
        averages.sort(key=lambda row: row[1], reverse=True)
        story.append(Paragraph(
            f"En promedio, la categoría con mejor desempeño relativo es {safe(averages[0][0])}, con {percent(averages[0][1])}. "
            f"La categoría que requiere mayor atención comparativa es {safe(averages[-1][0])}, con {percent(averages[-1][1])}.",
            styles["Bodyx"],
        ))
    if preliminary:
        story.append(Paragraph(
            "Dado que la valoración aún es preliminar, se recomienda completar los criterios pendientes antes de utilizar estos resultados como insumo definitivo para la decisión del concurso.",
            styles["Bodyx"],
        ))
    else:
        story.append(Paragraph(
            "Al estar completas las puntuaciones registradas para la estructura actual, el informe puede utilizarse como insumo consolidado para la deliberación del proceso, sin sustituir la decisión humana competente.",
            styles["Bodyx"],
        ))
    return story


def build_template_general_report(template: Template, candidates: list[Candidate], criteria: list[Criterion]) -> BytesIO:
    styles = make_styles()
    candidates = sorted(candidates, key=lambda candidate: candidate.name)
    summaries = [summarize_candidate(candidate, criteria, template) for candidate in candidates]
    summaries.sort(key=lambda row: row["global_score"], reverse=True)
    preliminary = has_pending_scores(candidates, criteria)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.72 * inch,
        title=f"Reporte general - {template.name}",
    )
    story = []
    story.extend(cover_story(styles, template, candidates, preliminary))
    story.extend(structure_story(styles, template, criteria))
    story.extend(ranking_story(styles, template, summaries, preliminary))
    story.extend(category_matrix_story(styles, template, summaries))
    story.extend(criterion_matrix_story(styles, candidates, criteria))
    story.extend(participant_profiles_story(styles, candidates, criteria))
    story.extend(synthesis_story(styles, template, summaries, preliminary))
    doc.build(story, onFirstPage=add_footer, onLaterPages=add_footer)
    buffer.seek(0)
    return buffer
