import os
import logging
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field # Pydantic V2 style
from typing import List, Optional, Dict, Any
import google.generativeai as genai
from dotenv import load_dotenv
import asyncio # Para ejecutar tareas en segundo plano
import time
import redis 

# --- Configuración Inicial ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

# --- Configuración Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    logger.error("Error: La variable de entorno GEMINI_API_KEY no está configurada.")
else:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        logger.info("Cliente de Gemini configurado correctamente.")
    except Exception as e:
        logger.error(f"Error configurando el cliente de Gemini: {e}")

GEMINI_MODEL_NAME = "gemini-2.0-flash" # Use a valid model

# --- Configuración Redis ---
REDIS_URL = os.getenv("REDIS_URL")
REDIS_KEY = os.getenv("REDIS_KEY")
REDIS_PORT = os.getenv("REDIS_PORT")
if not REDIS_URL:
    logger.error("CRITICAL: La variable de entorno REDIS_URL no está configurada.")
    # En producción (Vercel), esto debería detener la aplicación o manejarlo
    # como un error crítico. Para local, podrías poner una URL por defecto si tienes Redis local.
    raise ValueError("REDIS_URL environment variable is not set.")

try:
    # Crear cliente Redis desde la URL. decode_responses=True es útil.
    redis_client = redis.Redis(host=REDIS_URL, port=REDIS_PORT, password=REDIS_KEY, ssl=True, decode_responses=True,max_connections=20)
    logger.error(f"linea 44 pasada")    #chivato
    redis_client.ping() # Prueba la conexión al iniciar
    logger.error(f"linea 46 pasada")    #chivato
    logger.info("Conectado a Redis correctamente.")
except redis.exceptions.ConnectionError as e:
    logger.error(f"CRITICAL: Error conectando a Redis: {e}")
    # La aplicación no puede funcionar sin Redis, lanzar error
    raise ConnectionError(f"No se pudo conectar a Redis: {e}") from e
except Exception as e:
     logger.error(f"CRITICAL: Error inesperado al inicializar Redis: {e}")
     raise e

# --- Constantes de Estado y TTL ---
STATUS_PROCESSING = "processing"
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_NOT_FOUND = "not_found" # Estado implícito si no existe la key
GEMINI_ERROR_MARKER = "ERROR_PROCESSING_GEMINI" # Marcador en el resultado
REDIS_TTL_SECONDS = 86400 # Tiempo de vida de las keys en Redis (ej: 1 día)

# --- App FastAPI ---
app = FastAPI(title="Tally Webhook Processor")
templates = Jinja2Templates(directory="templates")

# --- Modelos Pydantic ---
# ... (keep your existing Pydantic models) ...
class TallyOption(BaseModel):
    id: str
    text: str
    
class TallyField(BaseModel):
    key: str
    label: Optional[str]
    value: Any
    type: str
    options: Optional[List[TallyOption]] = None

class TallyResponseData(BaseModel):
    responseId: str
    submissionId: str
    fields: List[TallyField]

class TallyWebhookPayload(BaseModel):
    eventId: str
    eventType: str
    data: TallyResponseData

# Placeholder model for PUT data (adjust as needed)
class UpdateResultPayload(BaseModel):
    new_result: str
    reason: Optional[str] = None


