import json
import mimetypes
from pathlib import Path

from google import genai
from google.genai import types

from app.models import Candidate, Criterion


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
        }
        for criterion in criteria
    ]
    prompt = (
        "Evalúa el expediente del candidato para una plantilla de CV. "
        "Devuelve únicamente JSON válido con la forma "
        '{"scores":[{"criterion_id":number,"score":number,"rationale":"texto breve","file_ids":[number]}]}. '
        "En file_ids incluye el id de cada documento usado como referencia para ese criterio; si no usaste evidencia documental, usa []. "
        "La puntuación va de 0 a 5: 5 excelente, 4 muy bueno, 3 aceptable, "
        "2 débil, 1 deficiente, 0 no evidenciado. Si no hay evidencia documental, usa 0 o 1. "
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
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
        ),
    )
    payload = _extract_json(response.text or "{}")
    return payload.get("scores", [])
