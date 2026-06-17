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
PAPER = colors.HexColor("#f7faf9")
WHITE = colors.white
DEEP_BLUE = colors.HexColor("#18383d")
PALE_BLUE = colors.HexColor("#edf6f5")


def compact_number(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".")


def percent(value: float) -> str:
    return f"{compact_number((value or 0) * 100)}%"


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


class AccentRule(Flowable):
    def __init__(self, width: float = 1.2 * inch, height: float = 0.07 * inch, fill=GOLD):
        super().__init__()
        self.width = width
        self.height = height
        self.fill = fill

    def draw(self):
        self.canv.setFillColor(self.fill)
        self.canv.roundRect(0, 0, self.width, self.height, 2, fill=1, stroke=0)


def make_styles():
    base = getSampleStyleSheet()
    base.add(ParagraphStyle("CoverEyebrow", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=TEAL, alignment=TA_LEFT, spaceAfter=8))
    base.add(ParagraphStyle("CoverTitle", parent=base["Title"], fontName="Helvetica-Bold", fontSize=28, leading=31, textColor=DEEP_BLUE, alignment=TA_LEFT, spaceAfter=8))
    base.add(ParagraphStyle("CoverProfile", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=BLUE, alignment=TA_LEFT, spaceAfter=8))
    base.add(ParagraphStyle("CoverSub", parent=base["Normal"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=MUTED, alignment=TA_LEFT, spaceAfter=4))
    base.add(ParagraphStyle("CoverMeta", parent=base["Normal"], fontName="Helvetica", fontSize=8, leading=10.5, textColor=WHITE, alignment=TA_RIGHT, spaceAfter=2))
    base.add(ParagraphStyle("CoverIntro", parent=base["BodyText"], fontName="Helvetica", fontSize=10, leading=14, textColor=INK, alignment=TA_JUSTIFY, spaceAfter=0))
    base.add(ParagraphStyle("CoverKpi", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=20, leading=22, textColor=TEAL, alignment=TA_CENTER))
    base.add(ParagraphStyle("CoverKpiLabel", parent=base["BodyText"], fontName="Helvetica", fontSize=8.2, leading=10, textColor=MUTED, alignment=TA_CENTER))
    base.add(ParagraphStyle("CoverFooterTitle", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=9.2, leading=11.5, textColor=BLUE, alignment=TA_LEFT))
    base.add(ParagraphStyle("CoverFooterText", parent=base["BodyText"], fontName="Helvetica", fontSize=8, leading=10.5, textColor=MUTED, alignment=TA_LEFT))
    base.add(ParagraphStyle("H1x", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=BLUE, spaceBefore=8, spaceAfter=8))
    base.add(ParagraphStyle("H2x", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=TEAL, spaceBefore=7, spaceAfter=5))
    base.add(ParagraphStyle("Bodyx", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13.2, textColor=INK, alignment=TA_JUSTIFY, spaceAfter=6))
    base.add(ParagraphStyle("Smallx", parent=base["BodyText"], fontName="Helvetica", fontSize=8, leading=10.5, textColor=MUTED, spaceAfter=3))
    base.add(ParagraphStyle("TableHeader", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=BLUE, alignment=TA_CENTER))
    base.add(ParagraphStyle("TableCell", parent=base["BodyText"], fontName="Helvetica", fontSize=7.3, leading=8.8, textColor=INK))
    base.add(ParagraphStyle("TableRight", parent=base["TableCell"], alignment=TA_RIGHT))
    base.add(ParagraphStyle("TableCenter", parent=base["TableCell"], alignment=TA_CENTER))
    base.add(ParagraphStyle("MatrixCell", parent=base["BodyText"], fontName="Helvetica", fontSize=5.4, leading=6.2, textColor=INK, alignment=TA_CENTER))
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


def add_cover_page(canvas, doc):
    canvas.saveState()
    width, height = letter
    canvas.setFillColor(WHITE)
    canvas.rect(0, 0, width, height, fill=1, stroke=0)

    canvas.setFillColor(PAPER)
    canvas.rect(0.18 * inch, 1.0 * inch, width - 0.18 * inch, height - 2.0 * inch, fill=1, stroke=0)
    canvas.setFillColor(DEEP_BLUE)
    canvas.rect(0, 0, 0.18 * inch, height, fill=1, stroke=0)
    canvas.setFillColor(TEAL)
    canvas.rect(0.18 * inch, 0, 0.05 * inch, height, fill=1, stroke=0)
    canvas.setFillColor(GOLD)
    canvas.rect(0.72 * inch, height - 1.42 * inch, 1.65 * inch, 0.06 * inch, fill=1, stroke=0)

    if LOGO_PATH.exists():
        canvas.drawImage(str(LOGO_PATH), 0.72 * inch, height - 1.04 * inch, width=1.85 * inch, height=0.62 * inch, preserveAspectRatio=True, mask="auto")
    canvas.setFont("Helvetica", 8.2)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.72 * inch, height - 1.22 * inch, "Superintendencia de Electricidad")

    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(DEEP_BLUE)
    canvas.drawRightString(width - 0.72 * inch, height - 0.72 * inch, "Valoración preliminar" if getattr(doc, "preliminary", False) else "Valoración completa")
    canvas.setFillColor(MUTED)
    canvas.drawRightString(width - 0.72 * inch, height - 0.92 * inch, f"Generado: {getattr(doc, 'generated_at', '')}")

    canvas.setStrokeColor(colors.HexColor("#d8e7e5"))
    canvas.setLineWidth(0.7)
    canvas.line(0.72 * inch, height - 1.52 * inch, width - 0.72 * inch, height - 1.52 * inch)

    canvas.setFillColor(WHITE)
    canvas.roundRect(0.72 * inch, 1.22 * inch, width - 1.44 * inch, 5.62 * inch, 12, fill=1, stroke=0)
    canvas.setStrokeColor(colors.HexColor("#dce9e7"))
    canvas.roundRect(0.72 * inch, 1.22 * inch, width - 1.44 * inch, 5.62 * inch, 12, fill=0, stroke=1)

    canvas.setFillColor(colors.HexColor("#fbfdfd"))
    canvas.roundRect(0.88 * inch, 1.48 * inch, 3.05 * inch, 0.72 * inch, 8, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#eaf4f2"))
    canvas.roundRect(width - 2.15 * inch, 1.48 * inch, 1.45 * inch, 1.45 * inch, 18, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#dcece9"))
    canvas.circle(width - 0.72 * inch, 0.88 * inch, 0.48 * inch, fill=1, stroke=0)

    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(width - doc.rightMargin, 0.37 * inch, f"Página {doc.page}")
    canvas.restoreState()


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
    story = [Spacer(1, 1.48 * inch)]
    story.append(Paragraph("REPORTE GENERAL DE EVALUACIÓN CURRICULAR", styles["CoverEyebrow"]))
    story.append(Paragraph("Evaluación comparativa de participantes", styles["CoverTitle"]))
    story.append(AccentRule())
    story.append(Spacer(1, 0.16 * inch))
    story.append(Paragraph(f"Perfil evaluado: {safe(template.name)}", styles["CoverProfile"]))
    intro = (
        "Este informe presenta una lectura comparativa del concurso a partir de la estructura de evaluación definida para el perfil, "
        "los participantes registrados y las puntuaciones disponibles. Su propósito es facilitar una revisión ejecutiva, clara y "
        "ordenada de fortalezas, brechas y resultados preliminares o finales según el estado de avance de la evaluación."
    )
    intro_box = Table([[Paragraph(intro, styles["CoverIntro"])]], colWidths=[5.35 * inch], hAlign="LEFT")
    intro_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), WHITE),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(intro_box)
    story.append(Spacer(1, 0.22 * inch))
    if preliminary:
        note_box = Table(
            [[Paragraph("Valoración preliminar: existen criterios o participantes con puntuaciones pendientes. Las conclusiones deben leerse como una fotografía de avance y no como cierre definitivo del proceso.", styles["Note"])]],
            colWidths=[5.9 * inch],
            hAlign="LEFT",
        )
        note_box.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(note_box)
        story.append(Spacer(1, 0.08 * inch))

    kpis = [
        [Paragraph(str(len(candidates)), styles["CoverKpi"]), Paragraph(str(len(template.categories)), styles["CoverKpi"]), Paragraph(str(len(template.criteria)), styles["CoverKpi"])],
        [Paragraph("Participantes", styles["CoverKpiLabel"]), Paragraph("Categorías", styles["CoverKpiLabel"]), Paragraph("Criterios", styles["CoverKpiLabel"])],
    ]
    kpi_table = Table(kpis, colWidths=[1.52 * inch, 1.52 * inch, 1.52 * inch], hAlign="LEFT")
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
        ("BOX", (0, 0), (-1, -1), 0.7, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, WHITE),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 0.8 * inch))
    footer_card = Table(
        [[
            Paragraph("Documento de apoyo a la decisión", styles["CoverFooterTitle"]),
            Paragraph("Uso institucional · Evaluación curricular comparativa", styles["CoverFooterText"]),
        ]],
        colWidths=[2.35 * inch, 3.1 * inch],
        hAlign="LEFT",
    )
    footer_card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), WHITE),
        ("BOX", (0, 0), (-1, -1), 0.45, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.append(footer_card)
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
            rows.append([Paragraph(safe(criterion.aspect), styles["TableCell"]), Paragraph(weight, styles["TableCenter"])])
        block = [
            Paragraph(f"{safe(category.name)} · {percent(category.weight)} del perfil", styles["H2x"]),
            table(rows, [5.55 * inch, 0.95 * inch]),
            Spacer(1, 0.08 * inch),
        ]
        story.append(KeepTogether(block))
    story.append(PageBreak())
    return story


def participant_directory_story(styles, candidates: list[Candidate], aliases: dict[int, str]):
    story = [Paragraph("Identificación de participantes", styles["H1x"])]
    if not candidates:
        story.append(Paragraph("No hay participantes registrados para este perfil.", styles["Bodyx"]))
        story.append(PageBreak())
        return story
    story.append(Paragraph(
        "Para facilitar la lectura de tablas comparativas donde los participantes aparecen como columnas, el informe utiliza referencias abreviadas. La correspondencia completa se presenta en esta sección.",
        styles["Bodyx"],
    ))
    rows = [[Paragraph("Referencia", styles["TableHeader"]), Paragraph("Participante", styles["TableHeader"])]]
    for candidate in candidates:
        rows.append([Paragraph(aliases[candidate.id], styles["TableCell"]), Paragraph(safe(candidate.name), styles["TableCell"])])
    story.append(table(rows, [1.2 * inch, 5.1 * inch]))
    story.append(PageBreak())
    return story


def ranking_story(styles, template: Template, summaries: list[dict], preliminary: bool, candidates: list[Candidate]):
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
    candidate_names = candidate_name_by_id(candidates)
    rows = [[Paragraph("Pos.", styles["TableHeader"]), Paragraph("Participante", styles["TableHeader"]), Paragraph("Resultado", styles["TableHeader"]), Paragraph("Visual", styles["TableHeader"]), Paragraph("Lectura", styles["TableHeader"])]]
    for index, summary in enumerate(summaries, start=1):
        rec = clean_recommendation(summary["recommendation"])
        rows.append([
            Paragraph(str(index), styles["TableCell"]),
            Paragraph(safe(candidate_names.get(summary["id"], "Participante")), styles["TableCell"]),
            Paragraph(percent(summary["global_score"]), styles["TableCenter"]),
            HorizontalBar(summary["global_score"], fill=recommendation_color(summary["recommendation"])),
            Paragraph(safe(rec), styles["TableCell"]),
        ])
    story.append(table(rows, [0.35 * inch, 1.75 * inch, 0.68 * inch, 2.1 * inch, 1.25 * inch]))
    story.append(Spacer(1, 0.08 * inch))
    story.append(Paragraph(
        f"El mejor resultado registrado corresponde a {safe(candidate_names.get(leader['id'], 'el participante con mayor puntuación'))}, con {percent(leader['global_score'])}. Esta posición refleja la suma ponderada de los criterios evaluados y debe complementarse con la revisión cualitativa del expediente.",
        styles["Bodyx"],
    ))
    story.append(PageBreak())
    return story


def category_matrix_story(styles, template: Template, summaries: list[dict], candidates: list[Candidate]):
    story = [Paragraph("Comparación por categoría", styles["H1x"])]
    if not summaries:
        return story
    candidate_names = candidate_name_by_id(candidates)
    categories = [category.name for category in template.categories]
    header = [Paragraph("Participante", styles["TableHeader"])] + [Paragraph(safe(category), styles["TableHeader"]) for category in categories] + [Paragraph("Global", styles["TableHeader"])]
    rows = [header]
    for summary in summaries:
        rows.append(
            [Paragraph(safe(candidate_names.get(summary["id"], "Participante")), styles["TableCell"])]
            + [Paragraph(percent(summary["categories"].get(category, 0)), styles["TableCenter"]) for category in categories]
            + [Paragraph(percent(summary["global_score"]), styles["TableCenter"])]
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


def criterion_matrix_story(styles, candidates: list[Candidate], criteria: list[Criterion], aliases: dict[int, str]):
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
        "El siguiente cuadro se limita a los criterios que tienen al menos una puntuación registrada. Los criterios ponderados se expresan de 0 a 5; los requisitos críticos se muestran como Cumple o No cumple.",
        styles["Bodyx"],
    ))
    story.append(Paragraph("Nota: el guion (-) indica que el criterio está pendiente de evaluación para ese participante.", styles["Smallx"]))
    for category in dict.fromkeys(criterion.category for criterion in evaluated):
        children = [criterion for criterion in evaluated if criterion.category == category]
        header = [Paragraph("Criterio", styles["TableHeader"]), Paragraph("Peso global", styles["TableHeader"])] + [Paragraph(aliases[candidate.id], styles["TableHeader"]) for candidate in candidates]
        rows = [header]
        for criterion in children:
            rows.append(
                [Paragraph(safe(criterion.aspect), styles["TableCell"]), Paragraph("Crítico" if criterion.is_critical else percent(criterion_global_weight(criterion)), styles["TableCenter"])]
                + [Paragraph(criterion_score_text(criterion, score_maps[candidate.id].get(criterion.id).score if score_maps[candidate.id].get(criterion.id) else None, "-"), styles["MatrixCell"]) for candidate in candidates]
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
                block.append(Paragraph(f"• {safe(criterion.aspect)} ({criterion_score_text(criterion, score.score)}).", styles["Smallx"]))
        else:
            block.append(Paragraph("No se identifican todavía criterios con puntuación alta registrada.", styles["Smallx"]))
        if gaps:
            block.append(Paragraph("Puntos a revisar o fortalecer:", styles["Smallx"]))
            for score, criterion in gaps:
                block.append(Paragraph(f"• {safe(criterion.aspect)} ({criterion_score_text(criterion, score.score)}).", styles["Smallx"]))
        else:
            block.append(Paragraph("No se registran brechas marcadas entre los criterios evaluados hasta el momento.", styles["Smallx"]))
        if candidate.comments:
            block.append(Paragraph(f"Observación general registrada: {safe(candidate.comments)}", styles["Smallx"]))
        block.append(Spacer(1, 0.08 * inch))
        story.append(KeepTogether(block))
    return story


def synthesis_story(styles, template: Template, summaries: list[dict], preliminary: bool, narrative: dict | None = None):
    story = [PageBreak(), Paragraph("Síntesis interpretativa", styles["H1x"])]
    ai_synthesis = narrative_paragraphs(narrative, "synthesis")
    ai_conclusion = narrative_paragraphs(narrative, "conclusion")
    if ai_synthesis:
        for text in ai_synthesis:
            story.append(Paragraph(safe(text), styles["Bodyx"]))
        story.append(Paragraph("Conclusión", styles["H1x"]))
        for text in ai_conclusion or ["La conclusión debe completarse con la validación humana responsable del proceso." ]:
            story.append(Paragraph(safe(text), styles["Bodyx"]))
        return story

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
    story.append(Paragraph("Conclusión", styles["H1x"]))
    story.append(Paragraph(
        "La lectura final debe integrarse con la verificación documental, las entrevistas, las pruebas técnicas y cualquier validación institucional que corresponda al proceso.",
        styles["Bodyx"],
    ))
    return story


def general_report_source_text(template: Template, candidates: list[Candidate], criteria: list[Criterion]) -> str:
    candidates = sorted(candidates, key=lambda candidate: candidate.name)
    summaries = [summarize_candidate(candidate, criteria, template) for candidate in candidates]
    summaries.sort(key=lambda row: row["global_score"], reverse=True)
    preliminary = has_pending_scores(candidates, criteria)
    candidate_names = candidate_name_by_id(candidates)
    score_maps: dict[int, dict[int, Score]] = {
        candidate.id: {score.criterion_id: score for score in candidate.scores}
        for candidate in candidates
    }
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


def build_template_general_report(template: Template, candidates: list[Candidate], criteria: list[Criterion], narrative: dict | None = None) -> BytesIO:
    styles = make_styles()
    candidates = sorted(candidates, key=lambda candidate: candidate.name)
    aliases = participant_aliases(candidates)
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
    doc.preliminary = preliminary
    doc.generated_at = datetime.now().strftime("%d/%m/%Y · %H:%M")
    story = []
    story.extend(cover_story(styles, template, candidates, preliminary))
    story.extend(participant_directory_story(styles, candidates, aliases))
    story.extend(structure_story(styles, template, criteria))
    story.extend(ranking_story(styles, template, summaries, preliminary, candidates))
    story.extend(category_matrix_story(styles, template, summaries, candidates))
    story.extend(criterion_matrix_story(styles, candidates, criteria, aliases))
    story.extend(participant_profiles_story(styles, candidates, criteria))
    story.extend(synthesis_story(styles, template, summaries, preliminary, narrative))
    doc.build(story, onFirstPage=add_cover_page, onLaterPages=add_footer)
    buffer.seek(0)
    return buffer
