import os
import logging
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field # Pydantic V2 style
from typing import List, Optional, Dict, Any
import google.generativeai as genai
from dotenv import load_dotenv
from supabase import create_client, Client
from openai import OpenAI
from prompts.prompt_parts import CONSULTING_PROMPT, CFO_FORM_PROMPT
from openai import OpenAIError, APIError

# --- Configuración Inicial ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

# --- Elegir el modelo a usar ---
# MODEL = "gemini" 
# MODEL = "deepseek" 
MODEL = "openai" 

if MODEL == "gemini":
    logger.info("Usando modelo Gemini para la generación de contenido.")
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
elif MODEL == "deepseek":
    logger.info("Usando modelo DeepSeek para la generación de contenido.")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    if not DEEPSEEK_API_KEY:
        logger.error("Error: La variable de entorno DEEPSEEK_API_KEY no está configurada.")
    else:
        try:
            client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
            logger.info("Cliente de DeepSeek configurado correctamente.")
        except Exception as e:
            logger.error(f"Error configurando el cliente de DeepSeek: {e}")
elif MODEL == "openai":
    logger.info("Usando modelo OpenAI para la generación de contenido.")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    if not OPENAI_API_KEY:
        logger.error("Error: La variable de entorno OPENAI_API_KEY no está configurada.")
    else:
        try:
            client = OpenAI(api_key=OPENAI_API_KEY)
            logger.info("Cliente de OpenAI configurado correctamente.")
        except Exception as e:
            logger.error(f"Error configurando el cliente de OpenAI: {e}")

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
DEEPSEEK_ERROR_MARKER = "ERROR_PROCESSING_DEEPSEEK" # Marcador en el resultado
OPENAI_ERROR_MARKER = "ERROR_PROCESSING_OPENAI" # Marcador en el resultado

# --- App FastAPI ---
app = FastAPI(title="Tally Webhook Processor")

# --- Modelos Pydantic ---
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

# Función para detectar el tipo de formulario
def detect_form_type(payload: TallyWebhookPayload) -> str:
    """Detecta el form type basándose en la primera label o key."""
    type = "CFO_Form"  # Valor por defecto
    if payload.data.fields:
        first_label = payload.data.fields[0].label 
        if first_label.strip() == "¿De qué sector es tu empresa o grupo?":
            return "CFO_Form"
    return type

# Función para generar el prompt basado en el tipo de formulario
def generate_prompt(payload: TallyWebhookPayload, submission_id: str, mode: str) -> str:
    """Genera un prompt basado en el tipo de formulario."""

    if mode == "CFO_Form":
        logger.info(f"[{submission_id}] Formulario CFO detectado. Procesando respuestas.")

        # --- Generación del Prompt ---
        prompt_parts = [CFO_FORM_PROMPT]

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

    elif mode == "consulting":
        logger.info(f"[{submission_id}] Formulario CFO detectado. Procesando respuestas.")

        # --- Generación del Prompt ---
        prompt_parts = [CONSULTING_PROMPT]

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
        prompt_parts = ["Analiza la siguiente respuesta de encuesta de un CFO\n", "Proporciona un resumen o conclusión en formato markdown:\n\n"]

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
async def generate_gemini_response(submission_id: str, prompt: str, prompt_type: str):
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
            if prompt_type == "consulting":
                try:
                    supabase_client.table("form_AI_DB").update({
                        "submission_id": submission_id,
                        "status": STATUS_SUCCESS,
                        "result_consulting": result_text
                    }).eq("submission_id", submission_id).execute()
                    logger.info(f"[{submission_id}] Resultado guardado en Supabase.")
                    logger.info(f"[{submission_id}] Estado '{STATUS_SUCCESS}' y resultado guardados en Supabase.")
                except Exception as e:
                    logger.error(f"[{submission_id}] Error guardando resultado en Supabase: {e}")
            else:
                try:
                    supabase_client.table("form_AI_DB").update({
                        "submission_id": submission_id,
                        "status": STATUS_SUCCESS,
                        "result_client": result_text
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
                    "result_client": GEMINI_ERROR_MARKER,
                    "result_consulting": GEMINI_ERROR_MARKER
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
                "result_client": f"Error interno: {e}",
                "result_consulting": f"Error interno: {e}"
            }).eq("submission_id", submission_id).execute()
            logger.warning(f"[{submission_id}] Estado '{STATUS_ERROR}' guardado en Supabase debido a excepción.")
        except Exception as e:
            logger.error(f"[{submission_id}] Error guardando estado de error en Supabase: {e}")

    logger.info(f"[{submission_id}] Tarea Gemini finalizada.")

