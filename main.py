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
import supabase
from supabase import create_client, Client
import json

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

# --- Configuración Supabase ---
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL:
    logger.error("CRITICAL: La variable de entorno SUPABASE_URL no está configurada.")
    raise ValueError("SUPABASE_URL environment variable is not set.")

try:
    # Crear cliente Supabase desde la URL. 
    supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("Conectado a Supabase correctamente.")
except Exception as e:
    logger.error(f"CRITICAL: Error conectando a Supabase: {e}")
    raise ConnectionError(f"No se pudo conectar a Supabase: {e}") from e

# --- Constantes de Estado y TTL ---
STATUS_PROCESSING = "processing"
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_NOT_FOUND = "not_found" # Estado implícito si no existe la key
GEMINI_ERROR_MARKER = "ERROR_PROCESSING_GEMINI" # Marcador en el resultado

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

# Función para generar un resumen legible del payload de Tally
def summarize_payload(payload: TallyWebhookPayload) -> str:
    """Genera un resumen entendible del Tally payload."""
    lines = ["Respuestas:"]
    for field in payload.data.fields:
        label = field.label or field.key
        value = field.value
        # Si el valor es una lista y tiene opciones, mapeamos los IDs a texto
        if isinstance(value, list) and field.options:
            id_to_text = {opt.id: opt.text for opt in field.options}
            value_texts = [id_to_text.get(v, v) for v in value]
            value_str = ", ".join(value_texts)
        else:
            value_str = str(value)
        lines.append(f"- {label}: {value_str}")
    return "\n".join(lines)

def detect_form_type(payload: TallyWebhookPayload) -> str:
    """Detecta el form type basándose en la primera label o key."""
    if payload.data.fields:
        first_label = payload.data.fields[0].label or payload.data.fields[0].key
        if first_label.strip() == "¿De qué sector es tu empresa o grupo?":
            return "CFO_Form"
    return "Unknown"

def generate_prompt(payload: TallyWebhookPayload, submission_id: str, form_type: str) -> str:
    """Genera un prompt para Gemini basado en el tipo de formulario."""
    if form_type == "CFO_Form":
        logger.info(f"[{submission_id}] Formulario CFO detectado. Procesando respuestas.")

        # --- Generación del Prompt (sin cambios) ---
        prompt_parts = ["Analiza la siguiente respuesta de encuesta y proporciona un resumen o conclusión en formato markdown:\n\n"]

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
    else:
        logger.info(f"[{submission_id}] Otro tipo de formulario detectado. Procesando respuestas.")

        # --- Generación del Prompt (sin cambios) ---
        prompt_parts = ["Analiza la siguiente respuesta de encuesta y proporciona un resumen o conclusión en formato markdown:\n\n"]

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
    return full_prompt


# --- Lógica para interactuar con Gemini ---
async def generate_gemini_response(submission_id: str, prompt: str):
    """Genera una respuesta de Gemini y actualiza Supabase con el resultado."""
    logger.info(f"[{submission_id}] Iniciando tarea Gemini.")
    
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
 
        # --- Actualizar Supabase con el resultado ---
        if result_text:
            # Guardar resultado en Supabase
            try:
                supabase_client.table("form_AI_DB").update({
                    "submission_id": submission_id,
                    "status": STATUS_SUCCESS,
                    "result": result_text
                }).eq("submission_id", submission_id).execute()
                logger.info(f"[{submission_id}] Resultado guardado en Supabase.")
                logger.info(f"[{submission_id}] Estado '{STATUS_SUCCESS}' y resultado guardados en Supabase.")
            except Exception as e:
                logger.error(f"[{submission_id}] Error guardando resultado en Supabase: {e}")
        else:
            # Si no hay texto válido, guardar error
            try:
                supabase_client.table("form_AI_DB").update({
                    "submission_id": submission_id,
                    "status": STATUS_ERROR,
                    "result": GEMINI_ERROR_MARKER
                }).eq("submission_id", submission_id).execute()
                logger.warning(f"[{submission_id}] Estado '{STATUS_ERROR}' y marcador guardados en Supabase (sin texto válido).")
            except Exception as e:
                logger.error(f"[{submission_id}] Error guardando marcador de error en Supabase: {e}")

    except Exception as e:
        logger.error(f"[{submission_id}] Excepción durante procesamiento Gemini: {e}", exc_info=True) # Log con traceback
        try:
            # Intenta guardar el estado de error incluso si Gemini falló
            supabase_client.table("form_AI_DB").update({
                "submission_id": submission_id,
                "status": STATUS_ERROR,
                "result": f"Error interno: {e}"
            }).eq("submission_id", submission_id).execute()
            logger.warning(f"[{submission_id}] Estado '{STATUS_ERROR}' guardado en Supabase debido a excepción.")
        except Exception as e:
            logger.error(f"[{submission_id}] Error guardando estado de error en Supabase: {e}")

    logger.info(f"[{submission_id}] Tarea Gemini finalizada.")
    
# --- Endpoints FastAPI ---

