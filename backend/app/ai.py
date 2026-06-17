import json
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types

from app.models import Candidate, Criterion


AI_EVALUATION_POLICY = """
Reglas institucionales de evaluación:
- Evalúa el expediente de forma integral, no criterio por criterio de manera aislada.
- No dupliques una misma credencial, título, maestría, postgrado, certificación, diplomado, curso, cargo o proyecto para sumar puntos adicionales en varios criterios si ya agotó su aporte principal.
- Cuando una evidencia pueda aplicar a más de un criterio, úsala en el criterio más específico o más favorable al perfil requerido y explica brevemente esa asignación.
- Una maestría no debe contarse dos veces. Si una misma maestría cumple un criterio especializado, no debe volver a sumar como maestría genérica, salvo que exista otra maestría distinta documentada.
- No sustituyas un requisito de maestría con cursos, diplomados, talleres, seminarios o experiencia laboral. Si el criterio pide maestría y no hay maestría documentada, asigna 0.
- Si una maestría está en curso, pendiente de tesis, pendiente de grado o no consta como concluida, no debe recibir puntuación plena; usa una puntuación conservadora según avance y pertinencia.
- Diferencia estrictamente las certificaciones internacionales o profesionales de la formación complementaria. Una certificación normalmente acredita competencia mediante una entidad certificadora, examen, licencia, estándar, credencial profesional o certificado formal de competencia; no trates cursos, talleres, seminarios ni diplomados como certificaciones internacionales.
- Los diplomados, cursos, talleres y seminarios pueden valorarse juntos como formación complementaria cuando el criterio así lo indique. Premia cantidad, nivel, duración, pertinencia, actualidad y entidad emisora solo mientras exista margen de puntuación en el criterio correspondiente. Nunca excedas 5 puntos por criterio.
- Para formación, prioriza relevancia regulatoria, eléctrica, energética y de mercados eléctricos sobre formación genérica de otros sectores.
- Si la evidencia es ambigua, incompleta o no verificable en los documentos, asigna una puntuación conservadora y explícalo.
""".strip()
SUPPORTED_DOCUMENT_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}


GEMINI_PRICING_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing"
GEMINI_PRICING_REFERENCE_DATE = "2026-06-03"
GEMINI_STANDARD_PRICING_USD_PER_1M = {
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00, "cache": 0.15},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50, "cache": 0.025},
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00, "cache": 0.20},
    "gemini-3-flash-preview": {"input": 0.50, "output": 3.00, "cache": 0.05},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "cache": 0.125},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cache": 0.03},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "cache": 0.01},
}


@dataclass
class GeminiCallResult:
    payload: dict
    prompt_text: str
    response_text: str
    input_tokens: int
    cached_input_tokens: int
    billable_input_tokens: int
    output_text_tokens: int
    output_tokens: int
    thinking_tokens: int
    total_tokens: int
    input_cost_usd: float
    cache_cost_usd: float
    output_cost_usd: float
    thinking_cost_usd: float
    total_cost_usd: float
    pricing_input_usd_per_1m: float
    pricing_cache_usd_per_1m: float
    pricing_output_usd_per_1m: float
    cached_content_name: str = ""
    pricing_source: str = GEMINI_PRICING_SOURCE
    pricing_reference_date: str = GEMINI_PRICING_REFERENCE_DATE


def model_pricing(model: str) -> dict[str, float]:
    return GEMINI_STANDARD_PRICING_USD_PER_1M.get(model, {"input": 0.0, "output": 0.0, "cache": 0.0})


def response_token_usage(response) -> tuple[int, int, int, int, int, int]:
    usage = getattr(response, "usage_metadata", None)
    input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
    cached_input_tokens = int(getattr(usage, "cached_content_token_count", 0) or 0)
    candidates_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
    thinking_tokens = int(getattr(usage, "thoughts_token_count", 0) or 0)
    total_tokens = int(getattr(usage, "total_token_count", input_tokens + candidates_tokens + thinking_tokens) or 0)
    output_tokens = candidates_tokens + thinking_tokens
    if output_tokens == 0 and total_tokens > input_tokens:
        output_tokens = total_tokens - input_tokens
    billable_input_tokens = max(input_tokens - cached_input_tokens, 0)
    return input_tokens, cached_input_tokens, billable_input_tokens, candidates_tokens, output_tokens, thinking_tokens, total_tokens


