from app.models import Criterion, EvaluationMode, Template


INITIAL_TEMPLATE_NAME = "Gerente de Normas Eléctricas"
MANUAL_EVIDENCE_NOTE = "Estos tópicos deben evaluarse en la entrevista y/o examen temático"

INITIAL_CRITERIA = [
    ("F1", "Formación académica y requisitos básicos", "Título de Ingeniería Eléctrica/ Mecánica/ Electrónica/ Industrial/ Civil/ Arquitectura", 0.15, 0.0, 0.0, ""),
    ("F2", "Formación académica y requisitos básicos", "Maestría en regulación, energía, mercados eléctricos o áreas afines", 0.15, 0.4, 0.06, ""),
    ("F3", "Formación académica y requisitos básicos", "Maestría en áreas comercial, industrial, calidad y afines", 0.15, 0.3, 0.045, ""),
    ("F4", "Formación académica y requisitos básicos", "Certificaciones internacionales en las áreas mencionadas más arriba", 0.15, 0.2, 0.03, ""),
    ("F5", "Formación académica y requisitos básicos", "Diplomados en las áreas mencionadas más arriba", 0.15, 0.1, 0.015, ""),
    ("E1", "Experiencia profesional y liderazgo", "Mínimo 5 años de experiencia en el sector eléctrico", 0.2, 0.3, 0.06, ""),
    ("E2", "Experiencia profesional y liderazgo", "Al menos 3 años de experiencia en posiciones técnicas o regulatorias", 0.2, 0.2, 0.04, ""),
    ("E3", "Experiencia profesional y liderazgo", "Al menos 3 años en funciones de liderazgo, normativa o regulación", 0.2, 0.3, 0.06, ""),
    ("E4", "Experiencia profesional y liderazgo", "Dirección de equipos técnicos, mesas de trabajo o proyectos institucionales", 0.2, 0.2, 0.04, ""),
    ("T1", "Competencias técnicas regulatorias", "Regulación de mercados eléctricos mayoristas y minoristas", 0.3, 0.15, 0.045, "Estos tópicos deben evaluarse en la entrevista y/o examen temático"),
    ("T2", "Competencias técnicas regulatorias", "Calidad de servicio técnico y comercial", 0.3, 0.15, 0.045, ""),
    ("T3", "Competencias técnicas regulatorias", "Generación distribuida y autoproducción", 0.3, 0.15, 0.045, ""),
    ("T4", "Competencias técnicas regulatorias", "Energías renovables y eficiencia energética", 0.3, 0.15, 0.045, ""),
    ("T5", "Competencias técnicas regulatorias", "Electromovilidad, almacenamiento energético y nuevas tecnologías", 0.3, 0.1, 0.03, ""),
    ("T6", "Competencias técnicas regulatorias", "Análisis regulatorio comparado", 0.3, 0.1, 0.03, ""),
    ("T7", "Competencias técnicas regulatorias", "Redacción normativa, técnica y regulatoria", 0.3, 0.15, 0.045, ""),
    ("T8", "Competencias técnicas regulatorias", "Estándares y buenas prácticas internacionales", 0.3, 0.05, 0.015, ""),
    ("G1", "Gestión normativa e institucional", "Elaboración, revisión y actualización de normas técnicas", 0.2, 0.25, 0.05, "Estos tópicos deben evaluarse en la entrevista y/o examen temático, contrastable con certificación laboral"),
    ("G2", "Gestión normativa e institucional", "Supervisión técnica de diseños eléctricos", 0.2, 0.15, 0.03, ""),
    ("G3", "Gestión normativa e institucional", "Coordinación con Legal para resoluciones y propuestas normativas", 0.2, 0.15, 0.03, ""),
    ("G4", "Gestión normativa e institucional", "Representación institucional ante organismos nacionales e internacionales", 0.2, 0.15, 0.03, ""),
    ("G5", "Gestión normativa e institucional", "Consultas públicas, observaciones regulatorias y relación con regulados", 0.2, 0.15, 0.03, ""),
    ("G6", "Gestión normativa e institucional", "Gestión de proyectos normativos y planificación regulatoria", 0.2, 0.15, 0.03, ""),
    ("S1", "Competencias estratégicas y transición energética", "Visión estratégica sobre modernización del subsector eléctrico", 0.1, 0.25, 0.025, ""),
    ("S2", "Competencias estratégicas y transición energética", "Anticipación de impactos regulatorios de nuevas tecnologías", 0.1, 0.25, 0.025, ""),
    ("S3", "Competencias estratégicas y transición energética", "Alineación normativa con mejores prácticas internacionales", 0.1, 0.25, 0.025, ""),
    ("S4", "Competencias estratégicas y transición energética", "Propuestas de mejora regulatoria viables y aplicables", 0.1, 0.25, 0.025, ""),
    ("I1", "Idioma inglés", "Comprensión lectora de documentos técnicos y estándares internacionales", 0.05, 0.5, 0.025, ""),
    ("I2", "Idioma inglés", "Comunicación oral y escrita en inglés técnico", 0.05, 0.5, 0.025, ""),
]


def seed_initial_template(db):
    exists = db.query(Template).filter(Template.name == INITIAL_TEMPLATE_NAME).first()
    if exists:
        return

    template = Template(
        name=INITIAL_TEMPLATE_NAME,
        description="Plantilla inicial importada del libro de evaluación de aspirantes.",
    )
    db.add(template)
    db.flush()

    automatic_codes = {"F1", "F2", "F3", "F4", "F5", "E1", "E2", "E3", "E4", "I1", "I2"}
    for idx, (code, category, aspect, category_weight, within_weight, global_weight, notes) in enumerate(INITIAL_CRITERIA):
        evaluation_mode = EvaluationMode.automatic if code in automatic_codes else EvaluationMode.manual
        db.add(
            Criterion(
                template_id=template.id,
                code=code,
                category=category,
                aspect=aspect,
                category_weight=category_weight,
                within_category_weight=within_weight,
                global_weight=global_weight,
                scale="0 a 5",
                notes=notes,
                evaluation_mode=evaluation_mode,
                order_index=idx,
            )
        )
    db.commit()
