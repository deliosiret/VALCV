# VALCV

Web app para evaluar curriculum vitae con plantillas ponderadas, carga de expedientes en PDF/imagen, evaluación manual o automática con Gemini y gráficas comparativas.

## Puertos

- Frontend: `http://localhost:18673`
- API FastAPI: `http://localhost:18429`
- PostgreSQL: `localhost:15439`

El frontend en Docker llama a la API por la ruta relativa `/api`, servida por el mismo contenedor web y reenviada internamente a FastAPI. Esto evita problemas de `localhost` y contenido mixto cuando se usa un dominio HTTPS.

## Ejecutar

```bash
cp .env.example .env
# edita GEMINI_API_KEY si usarás evaluación automática
# cambia ADMIN_PASSWORD antes de exponer la app
docker compose up --build
```

La primera plantilla se inicializa con los criterios del archivo `evaluacion_aspirantes_gerente_normas_electricas (1).xlsx`.

El usuario inicial por defecto es `admin` con contraseña `admin123`. Cámbiala en `.env` antes del primer arranque en producción.

Si cambias el frontend y lo sirves detrás de Nginx Proxy Manager, reconstruye el servicio web para evitar mezclar JS nuevo con CSS viejo:

```bash
docker compose build --no-cache web
docker compose up -d web
```
