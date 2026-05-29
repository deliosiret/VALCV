from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from io import BytesIO

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from app.models import Candidate, Criterion, Template
from app.scoring import summarize_candidate


BLUE = RGBColor(37, 70, 74)
TEAL = RGBColor(22, 105, 122)


def percent(value: float) -> str:
    return f"{round((value or 0) * 100, 2)}%"


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def set_cell_text(cell, text: str, bold: bool = False) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.name = "Aptos"
    run.font.size = Pt(9)
    paragraph.paragraph_format.space_after = Pt(0)


def heading(document: Document, text: str, level: int = 1):
    paragraph = document.add_heading(text, level=level)
    for run in paragraph.runs:
        run.font.name = "Aptos Display"
        run.font.color.rgb = BLUE if level == 1 else TEAL
    return paragraph


def body(document: Document, text: str):
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(5)
    run = paragraph.add_run(text)
    run.font.name = "Aptos"
    run.font.size = Pt(10.5)
    return paragraph


def add_key_value_table(document: Document, rows: list[tuple[str, str]]) -> None:
    table = document.add_table(rows=0, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    for label, value in rows:
        cells = table.add_row().cells
        set_cell_text(cells[0], label, True)
        set_cell_text(cells[1], value)
        set_cell_shading(cells[0], "DCECEA")
    document.add_paragraph()


def add_table(document: Document, headers: list[str], rows: list[list[str]]) -> None:
    table = document.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    for index, header in enumerate(headers):
        set_cell_text(table.rows[0].cells[index], header, True)
        set_cell_shading(table.rows[0].cells[index], "DCECEA")
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            set_cell_text(cells[index], value)
    document.add_paragraph()


def configure(document: Document) -> None:
    styles = document.styles
    styles["Normal"].font.name = "Aptos"
    styles["Normal"].font.size = Pt(10.5)
    section = document.sections[0]
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("Reporte de evaluación curricular - VALCV")
    run.font.name = "Aptos"
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor(100, 100, 100)


def build_candidate_report(candidate: Candidate, template: Template, criteria: list[Criterion]) -> BytesIO:
    document = Document()
    configure(document)
    summary = summarize_candidate(candidate, criteria)
    score_by_criterion = {score.criterion_id: score for score in candidate.scores}
    file_names = {file.id: file.original_name for file in candidate.files}

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("Reporte de evaluación curricular")
    run.bold = True
    run.font.name = "Aptos Display"
    run.font.size = Pt(20)
    run.font.color.rgb = BLUE

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run(candidate.name)
    run.bold = True
    run.font.name = "Aptos"
    run.font.size = Pt(14)
    run.font.color.rgb = TEAL

    heading(document, "Datos generales")
    add_key_value_table(
        document,
        [
            ("Candidato", candidate.name),
            ("Cédula / ID", candidate.document_id or "No registrado"),
            ("Plantilla utilizada", template.name),
            ("Evaluador asignado", candidate.evaluator or "No registrado"),
            ("Fecha de generación", datetime.now().strftime("%d/%m/%Y %H:%M")),
            ("Resultado global", percent(summary["global_score"])),
            ("Recomendación", summary["recommendation"]),
        ],
    )

    heading(document, "Resumen de resultados")
    category_rows = []
    for category in template.categories:
        category_rows.append([category.name, percent(category.weight), percent(summary["categories"].get(category.name, 0))])
    add_table(document, ["Categoría", "Peso en plantilla", "Resultado obtenido"], category_rows)

    heading(document, "Ponderaciones y criterios utilizados")
    grouped: dict[str, list[Criterion]] = defaultdict(list)
    for criterion in criteria:
        grouped[criterion.category].append(criterion)
    rows = []
    for category in template.categories:
        for criterion in grouped.get(category.name, []):
            rows.append(
                [
                    category.name,
                    percent(category.weight),
                    criterion.aspect,
                    "Crítico" if criterion.is_critical else percent(criterion.within_category_weight),
                    "IA" if criterion.evaluation_mode == "automatic" else "Manual",
                    criterion.notes or "",
                ]
            )
    add_table(document, ["Categoría", "Peso categoría", "Criterio", "Peso criterio", "Modo", "Notas de evaluación"], rows)

    heading(document, "Detalle de evaluación")
    detail_rows = []
    for criterion in criteria:
        score = score_by_criterion.get(criterion.id)
        references = ", ".join(file_names.get(file_id, str(file_id)) for file_id in (score.file_ids if score else []))
        detail_rows.append(
            [
                criterion.category,
                criterion.aspect,
                "Crítico" if criterion.is_critical else ("IA" if criterion.evaluation_mode == "automatic" else "Manual"),
                f"{score.score:.1f}/5" if score else "Sin evaluar",
                score.rationale if score else "",
                score.evaluator_note if score else "",
                references,
            ]
        )
    add_table(document, ["Categoría", "Criterio", "Tipo", "Puntuación", "Evidencia / justificación", "Nota del evaluador", "Documentos"], detail_rows)

    heading(document, "Observaciones generales")
    body(document, candidate.comments or "No se registraron observaciones generales para este candidato.")

    heading(document, "Conclusión")
    body(
        document,
        f"Con base en la plantilla \"{template.name}\", el candidato obtiene un resultado global de {percent(summary['global_score'])}. "
        f"La recomendación generada por el sistema es: {summary['recommendation']}. "
        "Este reporte resume la evaluación registrada en la plataforma y debe interpretarse como soporte documental para la revisión humana y la decisión institucional correspondiente.",
    )

    buffer = BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return buffer