# --- Lógica para interactuar con DeepSeek ---
async def generate_deepseek_response(submission_id: str, prompt: str, prompt_type: str):
    """Genera una respuesta de DeepSeek y actualiza Supabase con el resultado."""
    logger.info(f"[{submission_id}] Iniciando tarea DeepSeek.")
    
    try:
        # --- Llamada a DeepSeek API (lógica sin cambios) ---
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000
        )
        result_text = response.choices[0].message.content if response.choices else None

        # --- Actualizar Supabase con el resultado ---
        if result_text:
            if prompt_type == "consulting":
                try:
                    supabase_client.table("form_AI_DB").update({
                        "submission_id": submission_id,
                        "status": STATUS_SUCCESS,
                        "result_consulting": result_text
                    }).eq("submission_id", submission_id).execute()
                    logger.info(f"[{submission_id}] Resultado guardado en Supabase.")
                    logger.info(f"[{submission_id}] Estado '{STATUS_SUCCESS}' y resultado guardados en Supabase.")
                except Exception as e:
                    logger.error(f"[{submission_id}] Error guardando resultado en Supabase: {e}")
            else:
                try:
                    supabase_client.table("form_AI_DB").update({
                        "submission_id": submission_id,
                        "status": STATUS_SUCCESS,
                        "result_client": result_text
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
                    "result_client": DEEPSEEK_ERROR_MARKER,
                    "result_consulting": DEEPSEEK_ERROR_MARKER
                }).eq("submission_id", submission_id).execute()
                logger.warning(f"[{submission_id}] Estado '{STATUS_ERROR}' y marcador guardados en Supabase (sin texto válido).")
            except Exception as e:
                logger.error(f"[{submission_id}] Error guardando marcador de error en Supabase: {e}")
    except APIError as e:
        error_code = getattr(e, "code", "unknown")
        error_message = getattr(e, "message", str(e))
        logger.error(f"[{submission_id}] DeepSeek APIError: {error_code} - {error_message}")
        # Opcional: guardar el error en Supabase
        supabase_client.table("form_AI_DB").update({
            "submission_id": submission_id,
            "status": STATUS_ERROR,
            "result_client": f"DeepSeek APIError {error_code}: {error_message}",
            "result_consulting": f"DeepSeek APIError {error_code}: {error_message}"
        }).eq("submission_id", submission_id).execute()
    except OpenAIError as e:
        logger.error(f"[{submission_id}] Error de OpenAI durante procesamiento DeepSeek: {e}", exc_info=True)
        supabase_client.table("form_AI_DB").update({
            "submission_id": submission_id,
            "status": STATUS_ERROR,
            "result_client": f"DeepSeek OpenAIError: {str(e)}",
            "result_consulting": f"DeepSeek OpenAIError: {str(e)}"
        }).eq("submission_id", submission_id).execute()
    except Exception as e:
        logger.error(f"[{submission_id}] Excepción durante procesamiento DeepSeek: {e}", exc_info=True) # Log con traceback
        try:
            # Intenta guardar el estado de error incluso si DeepSeek falló
            supabase_client.table("form_AI_DB").update({
                "submission_id": submission_id,
                "status": STATUS_ERROR,
                "result_client": f"Error interno: {e}",
                "result_consulting": f"Error interno: {e}"
            }).eq("submission_id", submission_id).execute()
            logger.warning(f"[{submission_id}] Estado '{STATUS_ERROR}' guardado en Supabase debido a excepción.")
        except Exception as e:
            logger.error(f"[{submission_id}] Error guardando estado de error en Supabase: {e}")

    logger.info(f"[{submission_id}] Tarea DeepSeek finalizada.")

