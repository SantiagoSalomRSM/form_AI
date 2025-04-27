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

GEMINI_MODEL_NAME = "gemini-1.5-flash" # Use a valid model

results_store: Dict[str, str] = {}
processing_status: Dict[str, bool] = {}

app = FastAPI(title="Tally Webhook Processor")
templates = Jinja2Templates(directory="templates")

# --- Modelos Pydantic ---
# ... (keep your existing Pydantic models) ...
class TallyField(BaseModel):
    key: str
    label: str
    value: Any
    type: str

class TallyResponseData(BaseModel):
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
    for field in payload.data.fields:
        if field.value:
             value_str = ""
             if isinstance(field.value, list):
                 value_str = ", ".join(map(str, field.value))
             else:
                 value_str = str(field.value)
             prompt_parts.append(f"Pregunta: {field.label}\nRespuesta: {value_str}\n---\n")

    full_prompt = "".join(prompt_parts)
    logger.debug(f"[{submission_id}] Prompt para Gemini: {full_prompt[:200]}...")

    background_tasks.add_task(generate_gemini_response, submission_id, full_prompt)

    logger.info(f"[{submission_id}] Tarea de Gemini iniciada en segundo plano. Respondiendo OK a Tally.")
    return {"status": "ok", "message": "Processing started"}


# --- NEW PUT METHOD (Defined BEFORE the GET for the same path) ---
@app.put("/results/{submission_id}")
async def update_result(submission_id: str, update_data: UpdateResultPayload):
    """
    Placeholder endpoint to update/modify an existing result.
    (Implement the actual update logic here if needed).
    """
    logger.info(f"[{submission_id}] Received PUT request to update result.")

    if submission_id not in results_store and submission_id not in processing_status:
        # Or maybe allow creating via PUT? Depends on your logic (upsert)
        raise HTTPException(status_code=404, detail="Result not found or not yet processed.")

    if submission_id in processing_status:
         raise HTTPException(status_code=409, detail="Result is currently being processed, cannot update yet.")

    # --- Example Update Logic ---
    # You might want to overwrite or modify the existing result
    old_result = results_store.get(submission_id, "N/A")
    results_store[submission_id] = update_data.new_result
    logger.info(f"[{submission_id}] Result updated. Reason: {update_data.reason}. Old result was: {old_result[:50]}...")
    # --- End Example ---

    return {"status": "ok", "submission_id": submission_id, "message": "Result updated successfully."}


# --- GET METHOD (Defined AFTER the PUT for the same path) ---
@app.get("/results/{submission_id}", response_class=HTMLResponse)
async def get_results_page(request: Request, submission_id: str):
    """
    Muestra la página HTML con el resultado de Gemini si está listo,
    o un mensaje de "procesando".
    """
    # ... (keep your existing get_results_page implementation) ...
    logger.info(f"[{submission_id}] Solicitud GET recibida para la página de resultados.")

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
        logger.warning(f"[{submission_id}] No se encontró resultado ni está en proceso.")
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