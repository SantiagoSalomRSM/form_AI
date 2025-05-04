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

# --- Configuración Inicial ---
# ... (keep your existing setup code) ...
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

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

GEMINI_ERROR_MARKER = "ERROR_PROCESSING_GEMINI" # Marcador para errores
results_store: Dict[str, str] = {}
processing_status: Dict[str, bool] = {}

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
# ... (keep your existing generate_gemini_response function) ...
async def generate_gemini_response(submission_id: str, prompt: str):
    logger.info(f"[{submission_id}] Generando respuesta de Gemini...")
    # Add result to store, handle errors, remove from processing_status
    # (Your existing logic here)
    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        response = await model.generate_content_async(prompt)
        result_text = None # Inicializa result_text

        if response and hasattr(response, 'text') and response.text:
             result_text = response.text
             logger.info(f"[{submission_id}] Respuesta de Gemini recibida - {prompt} ----> {result_text}.")
        elif response and hasattr(response, 'parts'):
             result_text = "".join(part.text for part in response.parts if hasattr(part, 'text'))
             if result_text:
                logger.info(f"[{submission_id}] Respuesta de Gemini recibida (desde parts).")
             else:
                 logger.warning(f"[{submission_id}] Respuesta de Gemini recibida pero sin contenido de texto en 'parts'. Respuesta: {response}")
                 result_text = None # Asegura que no se guarde si está vacío
        else:
            logger.warning(f"[{submission_id}] Respuesta de Gemini recibida pero sin contenido de texto. Respuesta: {response}")

        if result_text:
            results_store[submission_id] = result_text
            logger.info(f"[{submission_id}] Resultado guardado correctamente.")
        else:
             # Si no hubo texto válido, márcalo como error
             results_store[submission_id] = GEMINI_ERROR_MARKER
             logger.warning(f"[{submission_id}] No se obtuvo texto v\u00E1lido de Gemini. Marcado como error.")

        # Elimina de processing SOLO si tuvimos éxito o marcamos error aquí
        processing_status.pop(submission_id, None)

    except Exception as e:
        logger.error(f"[{submission_id}] Error llamando a la API de Gemini: {e}")
        # Guarda el marcador de error en caso de excepción
        results_store[submission_id] = GEMINI_ERROR_MARKER
        # Elimina de processing DESPUÉS de marcar el error
        processing_status.pop(submission_id, None)
    # NO hay bloque finally aquí para pop
    logger.info(f"[{submission_id}] Procesamiento de Gemini finalizado. Estado guardado: {results_store.get(submission_id)}")
    
# --- Endpoints FastAPI ---

@app.post("/webhook")
async def handle_tally_webhook(payload: TallyWebhookPayload, background_tasks: BackgroundTasks):
    # ... (keep your existing webhook handler) ...
    submission_id = payload.eventId
    logger.info(f"[{submission_id}] Webhook recibido de Tally.")

    if submission_id in results_store or submission_id in processing_status:
        logger.warning(f"[{submission_id}] Ya procesado o en proceso. Ignorando.")
        return {"status": "ok", "message": "Already processed or in progress"}
    

    processing_status[submission_id] = True
    prompt_parts = ["Analiza la siguiente respuesta de encuesta y proporciona un resumen o conclusión:\n\n"]
    
# -------------------------------------------------

    for field in payload.data.fields:
        # Obtiene el label. Si es None (null en JSON), usa el string "null"
        label = field.label
        label_str = "null" if label is None else str(label).strip() # strip() para quitar espacios extra
        # Obtiene el value
        value = field.value
        logger.info(f"[{submission_id}] 124 {label} - {label_str} - {value}.")    #chivato
        # Formatea el value según su tipo para que coincida con el ejemplo
        if isinstance(value, list):
            try:
                # Si es una lista, une los elementos con coma y envuélvelos en comillas dobles
                value_str = f'"{",".join(map(str, value))}"'
            except Exception as e:
            # Añadir logging por si falla la conversión a string
                logger.error(f"[{submission_id}] Error convirtiendo lista a string para campo {field.key}: {e}")
                value_str = "[Error procesando lista]" # Valor por defecto o manejo alternativo
        elif value is None:
             value_str = "null"
        else:
            # Para otros tipos (int, string, etc.), simplemente conviértelos a string
            value_str = str(value)

        # Crea la línea formateada y añádela a la lista
        prompt_parts.append(f"Pregunta: {label_str} - Respuesta: {value_str}")

# -------------------------------------------------
    full_prompt = "".join(prompt_parts)
    logger.debug(f"[{submission_id}] Prompt para Gemini: {full_prompt[:200]}...")

    background_tasks.add_task(generate_gemini_response, submission_id, full_prompt)

    logger.info(f"[{submission_id}] Tarea de Gemini iniciada en segundo plano. Respondiendo OK a Tally.")
    return {"status": "ok", "message": "Processing started"}

# --- GET METHOD (Defined AFTER the PUT for the same path) ---
@app.get("/results/{submission_id}", response_class=HTMLResponse)
async def get_results_page(request: Request, submission_id: str):
    """
    Muestra la página HTML con el resultado, mensaje de procesando,
    mensaje de error, o mensaje de no encontrado.
    """
    result_value = results_store.get(submission_id)
    is_processing = submission_id in processing_status
    status = ""
    status_code = 200 # Default status code

    if is_processing:
        status = "processing"
        logger.info(f"[{submission_id}] A\u00FAn procesando.")
    elif result_value == GEMINI_ERROR_MARKER:
        status = "error"
        logger.warning(f"[{submission_id}] Se encontr\u00F3 un marcador de error.")
    elif result_value is not None: # Existe y no es el marcador de error
        status = "success"
        logger.info(f"[{submission_id}] Resultado encontrado con \u00E9xito.")
    else: # No está procesando, no hay resultado ni error guardado
        status = "not_found"
        status_code = 404 # Not Found
        logger.warning(f"[{submission_id}] No se encontr\u00F3 resultado ni est\u00E1 en proceso (not_found).")

    context = {
        "request": request,
        "submission_id": submission_id,
        "result": result_value if status == "success" else None, # Solo pasa el resultado si es exitoso
        "status": status # Pasa el estado determinado
    }

    return templates.TemplateResponse("results.html", context, status_code=status_code)


@app.get("/")
async def root():
    """Endpoint raíz simple para verificar que la app funciona."""
    return {"message": "Hola! Soy el procesador de Tally a Gemini."}


# --- Para ejecutar localmente (opcional, Vercel usa su propio método) ---
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)