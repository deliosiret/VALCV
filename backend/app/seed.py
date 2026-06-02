from app.models import Criterion, EvaluationMode, Template


INITIAL_TEMPLATE_NAME = "Gerente de Normas Eléctricas"
MANUAL_EVIDENCE_NOTE = "Estos tópicos deben evaluarse en la entrevista y/o examen temático"

CATEGORY_RENAMES = {
    "Gestión normativa e institucional": "Competencias normativas e institucionales",
    "Competencias estratégicas y transición energética": "Aspectos estratégicos",
    "Competencias estratégica": "Aspectos estratégicos",
}

CATEGORY_WEIGHTS = {
    "Formación académica y requisitos básicos": 0.15,
    "Experiencia profesional y liderazgo": 0.2,
    "Competencias técnicas regulatorias": 0.3,
    "Competencias normativas e institucionales": 0.2,
    "Aspectos estratégicos": 0.1,
    "Idioma inglés": 0.05,
}

CRITERION_GUIDANCE = {
    "F1": {
        "is_critical": True,
        "notes": "Requisito obligatorio. Verificar que el título profesional corresponda a Ingeniería Eléctrica, Mecánica, Electrónica, Industrial, Civil, Arquitectura u otra formación expresamente aceptada para el perfil. Si no está evidenciado, asignar 0.",
    },
    "F2": {
        "notes": "Valorar únicamente maestrías documentadas directamente relacionadas con regulación, energía, mercados eléctricos, electricidad, potencia, renovables o eficiencia energética. Cursos, diplomados, talleres, seminarios o experiencia no sustituyen una maestría; si no existe maestría documentada, asignar 0. Una maestría en curso, pendiente de tesis o sin evidencia de grado concluido no debe recibir puntuación plena. Una misma maestría solo puede usarse aquí o en otro criterio de maestría, no en ambos. Si hay varias maestrías pertinentes, premiar cantidad, nivel y pertinencia sin exceder 5.",
    },
    "F3": {
        "notes": "Valorar únicamente maestrías documentadas distintas a las usadas en criterios más específicos, relacionadas con áreas comercial, industrial, calidad, gestión, STEM o afines. Cursos, diplomados, talleres, seminarios o experiencia no sustituyen una maestría; si no existe maestría documentada, asignar 0. Una maestría en curso, pendiente de tesis o sin evidencia de grado concluido no debe recibir puntuación plena. No reutilizar una maestría ya considerada en regulación, energía o mercados eléctricos. Si solo existe la misma maestría ya aplicada a otro criterio, no sumar nuevamente.",
    },
    "F4": {
        "notes": "Valorar únicamente certificaciones profesionales o internacionales verificables en áreas del perfil. No considerar cursos, talleres, seminarios ni diplomados como certificaciones. Si el documento solo evidencia participación o capacitación sin credencial certificadora clara, asignar una puntuación conservadora. Premiar cantidad, vigencia, entidad emisora, nivel y pertinencia, sin exceder 5.",
    },
    "F5": {
        "notes": "Valorar diplomados, cursos, talleres, seminarios o programas de capacitación verificables en áreas relacionadas con el perfil. No contarlos como certificaciones internacionales o profesionales. Premiar pertinencia, duración, nivel, entidad emisora, actualidad y cantidad, sin exceder 5.",
    },
    "E1": {
        "notes": "Valorar años documentados de experiencia en el sector eléctrico. No duplicar el mismo periodo laboral para inflar años; considerar solapamientos de fechas de forma conservadora.",
    },
    "E2": {
        "notes": "Valorar experiencia técnica o regulatoria documentada. Si el mismo cargo ya fue considerado en experiencia general, puede respaldar este criterio por su naturaleza técnica, pero no debe exagerarse la puntuación si no hay funciones específicas evidenciadas.",
    },
    "E3": {
        "notes": "Valorar liderazgo, funciones normativas o regulatorias documentadas. Diferenciar liderazgo formal de participación técnica individual.",
    },
    "E4": {
        "notes": "Valorar dirección de equipos técnicos, mesas de trabajo o proyectos institucionales cuando esté evidenciado por cargos, funciones, certificaciones laborales o productos verificables.",
    },
    "I1": {
        "notes": "Valorar evidencia documental de lectura técnica en inglés, estudios, certificaciones, publicaciones, experiencia laboral o manejo de estándares internacionales.",
    },
    "I2": {
        "notes": "Valorar evidencia documental de comunicación oral o escrita en inglés técnico. Si no hay evidencia explícita, asignar puntuación conservadora.",
    },
}

CRITERION_ASPECT_UPDATES = {
    "F5": "Diplomados y cursos especializados en las áreas mencionadas más arriba",
}