def gemini_result(model: str, prompt_text: str, response, cached_content_name: str = "") -> GeminiCallResult:
    response_text = response.text or "{}"
    input_tokens, cached_input_tokens, billable_input_tokens, output_text_tokens, output_tokens, thinking_tokens, total_tokens = response_token_usage(response)
    pricing = model_pricing(model)
    input_cost = billable_input_tokens * pricing["input"] / 1_000_000
    cache_cost = cached_input_tokens * pricing["cache"] / 1_000_000
    output_cost = output_tokens * pricing["output"] / 1_000_000
    thinking_cost = thinking_tokens * pricing["output"] / 1_000_000
    return GeminiCallResult(
        payload=_extract_json(response_text),
        prompt_text=prompt_text,
        response_text=response_text,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        billable_input_tokens=billable_input_tokens,
        output_text_tokens=output_text_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        total_tokens=total_tokens,
        input_cost_usd=input_cost,
        cache_cost_usd=cache_cost,
        output_cost_usd=output_cost,
        thinking_cost_usd=thinking_cost,
        total_cost_usd=input_cost + cache_cost + output_cost,
        pricing_input_usd_per_1m=pricing["input"],
        pricing_cache_usd_per_1m=pricing["cache"],
        pricing_output_usd_per_1m=pricing["output"],
        cached_content_name=cached_content_name,
    )


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def generate_config(model: str, cached_content_name: str | None = None) -> types.GenerateContentConfig:
    model_name = model.lower()
    thinking_config = None
    if model_name.startswith("gemini-3"):
        thinking_config = types.ThinkingConfig(thinking_level="low")
    elif model_name.startswith(("gemini-2.5-flash", "gemini-2.5-flash-lite")):
        thinking_config = types.ThinkingConfig(thinking_budget=0)
    config = {
        "response_mime_type": "application/json",
        "thinking_config": thinking_config,
    }
    if cached_content_name:
        config["cached_content"] = cached_content_name
    return types.GenerateContentConfig(**config)


def candidate_document_signature(candidate: Candidate) -> str:
    source = "|".join(
        f"{file.id}:{file.stored_name}:{file.size_bytes}:{file.created_at.isoformat() if file.created_at else ''}"
        for file in sorted(candidate.files, key=lambda item: item.id)
    )
    import hashlib

    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def candidate_document_parts(candidate: Candidate, upload_dir: str) -> list:
    parts: list = []
    for file in candidate.files:
        path = Path(upload_dir) / file.stored_name
        mime_type = file.mime_type or mimetypes.guess_type(file.original_name)[0] or "application/octet-stream"
        if mime_type not in SUPPORTED_DOCUMENT_MIME_TYPES:
            continue
        parts.append(types.Part.from_text(text=f"Documento disponible: id={file.id}, nombre={file.original_name}"))
        parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type))
    return parts


def create_candidate_context_cache(
    candidate: Candidate,
    upload_dir: str,
    api_key: str | None,
    model: str,
    ttl_seconds: int = 3600,
) -> str:
    if not api_key:
        raise RuntimeError("Configura la API key de Gemini antes de crear caché de IA.")
    client = genai.Client(api_key=api_key)
    parts: list = [
        types.Part.from_text(
            text=(
                "Expediente documental cacheado para evaluación curricular. "
                "Usa estos documentos como fuente de evidencia. Mapa de documentos: "
                + json.dumps(
                    [{"id": file.id, "name": file.original_name, "mime_type": file.mime_type} for file in candidate.files],
                    ensure_ascii=False,
                )
            )
        )
    ]
    for file in candidate.files:
        path = Path(upload_dir) / file.stored_name
        mime_type = file.mime_type or mimetypes.guess_type(file.original_name)[0] or "application/octet-stream"
        if mime_type not in SUPPORTED_DOCUMENT_MIME_TYPES or not path.exists():
            continue
        uploaded = client.files.upload(
            file=path,
            config=types.UploadFileConfig(mime_type=mime_type, display_name=f"{file.id}-{file.original_name}"),
        )
        deadline = time.time() + 45
        while getattr(getattr(uploaded, "state", None), "name", "") == "PROCESSING" and time.time() < deadline:
            time.sleep(2)
            uploaded = client.files.get(name=uploaded.name)
        parts.append(types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime_type))
    if len(parts) == 1:
        raise RuntimeError("No hay documentos compatibles para crear caché de IA.")
    cache = client.caches.create(
        model=model,
        config=types.CreateCachedContentConfig(
            display_name=f"valcv-candidate-{candidate.id}",
            system_instruction=(
                "Eres un asistente de evaluación curricular. Usa el expediente documental cacheado como evidencia. "
                "Cuando se soliciten file_ids, responde con los ids del mapa de documentos incluido en el cache."
            ),
            contents=[types.Content(role="user", parts=parts)],
            ttl=f"{ttl_seconds}s",
        ),
    )
    return cache.name


