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

        if response and hasattr(response, 'text'):
             result_text = response.text
             logger.info(f"[{submission_id}] Respuesta de Gemini recibida.")
             results_store[submission_id] = result_text
        elif response and hasattr(response, 'parts'):
             result_text = "".join(part.text for part in response.parts if hasattr(part, 'text'))
             if result_text:
                logger.info(f"[{submission_id}] Respuesta de Gemini recibida (desde parts).")
                results_store[submission_id] = result_text
             else:
                 logger.warning(f"[{submission_id}] Respuesta de Gemini recibida pero sin contenido de texto en 'parts'. Respuesta: {response}")
                 results_store[submission_id] = "Error: Gemini devolvió una respuesta vacía o inesperada."
        else:
            logger.warning(f"[{submission_id}] Respuesta de Gemini recibida pero sin contenido de texto. Respuesta: {response}")
            results_store[submission_id] = "Error: Gemini devolvió una respuesta vacía o inesperada."

    except Exception as e:
        logger.error(f"[{submission_id}] Error llamando a la API de Gemini: {e}")
        results_store[submission_id] = f"Error al procesar con Gemini: {e}"
    finally:
        processing_status.pop(submission_id, None)

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
        label = field.get('label')
        label_str = "null" if label is None else str(label).strip() # strip() para quitar espacios extra

        # Obtiene el value
        value = field.get('value')

        # Formatea el value según su tipo para que coincida con el ejemplo
        if isinstance(value, list):
            # Si es una lista, une los elementos con coma y envuélvelos en comillas dobles
            value_str = f'"{",".join(map(str, value))}"'
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
    Muestra la página HTML con el resultado de Gemini si está listo,
    o un mensaje de "procesando".
    """
    # ... (keep your existing get_results_page implementation) ...
    logger.info(f"[{submission_id}] Solicitud GET recibida para la página de resultados.")
    time.sleep(30)
    result = results_store.get(submission_id)
    is_processing = submission_id in processing_status
    was_processed = submission_id in results_store # Check if it *ever* existed in results

    context = {
        "request": request,
        "submission_id": submission_id,
        "result": result,
        "processing": is_processing,
         # Add a flag to know if it was not found vs still processing
        "not_found": not is_processing and not was_processed
    }

    if result:
        logger.info(f"[{submission_id}] Resultado encontrado. Mostrando página.")
        return templates.TemplateResponse("results.html", context)
    elif is_processing:
         logger.info(f"[{submission_id}] Aún procesando. Mostrando mensaje de espera.")
         return templates.TemplateResponse("results.html", context)
    else:
        logger.warning(f"[{submission_id}] No se encontró resultado ni está en proceso - {was_processed}")
        # Return 404 status code while still rendering the page with a message
        return templates.TemplateResponse("results.html", context, status_code=404)


@app.get("/")
async def root():
    """Endpoint raíz simple para verificar que la app funciona."""
    return {"message": "Hola! Soy el procesador de Tally a Gemini."}


# --- Para ejecutar localmente (opcional, Vercel usa su propio método) ---
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)