# --- Lógica para interactuar con OpenAI ---
async def generate_openai_response(submission_id: str, prompt: str, prompt_type: str, payload: TallyWebhookPayload):
    """Genera una respuesta de OpenAI y actualiza Supabase con el resultado."""
    logger.info(f"[{submission_id}] Iniciando tarea OpenAI.")
    
    try:
        # --- Llamada a OpenAI API ---
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10000
        )
        result_text = response.choices[0].message.content if response.choices else None

        # --- Actualizar Supabase con el resultado ---
        if result_text:
            if prompt_type == "consulting":
                try:
                    supabase_client.table("form_AI_DB").update({
                        "submission_id": submission_id,
                        "status": STATUS_SUCCESS,
                        "result_consulting": result_text
                    }).eq("submission_id", submission_id).execute()
                    logger.info(f"[{submission_id}] Resultado guardado en Supabase.")
                    logger.info(f"[{submission_id}] Estado '{STATUS_SUCCESS}' y resultado guardados en Supabase.")
                except Exception as e:
                    logger.error(f"[{submission_id}] Error guardando resultado en Supabase: {e}")
            else:
                try:
                    supabase_client.table("form_AI_DB").update({
                        "submission_id": submission_id,
                        "status": STATUS_SUCCESS,
                        "result_client": result_text
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
                    "result_client": OPENAI_ERROR_MARKER,
                    "result_consulting": OPENAI_ERROR_MARKER
                }).eq("submission_id", submission_id).execute()
                logger.warning(f"[{submission_id}] Estado '{STATUS_ERROR}' y marcador guardados en Supabase (sin texto válido).")
            except Exception as e:
                logger.error(f"[{submission_id}] Error guardando marcador de error en Supabase: {e}")

    except Exception as e:
        logger.error(f"[{submission_id}] Excepción durante procesamiento OpenAI: {e}", exc_info=True) # Log con traceback
        try:
            # Intenta guardar el estado de error incluso si OpenAI falló
            supabase_client.table("form_AI_DB").update({
                "submission_id": submission_id,
                "status": STATUS_ERROR,
                "result_client": f"Error interno: {e}",
                "result_consulting": f"Error interno: {e}"
            }).eq("submission_id", submission_id).execute()
            logger.warning(f"[{submission_id}] Estado '{STATUS_ERROR}' guardado en Supabase debido a excepción.")
        except Exception as e:
            logger.error(f"[{submission_id}] Error guardando estado de error en Supabase: {e}")
    
    if prompt_type != "consulting" and result_text:
        prompt_consulting = generate_prompt(payload, submission_id, "consulting")
        await generate_openai_response(submission_id, prompt_consulting, "consulting", payload)
    
    logger.info(f"[{submission_id}] Tarea OpenAI finalizada.")


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
        
        # Extraer información relevante del formulario
        form_type = detect_form_type(payload)
        response = summarize_payload(payload)
        #ai_preference = extract_ai_preference(payload)
        #logger.info(f"[{submission_id}] Form type detectado: {form_type}, AI preference: {ai_preference}")
        supabase_client.table("form_AI_DB").insert({
                "submission_id": submission_id,
                "status": STATUS_PROCESSING,
                "result_client": None,  # Inicialmente no hay resultado"
                "result_consulting": None,  # Inicialmente no hay resultado
                "user_responses": response,  # Resumen legible del payload
                "form_type": form_type  # Tipo de formulario
            }).execute()

        # Si llegamos aquí, la key se creó y se puso en 'processing'
        logger.info(f"[{submission_id}] Estado '{STATUS_PROCESSING}' establecido en Supabase.")