@app.post("/webhook")
async def handle_tally_webhook(payload: TallyWebhookPayload, background_tasks: BackgroundTasks):
    # ... (keep your existing webhook handler) ...
    submission_id = payload.data.submissionId
    logger.info(f"[{submission_id}] Webhook recibido. Verificando Supabase (ID: {submission_id}).")
    logger.info(f"[{submission_id}] Event ID: {payload.eventId}, Event Type: {payload.eventType}")

    try:
        # Verificar si ya existe un estado final (success o error) o si aún está procesando
        # Usamos SET con NX (Not Exists) y GET para hacerlo atómico y evitar race conditions
        # set(key, value, nx=True) -> True si la key se creó, False si ya existía
        data = supabase_client.table("form_AI_DB").select("*").eq("submission_id", submission_id).execute()
        if data.data:
            if data.data['status'] == STATUS_PROCESSING:
                logger.warning(f"[{submission_id}] Webhook ignorado: ya está en estado '{STATUS_PROCESSING}'.")
                return {"status": "ok", "message": "Already processing"}
            else:
                logger.warning(f"[{submission_id}] Webhook ignorado: ya tiene estado final '{data.data['status']}'.")
                return {"status": "ok", "message": f"Already processed with status: {data.data['status']}"}
        
        form_type = detect_form_type(payload)
        response = summarize_payload(payload)
        supabase_client.table("form_AI_DB").insert({
                "submission_id": submission_id,
                "status": STATUS_PROCESSING,
                "result": None,  # Inicialmente no hay resultado"
                "user_responses": response,  # Resumen legible del payload
                "form_type": form_type  # Tipo de formulario
            }).execute()

        # Si llegamos aquí, la key se creó y se puso en 'processing'
        logger.info(f"[{submission_id}] Estado '{STATUS_PROCESSING}' establecido en Supabase.")

# -------------------------------------------------
        # --- Generación del Prompt modularizada ---
        full_prompt = generate_prompt(payload, submission_id, form_type)
        logger.debug(f"[{submission_id}] Prompt para Gemini: {full_prompt[:200]}...")
 
    # --- Iniciar Tarea en Segundo Plano ---
        background_tasks.add_task(generate_gemini_response, submission_id, full_prompt)
        logger.info(f"[{submission_id}] Tarea de Gemini iniciada en segundo plano.")

        return {"status": "ok", "message": "Processing started"}

    except Exception as e:
        logger.error(f"[{submission_id}] Error procesando webhook: {e}", exc_info=True)
        # Devolver error 500 si algo falla aquí es crítico
        raise HTTPException(status_code=500, detail="Internal server error")
    

# --- GET METHOD (Defined AFTER the PUT for the same path) ---
@app.get("/results/{submission_id}", response_class=HTMLResponse)
async def get_results_page(request: Request, submission_id: str):

    final_status = STATUS_NOT_FOUND # Estado por defecto si no encontramos la key de estado
    result_value = False # Indica si hay resultado
    error_message = None
    http_status_code = 404 # Por defecto es Not Found

    logger.info(f"[{submission_id}] GET /results. Consultando Supabase (ID: {submission_id}).")

    try:
        # Obtener el estado en Supabase

        data = supabase_client.table("form_AI_DB").select("*").eq("submission_id", submission_id).execute()
        supabase_status = data.data[0]['status'] if data.data else None # Extraer el estado si existe
        supabase_result = data.data[0]['result'] if data.data else None # Extraer el resultado si existe
        logger.info(f"[{submission_id}] Estado en Supabase: {supabase_status}).")

        if supabase_status == STATUS_PROCESSING:
            final_status = STATUS_PROCESSING
            http_status_code = 200 # Página encontrada, pero está procesando
            logger.info(f"[{submission_id}] Estado Supabase: {STATUS_PROCESSING}")
        elif supabase_status == STATUS_SUCCESS:
            final_status = STATUS_SUCCESS
            http_status_code = 200
            result_value = supabase_result
            logger.info(f"[{submission_id}] Estado Supabase: {STATUS_SUCCESS}. Resultado obtenido.")
        elif supabase_status == STATUS_ERROR:
            final_status = STATUS_ERROR
            http_status_code = 200 # Mostramos la página de error normalmente
            error_message = supabase_result
            logger.warning(f"[{submission_id}] Estado Supabase: {STATUS_ERROR}. Mensaje/marcador: {error_message}")
        elif supabase_status is None:
            # La key de estado no existe, por lo tanto "not found"
            final_status = STATUS_NOT_FOUND
            http_status_code = 404
            logger.warning(f"[{submission_id}] No se encontró estado en Supabase (ID: {submission_id}).")
        else:
            # Estado inesperado guardado en Supabase
            final_status = STATUS_ERROR
            http_status_code = 500  # Error interno porque el estado es inválido
            error_message = f"Error interno: Estado inválido '{supabase_status}' encontrado en Supabase."
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
    
    except Exception as e:
        logger.error(f"[{submission_id}] Error inesperado en GET /results: {e}", exc_info=True)
        # Devolver error 500 si algo falla aquí es crítico
        context = {"request": request, "submission_id": submission_id, "status": "critical_error", "error_message": "Error interno del servidor."}
        return templates.TemplateResponse("results.html", context, status_code=500)


@app.get("/")
async def root():
    """Endpoint raíz simple para verificar que la app funciona."""
    return {"message": "Hola! Soy el procesador de Tally a Gemini."}


# --- Para ejecutar localmente (opcional, Vercel usa su propio método) ---
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run("main:app", host="0.0.0.0", port=8000)