# --- Lógica para interactuar con Gemini ---
async def generate_gemini_response(submission_id: str, prompt: str):
    # Define las claves de Redis que se usarán para este submission_id
    status_key = f"status:{submission_id}"
    result_key = f"result:{submission_id}"
    logger.info(f"[{submission_id}] Iniciando tarea Gemini. Keys Redis: {status_key}, {result_key}")
    
    try:
        # --- Llamada a Gemini API (lógica sin cambios) ---
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        response = await model.generate_content_async(prompt)
        result_text = None # Inicializa result_text

        if response and hasattr(response, 'text') and response.text:
             result_text = response.text
             logger.info(f"[{submission_id}] Respuesta de Gemini recibida (text)")
        elif response and hasattr(response, 'parts'):
             result_text = "".join(part.text for part in response.parts if hasattr(part, 'text'))
             if result_text:
                logger.info(f"[{submission_id}] Respuesta de Gemini recibida (desde parts).")
             else:
                 logger.warning(f"[{submission_id}] Respuesta de Gemini (parts) sin texto")
                 result_text = None # Asegura que no se guarde si está vacío
        else:
            logger.warning(f"[{submission_id}] Respuesta Gemini inesperada o sin texto")
 
        # --- Actualizar Redis con el resultado ---
        if result_text:
            redis_client.set(result_key, result_text, ex=REDIS_TTL_SECONDS)
            redis_client.set(status_key, STATUS_SUCCESS, ex=REDIS_TTL_SECONDS)
            logger.info(f"[{submission_id}] Estado '{STATUS_SUCCESS}' y resultado guardados en Redis.")
        else:
            # Si no hay texto válido, guardar error
            redis_client.set(result_key, GEMINI_ERROR_MARKER, ex=REDIS_TTL_SECONDS)
            redis_client.set(status_key, STATUS_ERROR, ex=REDIS_TTL_SECONDS)
            logger.warning(f"[{submission_id}] Estado '{STATUS_ERROR}' y marcador guardados en Redis (sin texto válido).")

    except Exception as e:
        logger.error(f"[{submission_id}] Excepción durante procesamiento Gemini: {e}", exc_info=True) # Log con traceback
        try:
            # Intenta guardar el estado de error incluso si Gemini falló
            redis_client.set(result_key, f"Error interno: {e}", ex=REDIS_TTL_SECONDS) # Guarda el mensaje de error si es posible
            redis_client.set(status_key, STATUS_ERROR, ex=REDIS_TTL_SECONDS)
            logger.warning(f"[{submission_id}] Estado '{STATUS_ERROR}' guardado en Redis debido a excepción.")
        except redis.exceptions.RedisError as redis_err:
            logger.error(f"[{submission_id}] CRITICAL: Fallo al guardar estado de error en Redis tras excepción Gemini: {redis_err}")
        except Exception as inner_e:
             logger.error(f"[{submission_id}] CRITICAL: Error inesperado al guardar estado de error en Redis: {inner_e}")

    logger.info(f"[{submission_id}] Tarea Gemini finalizada.")
    
# --- Endpoints FastAPI ---

@app.post("/webhook")
async def handle_tally_webhook(payload: TallyWebhookPayload, background_tasks: BackgroundTasks):
    # ... (keep your existing webhook handler) ...
    submission_id = payload.eventId
    status_key = f"status:{submission_id}"
    logger.info(f"[{submission_id}] Webhook recibido. Verificando Redis (Key: {status_key}).")

    try:
        # Verificar si ya existe un estado final (success o error) o si aún está procesando
        # Usamos SET con NX (Not Exists) y GET para hacerlo atómico y evitar race conditions
        # set(key, value, nx=True) -> True si la key se creó, False si ya existía
        if not redis_client.set(status_key, STATUS_PROCESSING, nx=True, ex=REDIS_TTL_SECONDS):
            # La key ya existía. Comprobar qué estado tiene.
            current_status = redis_client.get(status_key)
            if current_status == STATUS_PROCESSING:
                logger.warning(f"[{submission_id}] Webhook ignorado: ya está en estado '{STATUS_PROCESSING}'.")
                return {"status": "ok", "message": "Already processing"}
            else: # Ya tiene un estado final (success/error)
                 logger.warning(f"[{submission_id}] Webhook ignorado: ya tiene estado final '{current_status}'.")
                 return {"status": "ok", "message": f"Already processed with status: {current_status}"}

        # Si llegamos aquí, redis_client.set tuvo éxito (nx=True), la key se creó y se puso en 'processing'
        logger.info(f"[{submission_id}] Estado '{STATUS_PROCESSING}' establecido en Redis.")

        # --- Generación del Prompt (sin cambios) ---
        prompt_parts = ["Analiza la siguiente respuesta de encuesta y proporciona un resumen o conclusión:\n\n"]
  
# -------------------------------------------------
         # ... ( lógica para construir el prompt con payload.data.fields) ... 
        for field in payload.data.fields:
            label = field.label
            label_str = "null" if label is None else str(label).strip()
            value = field.value
            value_str = ""
            if isinstance(value, list):
                try:
                    value_str = f'"{",".join(map(str, value))}"'
                except Exception as e:
                    logger.error(f"[{submission_id}] Error convirtiendo lista a string: {e}")
                    value_str = "[Error procesando lista]"
            elif value is None:
                value_str = "null"
            else:
                value_str = str(value)
            prompt_parts.append(f"Pregunta: {label_str} - Respuesta: {value_str}")