def evaluate_candidate_with_gemini(
    candidate: Candidate,
    criteria: list[Criterion],
    upload_dir: str,
    api_key: str | None,
    model: str,
    cached_content_name: str | None = None,
) -> GeminiCallResult:
    if not api_key:
        raise RuntimeError("Configura la API key de Gemini antes de evaluar con IA.")
    if not candidate.files:
        raise RuntimeError("El candidato no tiene archivos de expediente cargados.")

    client = genai.Client(api_key=api_key)
    rubric = [
        {
            "id": criterion.id,
            "code": criterion.code,
            "category": criterion.category,
            "aspect": criterion.aspect,
            "scale": criterion.scale,
            "notes": criterion.notes,
            "is_critical": criterion.is_critical,
        }
        for criterion in criteria
    ]
    prompt = (
        "Evalúa el expediente del candidato para un perfil curricular. "
        "Devuelve únicamente JSON válido con la forma "
        '{"scores":[{"criterion_id":number,"score":number,"rationale":"texto breve","file_ids":[number]}]}. '
        "En file_ids incluye el id de cada documento usado como referencia para ese criterio; si no usaste evidencia documental, usa []. "
        "La puntuación va de 0 a 5: 5 excelente, 4 muy bueno, 3 aceptable, "
        "2 débil, 1 deficiente, 0 no evidenciado. Si no hay evidencia documental, usa 0 o 1. "
        "Si is_critical es true, evalúa el criterio como cumple/no cumple: usa score 5 solamente si cumple plenamente "
        "el requisito excluyente, y score 0 si no cumple, no está evidenciado o solo cumple parcialmente. "
        "Usa notes como instrucciones específicas de evaluación para cada criterio. "
        f"{AI_EVALUATION_POLICY} "
        f"Candidato: {candidate.name}. Criterios automáticos: {json.dumps(rubric, ensure_ascii=False)}"
    )

    if cached_content_name:
        prompt = (
            "Usa el expediente documental cacheado asociado a esta solicitud. "
            "Devuelve file_ids usando el mapa de documentos cacheado. "
            + prompt
        )
    parts: list = [types.Part.from_text(text=prompt)]
    if not cached_content_name:
        parts.extend(candidate_document_parts(candidate, upload_dir))

    contents = [types.Content(role="user", parts=parts)]
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=generate_config(model, cached_content_name),
    )
    return gemini_result(model, prompt, response, cached_content_name or "")


def evaluate_candidate_bonus_with_gemini(
    candidate: Candidate,
    criteria: list[Criterion],
    evaluation_snapshot: list[dict],
    upload_dir: str,
    api_key: str | None,
    model: str,
    cached_content_name: str | None = None,
) -> GeminiCallResult:
    if not api_key:
        raise RuntimeError("Configura la API key de Gemini antes de evaluar con IA.")
    if not candidate.files:
        raise RuntimeError("El candidato no tiene archivos de expediente cargados.")

    rubric = [
        {
            "id": criterion.id,
            "code": criterion.code,
            "category": criterion.category,
            "aspect": criterion.aspect,
            "notes": criterion.notes,
            "is_critical": criterion.is_critical,
            "evaluation_mode": criterion.evaluation_mode.value if hasattr(criterion.evaluation_mode, "value") else str(criterion.evaluation_mode),
        }
        for criterion in criteria
    ]
    prompt = (
        "Segundo turno de evaluación. Ya existe una evaluación sobria por criterios; no recalifiques esos criterios. "
        "Analiza el expediente completo y el resultado ya calculado para decidir si corresponde una bonificación adicional global. "
        "La bonificación solo aplica por elementos claramente positivos, documentados y relevantes para el perfil que no hayan sido "
        "capturados suficientemente por las categorías o criterios existentes. No bonifiques por lo que ya fue plenamente puntuado, "
        "no dupliques credenciales, experiencia, cargos, proyectos ni formación ya agotados en la evaluación. "
        "Devuelve únicamente JSON válido con la forma "
        '{"bonus_score":number,"rationale":"texto breve"}. '
        "bonus_score va de 0 a 5: 0 sin bonificación, 1 marginal, 2 baja, 3 moderada, 4 alta, 5 excepcional. "
        "Sé conservador: la bonificación debe ser excepcional o claramente justificable, y no sustituye requisitos críticos ni criterios no cumplidos. "
        f"Candidato: {candidate.name}. Criterios del perfil: {json.dumps(rubric, ensure_ascii=False)}. "
        f"Evaluación ya registrada: {json.dumps(evaluation_snapshot, ensure_ascii=False)}"
    )
    client = genai.Client(api_key=api_key)
    if cached_content_name:
        prompt = "Usa el expediente documental cacheado asociado a esta solicitud. " + prompt
    parts: list = [types.Part.from_text(text=prompt)]
    if not cached_content_name:
        parts.extend(candidate_document_parts(candidate, upload_dir))
    response = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=parts)],
        config=generate_config(model, cached_content_name),
    )
    return gemini_result(model, prompt, response, cached_content_name or "")