# -------------------------------------------------
        if MODEL == "gemini":
            # --- Generación del Prompt modularizada ---
            prompt_cliente = generate_prompt(payload, submission_id, form_type)
            logger.debug(f"[{submission_id}] Prompt para Gemini: {prompt_cliente[:200]}...")
        
            # --- Iniciar Tarea en Segundo Plano ---
            background_tasks.add_task(generate_gemini_response, submission_id, prompt_cliente, form_type)
            logger.info(f"[{submission_id}] Tarea de Gemini iniciada en segundo plano.")
        
            # --- Generación del Prompt para consultoría ---
            prompt_consulting = generate_prompt(payload, submission_id, "consulting")
            logger.debug(f"[{submission_id}] Prompt para Gemini (Consulting): {prompt_consulting[:200]}...")
    
            # --- Iniciar Tarea en Segundo Plano (después de respuesta cliente) ---
            background_tasks.add_task(generate_gemini_response, submission_id, prompt_consulting, "consulting")
            logger.info(f"[{submission_id}] Tarea de Gemini iniciada en segundo plano.")

        elif MODEL == "deepseek":
            # --- Generación del Prompt modularizada ---
            prompt_cliente = generate_prompt(payload, submission_id, form_type)
            logger.debug(f"[{submission_id}] Prompt para DeepSeek: {prompt_cliente[:200]}...")

            # --- Iniciar Tarea en Segundo Plano ---
            background_tasks.add_task(generate_deepseek_response, submission_id, prompt_cliente, form_type)
            logger.info(f"[{submission_id}] Tarea de DeepSeek iniciada en segundo plano.")

            # --- Generación del Prompt para consultoría ---
            prompt_consulting = generate_prompt(payload, submission_id, "consulting")
            logger.debug(f"[{submission_id}] Prompt para DeepSeek (Consulting): {prompt_consulting[:200]}...")

            # --- Iniciar Tarea en Segundo Plano (después de respuesta cliente) ---
            background_tasks.add_task(generate_deepseek_response, submission_id, prompt_consulting, "consulting")
            logger.info(f"[{submission_id}] Tarea de DeepSeek iniciada en segundo plano.")

        elif MODEL == "openai":
            # --- Generación del Prompt modularizada ---
            prompt_cliente = generate_prompt(payload, submission_id, form_type)
            logger.debug(f"[{submission_id}] Prompt para OpenAI: {prompt_cliente[:200]}...")

            # --- Iniciar Tarea en Segundo Plano ---
            background_tasks.add_task(generate_openai_response, submission_id, prompt_cliente, form_type, payload)
            logger.info(f"[{submission_id}] Tarea de OpenAI iniciada en segundo plano.")

            # # --- Generación del Prompt para consultoría ---
            # prompt_consulting = generate_prompt(payload, submission_id, "consulting")
            # logger.debug(f"[{submission_id}] Prompt para OpenAI (Consulting): {prompt_consulting[:200]}...")

            # # --- Iniciar Tarea en Segundo Plano (después de respuesta cliente) ---
            # background_tasks.add_task(generate_openai_response, submission_id, prompt_consulting, "consulting")
            # logger.info(f"[{submission_id}] Tarea de OpenAI iniciada en segundo plano.")

        return {"status": "ok", "message": "Processing started"}
    
    except Exception as e:
        logger.error(f"[{submission_id}] Error procesando webhook: {e}", exc_info=True)
        # Devolver error 500 si algo falla aquí es crítico
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/")
async def root():
    """Endpoint raíz simple para verificar que la app funciona."""
    return {"message": "Hola! Soy el procesador de Tally a Gemini."}