# -------------------------------------------------
        full_prompt = "".join(prompt_parts)
        logger.debug(f"[{submission_id}] Prompt para Gemini: {full_prompt[:200]}...")
 
    # --- Iniciar Tarea en Segundo Plano ---
        background_tasks.add_task(generate_gemini_response, submission_id, full_prompt)
        logger.info(f"[{submission_id}] Tarea de Gemini iniciada en segundo plano.")

        return {"status": "ok", "message": "Processing started"}

    except redis.exceptions.RedisError as e:
        logger.error(f"[{submission_id}] Error de Redis en webhook: {e}")
        # Devolver error 500 si Redis falla aquí es crítico
        raise HTTPException(status_code=500, detail="Internal server error (Redis operation failed)")
    except Exception as e:
        logger.error(f"[{submission_id}] Error inesperado en webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# --- GET METHOD (Defined AFTER the PUT for the same path) ---
@app.get("/results/{submission_id}", response_class=HTMLResponse)
async def get_results_page(request: Request, submission_id: str):
    status_key = f"status:{submission_id}"
    result_key = f"result:{submission_id}"
    logger.info(f"[{submission_id}] GET /results. Consultando Redis (Keys: {status_key}, {result_key}).")

    final_status = STATUS_NOT_FOUND # Estado por defecto si no encontramos la key de estado
    result_value = None
    error_message = None
    http_status_code = 404 # Por defecto es Not Found

    try:
        # Obtener el estado de Redis
        redis_status = redis_client.get(status_key)

        if redis_status == STATUS_PROCESSING:
            final_status = STATUS_PROCESSING
            http_status_code = 200 # Página encontrada, pero está procesando
            logger.info(f"[{submission_id}] Estado Redis: {STATUS_PROCESSING}")
        elif redis_status == STATUS_SUCCESS:
            final_status = STATUS_SUCCESS
            http_status_code = 200
            result_value = redis_client.get(result_key)
            logger.info(f"[{submission_id}] Estado Redis: {STATUS_SUCCESS}. Resultado obtenido.")
            if result_value is None:
                # Puede pasar si la key de resultado expiró antes que la de estado (poco probable con mismo TTL)
                logger.error(f"[{submission_id}] INCONSISTENCIA: Estado es '{STATUS_SUCCESS}' pero falta resultado en {result_key}")
                final_status = STATUS_ERROR
                error_message = "Error: Resultado no encontrado a pesar de estado exitoso."
        elif redis_status == STATUS_ERROR:
            final_status = STATUS_ERROR
            http_status_code = 200 # Mostramos la página de error normalmente
            error_message = redis_client.get(result_key) # Obtenemos el mensaje/marcador de error
            logger.warning(f"[{submission_id}] Estado Redis: {STATUS_ERROR}. Mensaje/marcador: {error_message}")
        elif redis_status is None:
            # La key de estado no existe, por lo tanto "not found"
            final_status = STATUS_NOT_FOUND
            http_status_code = 404
            logger.warning(f"[{submission_id}] No se encontró estado en Redis (key: {status_key}).")
        else:
            # Estado inesperado guardado en Redis
            final_status = STATUS_ERROR
            http_status_code = 500 # Error interno porque el estado es inválido
            error_message = f"Error interno: Estado inválido '{redis_status}' encontrado en Redis."
            logger.error(f"[{submission_id}] {error_message}")

        # Contexto para la plantilla
        context = {
            "request": request,
            "submission_id": submission_id,
            "result": result_value if final_status == STATUS_SUCCESS else None,
            "error_message": error_message if final_status == STATUS_ERROR else None,
            "status": final_status # Pasar el estado final a la plantilla
        }
        logger.info(f"linea 270 - [{submission_id}] - request: {request}") #chivato
        logger.info(f"linea 271 - [{submission_id}] - submission_id: {submission_id}") #chivato
        logger.info(f"linea 272 - [{submission_id}] - result: {result_value}") #chivato
        logger.info(f"linea 273 - [{submission_id}] - error_message: {error_message}") #chivato 
        logger.info(f"linea 274 - [{submission_id}] - status: {final_status}") #chivato
        logger.info(f"linea 275 - [{submission_id}] - status_code: {http_status_code}") #chivato
               
        return templates.TemplateResponse("results.html", context, status_code=http_status_code)
    

        
    except redis.exceptions.RedisError as e:
        logger.error(f"[{submission_id}] Error de Redis en GET /results: {e}")
        # Error crítico al intentar leer de Redis
        context = {"request": request, "submission_id": submission_id, "status": "critical_error", "error_message": "Error de comunicación con la base de datos de estado."}
        # Devolver 503 Service Unavailable podría ser apropiado
        return templates.TemplateResponse("results.html", context, status_code=503)
    except Exception as e:
         logger.error(f"[{submission_id}] Error inesperado en GET /results: {e}", exc_info=True)
         context = {"request": request, "submission_id": submission_id, "status": "critical_error", "error_message": "Error interno del servidor."}
         return templates.TemplateResponse("results.html", context, status_code=500)



@app.get("/")
async def root():
    """Endpoint raíz simple para verificar que la app funciona."""
    return {"message": "Hola! Soy el procesador de Tally a Gemini."}


# --- Para ejecutar localmente (opcional, Vercel usa su propio método) ---
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)