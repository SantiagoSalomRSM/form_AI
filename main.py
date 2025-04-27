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
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv() # Carga variables de .env para desarrollo local

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    logger.error("Error: La variable de entorno GEMINI_API_KEY no está configurada.")
    # En un caso real, podríamos querer que la app falle al iniciar
    # raise ValueError("API Key de Gemini no encontrada")
else:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        logger.info("Cliente de Gemini configurado correctamente.")
    except Exception as e:
        logger.error(f"Error configurando el cliente de Gemini: {e}")
        # Podríamos fallar aquí también

# Modelo Gemini a usar
# Asegúrate de que es un modelo válido disponible para tu API Key
# Por ejemplo: 'gemini-1.5-flash', 'gemini-pro'
GEMINI_MODEL_NAME = "gemini-2.0-flash"

# --- Almacén de Resultados Temporal ---
# !!! ADVERTENCIA: Esto es SOLO para demostración. !!!
# !!! Se pierde al reiniciar el servidor. NO USAR EN PRODUCCIÓN REAL. !!!
# !!! Considera Redis, Firestore, una DB SQL simple, o incluso archivos si es necesario. !!!
results_store: Dict[str, str] = {}
processing_status: Dict[str, bool] = {} # Para saber si ya está en proceso

# --- Configuración FastAPI y Plantillas ---
app = FastAPI(title="Tally Webhook Processor")
templates = Jinja2Templates(directory="templates")

# --- Modelos Pydantic para el Webhook de Tally ---
# Nota: La estructura REAL de Tally puede variar.
# Inspecciona un JSON real de Tally o consulta su documentación.
# Esta es una estructura *supuesta*.

class TallyField(BaseModel):
    key: str
    label: str
    # El tipo de 'value' puede variar mucho (texto, número, array, etc.)
    # Usamos 'Any' para flexibilidad, pero sé más específico si conoces los tipos.
    value: Any
    type: str # ej: INPUT_TEXT, MULTIPLE_CHOICE, etc.

class TallyResponseData(BaseModel):
    # Ajusta los nombres de campo según el JSON real de Tally
    # A menudo Tally usa 'respondentId' o similar, no 'submissionId' directamente en 'data'
    # pero el 'eventId' o un ID en la URL/payload raíz SÍ podría ser el ID único
    fields: List[TallyField]
    # Podría haber otros campos útiles como 'createdAt', 'respondentId', etc.

class TallyWebhookPayload(BaseModel):
    eventId: str # Usaremos este como el submission_id único
    eventType: str # ej: FORM_RESPONSE
    data: TallyResponseData

# --- Lógica para interactuar con Gemini ---

async def generate_gemini_response(submission_id: str, prompt: str):
    """Llama a la API de Gemini y almacena el resultado."""
    logger.info(f"[{submission_id}] Generando respuesta de Gemini...")
    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        # Asegúrate de que el modelo soporta generate_content directamente con un string
        # O ajusta para usar un formato de contenido más estructurado si es necesario
        response = await model.generate_content_async(prompt)

        # Verifica si la respuesta tiene contenido y texto
        # La estructura exacta puede variar ligeramente según el modelo y la versión de la librería
        if response and hasattr(response, 'text'):
             result_text = response.text
             logger.info(f"[{submission_id}] Respuesta de Gemini recibida.")
             results_store[submission_id] = result_text
        elif response and hasattr(response, 'parts'):
             # A veces la respuesta viene en 'parts'
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
        # Marcar como completado (incluso si hubo error, ya no está "procesando")
        processing_status.pop(submission_id, None) # Elimina la marca de "procesando"

# --- Endpoints FastAPI ---

@app.post("/webhook")
async def handle_tally_webhook(payload: TallyWebhookPayload, background_tasks: BackgroundTasks):
    """
    Recibe los datos de Tally, prepara el prompt y lanza la tarea
    de Gemini en segundo plano.
    """
    submission_id = payload.eventId
    logger.info(f"[{submission_id}] Webhook recibido de Tally.")

    # Evitar procesar el mismo submission ID múltiples veces si Tally reintenta rápido
    if submission_id in results_store or submission_id in processing_status:
        logger.warning(f"[{submission_id}] Ya procesado o en proceso. Ignorando.")
        # Aún así devolvemos 200 para que Tally no siga reintentando
        return {"status": "ok", "message": "Already processed or in progress"}

    # Marcar como en proceso
    processing_status[submission_id] = True

    # 1. Extraer datos y construir el prompt para Gemini
    #    Esta parte es MUY dependiente de tu encuesta específica.
    #    Aquí un ejemplo genérico que concatena preguntas y respuestas.
    prompt_parts = ["Analiza la siguiente respuesta de encuesta y proporciona un resumen o conclusión:\n\n"]
    for field in payload.data.fields:
        # Ignorar campos sin valor o campos ocultos si es necesario
        if field.value:
             # Formatear el valor si es una lista (ej: checkboxes)
             value_str = ""
             if isinstance(field.value, list):
                 value_str = ", ".join(map(str, field.value))
             else:
                 value_str = str(field.value)

             prompt_parts.append(f"Pregunta: {field.label}\nRespuesta: {value_str}\n---\n")

    full_prompt = "".join(prompt_parts)
    logger.debug(f"[{submission_id}] Prompt para Gemini: {full_prompt[:200]}...") # Loguea solo el inicio

    # 2. Llamar a Gemini en segundo plano
    #    Usamos BackgroundTasks para que el webhook responda rápido a Tally (HTTP 200 OK)
    #    mientras el procesamiento de Gemini ocurre después.
    background_tasks.add_task(generate_gemini_response, submission_id, full_prompt)

    # 3. Responder a Tally inmediatamente
    logger.info(f"[{submission_id}] Tarea de Gemini iniciada en segundo plano. Respondiendo OK a Tally.")
    return {"status": "ok", "message": "Processing started"}

@app.get("/results/{submission_id}", response_class=HTMLResponse)
async def get_results_page(request: Request, submission_id: str):
    """
    Muestra la página HTML con el resultado de Gemini si está listo,
    o un mensaje de "procesando".
    """
    logger.info(f"[{submission_id}] Solicitud recibida para la página de resultados.")

    result = results_store.get(submission_id)
    is_processing = submission_id in processing_status

    if result:
        logger.info(f"[{submission_id}] Resultado encontrado. Mostrando página.")
        return templates.TemplateResponse(
            "results.html",
            {"request": request, "submission_id": submission_id, "result": result, "processing": False}
        )
    elif is_processing:
         logger.info(f"[{submission_id}] Aún procesando. Mostrando mensaje de espera.")
         return templates.TemplateResponse(
            "results.html",
            {"request": request, "submission_id": submission_id, "result": None, "processing": True}
        )
    else:
        # Ni está en resultados ni está marcado como procesando -> Probablemente no existe o hubo un error inicial
        logger.warning(f"[{submission_id}] No se encontró resultado ni está en proceso.")
        # Podrías devolver un 404 aquí, pero mostrar la página con un mensaje es más user-friendly
        # raise HTTPException(status_code=404, detail="Resultado no encontrado")
        return templates.TemplateResponse(
            "results.html",
             {"request": request, "submission_id": submission_id, "result": None, "processing": False} # processing:False para indicar que no hay que esperar
        )

@app.get("/")
async def root():
    """Endpoint raíz simple para verificar que la app funciona."""
    return {"message": "Hola! Soy el procesador de Tally a Gemini."}

# --- Para ejecutar localmente (opcional, Vercel usa su propio método) ---
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)