def generate_general_report_narrative_with_gemini(
    report_source_text: str,
    api_key: str | None,
    model: str,
) -> GeminiCallResult:
    if not api_key:
        raise RuntimeError("Configura la API key de Gemini antes de generar la narrativa del informe.")
    if not report_source_text.strip():
        raise RuntimeError("No hay contenido suficiente para generar la narrativa del informe.")

    client = genai.Client(api_key=api_key)
    prompt = (
        "Redacta la síntesis interpretativa y la conclusión ejecutiva de un informe general de evaluación curricular. "
        "Usa como insumo todo el contenido estructurado del informe que se suministra más abajo. "
        "No inventes datos, participantes, puntuaciones, documentos ni decisiones. "
        "No uses lenguaje técnico sobre la plataforma, plantillas, IA, prompts, logs o automatización. "
        "Cuando menciones participantes, usa siempre sus nombres completos; no uses referencias abreviadas ni letras sueltas. "
        "Si la evaluación está incompleta, expresa claramente que la valoración no es concluyente y que debe completarse antes del cierre. "
        "Mantén un tono profesional, institucional, sobrio y fácil de leer para Recursos Humanos y el área técnica. "
        "Devuelve únicamente JSON válido con la forma "
        '{"synthesis":["párrafo 1","párrafo 2","párrafo 3"],"conclusion":["párrafo 1","párrafo 2"]}. '
        "Cada párrafo debe tener entre 45 y 90 palabras. "
        f"Contenido completo del informe:\n{report_source_text}"
    )

    response = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
        config=generate_config(model),
    )
    return gemini_result(model, prompt, response)


def generate_template_with_gemini(
    requirements_text: str,
    file_name: str | None,
    file_bytes: bytes | None,
    file_mime_type: str | None,
    api_key: str | None,
    model: str,
) -> GeminiCallResult:
    if not api_key:
        raise RuntimeError("Configura la API key de Gemini antes de generar plantillas con IA.")
    if not requirements_text.strip() and not file_bytes:
        raise RuntimeError("Carga un PDF o escribe los requisitos de la posición.")

    client = genai.Client(api_key=api_key)
    prompt = (
        "Genera una plantilla de evaluación curricular para una vacante técnica a partir de los requisitos suministrados. "
        "Devuelve únicamente JSON válido con esta forma exacta: "
        '{"name":"texto","description":"texto","ai_evaluation_locked":true,'
        '"categories":[{"name":"texto","weight":number,"order_index":number}],'
        '"criteria":[{"code":"","category":"texto","aspect":"texto","category_weight":number,'
        '"within_category_weight":number,"global_weight":number,"scale":"0 a 5","notes":"texto",'
        '"is_critical":boolean,"evaluation_mode":"manual|automatic","order_index":number}]}. '
        "Los pesos deben ser decimales entre 0 y 1. La suma de categories.weight debe ser 1. "
        "Dentro de cada categoría, la suma de within_category_weight de criterios no críticos debe ser 1. "
        "Los criterios críticos son requisitos excluyentes de cumple/no cumple y deben tener within_category_weight 0 y global_weight 0. "
        "Usa evaluation_mode automatic cuando el criterio pueda inferirse de CV, certificaciones, experiencia documentada o expediente; "
        "usa manual cuando requiera entrevista, examen temático, validación institucional o juicio experto no plenamente documental. "
        "En notes escribe instrucciones concretas para evaluar ese criterio, especialmente para criterios automáticos. "
        "Si el cargo es de encargado, gerente, director o nivel equivalente, incluye categorías para aspectos estratégicos, "
        "competencias normativas e institucionales y competencias técnicas regulatorias, con ponderaciones explícitas. "
        "Para criterios de formación, separa las certificaciones internacionales/profesionales de los cursos y diplomados; "
        "estos últimos pueden agruparse como formación complementaria cuando aplique. Indica en notes que una misma credencial no debe contarse dos veces. "
        "Evita códigos visibles; code puede ir vacío. Crea una plantilla compacta, clara y utilizable, no una tabla copiada. "
        f"Requisitos escritos: {requirements_text.strip() or 'No suministrados por texto.'}"
    )

    parts: list = [types.Part.from_text(text=prompt)]
    if file_bytes:
        parts.append(types.Part.from_text(text=f"Documento de requisitos: {file_name or 'requisitos.pdf'}"))
        parts.append(types.Part.from_bytes(data=file_bytes, mime_type=file_mime_type or "application/pdf"))

    response = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=parts)],
        config=generate_config(model),
    )
    return gemini_result(model, prompt, response)