INITIAL_CRITERIA = [
    ("F1", "Formación académica y requisitos básicos", "Título de Ingeniería Eléctrica/ Mecánica/ Electrónica/ Industrial/ Civil/ Arquitectura", 0.15, 0.0, 0.0, ""),
    ("F2", "Formación académica y requisitos básicos", "Maestría en regulación, energía, mercados eléctricos o áreas afines", 0.15, 0.4, 0.06, ""),
    ("F3", "Formación académica y requisitos básicos", "Maestría en áreas comercial, industrial, calidad y afines", 0.15, 0.3, 0.045, ""),
    ("F4", "Formación académica y requisitos básicos", "Certificaciones internacionales en las áreas mencionadas más arriba", 0.15, 0.2, 0.03, ""),
    ("F5", "Formación académica y requisitos básicos", "Diplomados y cursos especializados en las áreas mencionadas más arriba", 0.15, 0.1, 0.015, ""),
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
    ("G1", "Competencias normativas e institucionales", "Elaboración, revisión y actualización de normas técnicas", 0.2, 0.25, 0.05, "Estos tópicos deben evaluarse en la entrevista y/o examen temático, contrastable con certificación laboral"),
    ("G2", "Competencias normativas e institucionales", "Supervisión técnica de diseños eléctricos", 0.2, 0.15, 0.03, ""),
    ("G3", "Competencias normativas e institucionales", "Coordinación con Legal para resoluciones y propuestas normativas", 0.2, 0.15, 0.03, ""),
    ("G4", "Competencias normativas e institucionales", "Representación institucional ante organismos nacionales e internacionales", 0.2, 0.15, 0.03, ""),
    ("G5", "Competencias normativas e institucionales", "Consultas públicas, observaciones regulatorias y relación con regulados", 0.2, 0.15, 0.03, ""),
    ("G6", "Competencias normativas e institucionales", "Gestión de proyectos normativos y planificación regulatoria", 0.2, 0.15, 0.03, ""),
    ("S1", "Aspectos estratégicos", "Visión estratégica sobre modernización del subsector eléctrico", 0.1, 0.25, 0.025, ""),
    ("S2", "Aspectos estratégicos", "Anticipación de impactos regulatorios de nuevas tecnologías", 0.1, 0.25, 0.025, ""),
    ("S3", "Aspectos estratégicos", "Alineación normativa con mejores prácticas internacionales", 0.1, 0.25, 0.025, ""),
    ("S4", "Aspectos estratégicos", "Propuestas de mejora regulatoria viables y aplicables", 0.1, 0.25, 0.025, ""),
    ("I1", "Idioma inglés", "Comprensión lectora de documentos técnicos y estándares internacionales", 0.05, 0.5, 0.025, ""),
    ("I2", "Idioma inglés", "Comunicación oral y escrita en inglés técnico", 0.05, 0.5, 0.025, ""),
]


def sync_initial_template_guidance(db, template):
    changed = False
    for category in template.categories:
        new_name = CATEGORY_RENAMES.get(category.name, category.name)
        if category.name != new_name:
            category.name = new_name
            changed = True
        expected_weight = CATEGORY_WEIGHTS.get(category.name)
        if expected_weight is not None and category.weight != expected_weight:
            category.weight = expected_weight
            changed = True

    for criterion in template.criteria:
        new_category = CATEGORY_RENAMES.get(criterion.category, criterion.category)
        if criterion.category != new_category:
            criterion.category = new_category
            changed = True
        expected_category_weight = CATEGORY_WEIGHTS.get(criterion.category)
        if expected_category_weight is not None and criterion.category_weight != expected_category_weight:
            criterion.category_weight = expected_category_weight
            criterion.global_weight = 0 if criterion.is_critical else criterion.category_weight * criterion.within_category_weight
            changed = True
        guidance = CRITERION_GUIDANCE.get(criterion.code)
        expected_aspect = CRITERION_ASPECT_UPDATES.get(criterion.code)
        if expected_aspect and criterion.aspect != expected_aspect:
            criterion.aspect = expected_aspect
            changed = True
        if guidance:
            expected_notes = guidance.get("notes", criterion.notes)
            if criterion.notes != expected_notes:
                criterion.notes = expected_notes
                changed = True
            expected_critical = bool(guidance.get("is_critical", criterion.is_critical))
            if criterion.is_critical != expected_critical:
                criterion.is_critical = expected_critical
                criterion.within_category_weight = 0.0 if expected_critical else criterion.within_category_weight
                criterion.global_weight = 0.0 if expected_critical else criterion.category_weight * criterion.within_category_weight
                changed = True

    present_categories = [category for category in template.categories if any(criterion.category == category.name for criterion in template.criteria)]
    present_total = sum(float(category.weight or 0) for category in present_categories)
    if present_categories and abs(present_total - 1.0) > 0.0001:
        for category in present_categories:
            category.weight = float(category.weight or 0) / present_total
            changed = True

    category_weights = {category.name: float(category.weight or 0) for category in template.categories}
    for criterion in template.criteria:
        resolved_category_weight = category_weights.get(criterion.category, criterion.category_weight)
        if abs(float(criterion.category_weight or 0) - resolved_category_weight) > 0.0001:
            criterion.category_weight = resolved_category_weight
            criterion.global_weight = 0.0 if criterion.is_critical else criterion.category_weight * criterion.within_category_weight
            changed = True
    if changed:
        db.commit()


def seed_initial_template(db):
    exists = db.query(Template).filter(Template.name == INITIAL_TEMPLATE_NAME).first()
    if exists:
        sync_initial_template_guidance(db, exists)
        return

    template = Template(
        name=INITIAL_TEMPLATE_NAME,
        description="Perfil con los criterios predefinidos.",
    )
    db.add(template)
    db.flush()

    automatic_codes = {"F1", "F2", "F3", "F4", "F5", "E1", "E2", "E3", "E4", "I1", "I2"}
    for idx, (code, category, aspect, category_weight, within_weight, global_weight, notes) in enumerate(INITIAL_CRITERIA):
        evaluation_mode = EvaluationMode.automatic if code in automatic_codes else EvaluationMode.manual
        guidance = CRITERION_GUIDANCE.get(code, {})
        is_critical = bool(guidance.get("is_critical", False))
        resolved_notes = str(guidance.get("notes", notes))
        db.add(
            Criterion(
                template_id=template.id,
                code=code,
                category=category,
                aspect=aspect,
                category_weight=category_weight,
                within_category_weight=0.0 if is_critical else within_weight,
                global_weight=0.0 if is_critical else global_weight,
                scale="0 a 5",
                notes=resolved_notes,
                is_critical=is_critical,
                evaluation_mode=evaluation_mode,
                order_index=idx,
            )
        )
    db.commit()
