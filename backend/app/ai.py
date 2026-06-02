import json
import mimetypes
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
- Diferencia estrictamente certificación, diplomado y curso. Una certificación normalmente acredita competencia mediante una entidad certificadora, examen, licencia, estándar, credencial profesional o certificado formal de competencia; un diplomado es formación académica estructurada; un curso es capacitación puntual y no debe tratarse como certificación ni como diplomado.
- Dentro de una categoría, premia cantidad, nivel, pertinencia y actualidad de postgrados, certificaciones y diplomados solo mientras exista margen de puntuación en el criterio correspondiente. Nunca excedas 5 puntos por criterio.
- Para formación, prioriza relevancia regulatoria, eléctrica, energética y de mercados eléctricos sobre formación genérica de otros sectores.
- Si la evidencia es ambigua, incompleta o no verificable en los documentos, asigna una puntuación conservadora y explícalo.
""".strip()


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


def generate_config(model: str) -> types.GenerateContentConfig:
    model_name = model.lower()
    thinking_config = None
    if model_name.startswith("gemini-3"):
        thinking_config = types.ThinkingConfig(thinking_level="low")
    elif model_name.startswith(("gemini-2.5-flash", "gemini-2.5-flash-lite")):
        thinking_config = types.ThinkingConfig(thinking_budget=0)
    return types.GenerateContentConfig(
        response_mime_type="application/json",
        thinking_config=thinking_config,
    )


def evaluate_candidate_with_gemini(
    candidate: Candidate,
    criteria: list[Criterion],
    upload_dir: str,
    api_key: str | None,
    model: str,
) -> list[dict]:
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

    parts: list = [types.Part.from_text(text=prompt)]
    for file in candidate.files:
        path = Path(upload_dir) / file.stored_name
        mime_type = file.mime_type or mimetypes.guess_type(file.original_name)[0] or "application/octet-stream"
        if mime_type not in {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}:
            continue
        parts.append(types.Part.from_text(text=f"Documento disponible: id={file.id}, nombre={file.original_name}"))
        parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type))

    contents = [types.Content(role="user", parts=parts)]
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=generate_config(model),
    )
    payload = _extract_json(response.text or "{}")
    return payload.get("scores", [])


def generate_template_with_gemini(
    requirements_text: str,
    file_name: str | None,
    file_bytes: bytes | None,
    file_mime_type: str | None,
    api_key: str | None,
    model: str,
) -> dict:
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
        "Para criterios de formación, separa maestría, postgrado, certificación, diplomado y curso cuando aplique, e indica en notes "
        "que una misma credencial no debe contarse dos veces. "
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
    return _extract_json(response.text or "{}")
