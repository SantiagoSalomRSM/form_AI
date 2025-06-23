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
from openai import OpenAI

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

# --- Configuración DeepSeek ---
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    logger.error("Error: La variable de entorno DEEPSEEK_API_KEY no está configurada.")
else:
    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
        logger.info("Cliente de DeepSeek configurado correctamente.")
    except Exception as e:
        logger.error(f"Error configurando el cliente de DeepSeek: {e}")


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
        first_label = payload.data.fields[2].label 
        if first_label.strip() == "¿De qué sector es tu empresa o grupo?":
            return "CFO_Form"
    return "Unknown"

def extract_ai_preference(payload: TallyWebhookPayload) -> str:
    """Detecta el AI elegido basándose en la primera label o key."""
    ai_preference = "gemini"  # Valor por defecto
    if payload.data.fields:
        first_answer = payload.data.fields[0]
        first_id = first_answer.value[0] 
        id_to_text = {opt.id: opt.text.lower() for opt in first_answer.options}
        first_chosen = id_to_text.get(first_id, "")
        if "Sí" in first_chosen:
            second_answer = payload.data.fields[1]
            second_id = second_answer.value[0]
            id_to_text = {opt.id: opt.text.lower() for opt in second_answer.options}
            second_chosen = id_to_text.get(second_id, "")
            if "DeepSeek" in second_chosen:
                ai_preference = "deepseek"
            elif "Gemini" in second_chosen:
                ai_preference = "gemini"
    return ai_preference

def generate_prompt(payload: TallyWebhookPayload, submission_id: str, mode: str) -> str:
    """Genera un prompt basado en el tipo de formulario."""

    if mode == "CFO_Form":
        logger.info(f"[{submission_id}] Formulario CFO detectado. Procesando respuestas.")

        # --- Generación del Prompt (sin cambios) ---
        prompt_parts = ["""# Prompt: Analizar Formulario de CFO para Resumen de Seguimiento

                        ## **Tu Rol y Objetivo:**

                        Actúas como un(a) **Estratega Financiero(a) Sénior** en **[Nombre de tu Empresa]**. Tu especialidad es diagnosticar rápidamente los desafíos operativos y financieros que enfrentan los CFOs y destacar caminos claros hacia la mejora.

                        Tu objetivo es analizar las siguientes respuestas de un formulario de diagnóstico completado por un(a) CFO. Basado en sus respuestas, debes generar un resumen personalizado y conciso en **formato Markdown**. Este resumen debe cumplir con los siguientes puntos:

                        1.  **Reconocer** su contribución y demostrar que hemos comprendido sus problemas clave.
                        2.  **Presentar** sus desafíos como oportunidades solucionables y estratégicas.
                        3.  **Posicionar** sutilmente a **[Nombre de tu Empresa]** como el socio experto que puede guiarles.
                        4.  **Concluir** con una llamada a la acción potente y alentadora para que se pongan en contacto con nosotros.

                        ## **Tono:**

                        Mantén un tono **profesional, seguro y servicial**. Actúas como un colega experto que ofrece una perspectiva valiosa, no como un vendedor. Sé directo(a) pero empático(a), mostrando un entendimiento genuino de su rol y presiones.

                        ## **Estructura del Resultado (Usa este formato Markdown exacto, --- representa un divider):**

                        Por favor, genera el resultado utilizando la siguiente estructura, incluyendo los emojis y el formato en negrita (adapta todos los datos a los del formulario y no escribas nada del estilo: De acuerdo, aquí tienes el resumen del análisis del formulario del CFO, listo para ser usado:):

                        ### 🚀 Gracias: Un Análisis Rápido de tu Situación
                        Agradecemos tu tiempo y transparencia al compartir tus desafíos. Identificamos una clara oportunidad para optimizar tus procesos, especialmente en la gestión de la tesorería y la implementación de un sistema EPM/CPM, que impulse la eficiencia y la toma de decisiones estratégicas en Banca.

                        ---

                        ### 🔑 Desafíos Clave que Hemos Identificado
                        - A pesar de tener un cierre de período rápido (4 días) y una participación alta (10/10) en la definición tecnológica, la valoración (5/10) de la usabilidad del ERP y la necesidad de realizar "cuadres" en Excel sugieren oportunidades de mejora en la **integración y usabilidad del sistema**, impactando la eficiencia del equipo.
                        - La valoración (6/10) del nivel de automatización en el cierre y reporting, junto con la ausencia de un software de EPM/CPM y la gestión manual del flujo de caja, indican una necesidad de **automatizar los procesos de planificación financiera** y presupuestación, liberando recursos para análisis estratégico.
                        - La solicitud de "más inteligencia" para el sistema de reporting señala una oportunidad de mejorar la **capacidad analítica** y el acceso a datos oportunos (7/10 de autonomía del equipo), permitiendo decisiones basadas en datos más rápidas y efectivas.
                        - La mención de SOX como tema relevante de seguridad y control de riesgos refuerza la necesidad de **robustecer las políticas y controles de seguridad** de la información para proteger los datos financieros sensibles y asegurar el cumplimiento normativo.
                        
                        ---

                        ### 💡 Cómo Podemos Ayudar: Tu Camino a Seguir
                        - **Automatizar tu Planificación Financiera:** Implementamos soluciones de EPM/CPM a medida para automatizar y centralizar tus procesos de presupuestación, forecasting y gestión de la tesorería, permitiéndote reaccionar rápidamente a los cambios del mercado y optimizar el flujo de caja.
                        - **Integrar y Mejorar la Usabilidad de tus Sistemas:** Conectamos tus datos de diversas fuentes (ERP, bancos, etc.) en una única plataforma, mejorando la usabilidad de tus sistemas y eliminando la necesidad de recurrir a Excel para tareas como los "cuadres".
                        - **Potenciar el Análisis de Datos:** Agregamos capacidades de Business Intelligence avanzadas a tu sistema de reporting, permitiendo a tu equipo acceder a datos oportunos y tomar decisiones basadas en datos de manera más rápida y efectiva.
                        - **Fortalecer tu Cumplimiento SOX:** Evaluamos y reforzamos tus políticas de seguridad de la información, asegurando el cumplimiento de las regulaciones SOX y protegiendo tus datos financieros más sensibles.
                        
                        ---

                        ### 📞 Hablemos de tu Estrategia
                        Estos son desafíos comunes pero críticos en la ruta del crecimiento, especialmente en el sector bancario. La buena noticia es que tienen solución con el enfoque adecuado.
                        Para empezar a diseñar un plan de acción concreto para tu equipo, simplemente pulsa el botón **'Contactar con RSM'** y envíanos un mensaje. Estaremos encantados de analizar los siguientes pasos contigo.

                        ## **Datos del Formulario del CFO para Analizar:**"""]

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

        # --- Generación del Prompt (sin cambios) ---
        prompt_parts = ["""# Prompt para Gemini: Generar Briefing Interno de Oportunidad de Venta (Análisis de Formulario de CFO)

                        ## **Tu Rol y Objetivo:**

                        Actúas como un(a) **Analista Estratégico de Cuentas** para un equipo de consultoría tecnológica. Tu especialidad es destilar la información de prospectos (CFOs) en un briefing interno accionable.

                        Tu objetivo es analizar las respuestas del formulario de un(a) CFO y generar un **resumen estratégico interno en formato Markdown**. Este documento preparará al equipo de ventas/consultoría para la primera llamada, destacando los puntos de dolor, los ganchos de venta y la estrategia de aproximación.

                        ## **Tono:**

                        **Directo, analítico y estratégico.** Utiliza un lenguaje de negocio claro y orientado a la acción. El objetivo no es vender al CFO, sino **armar al equipo interno** con la inteligencia necesaria para tener éxito. Cero "fluff" de marketing.

                        ## **Estructura del Resultado (Usa este formato Markdown exacto --- representa un divider):**

                        Por favor, genera el resultado utilizando la siguiente estructura, incluyendo los emojis y el formato en negrita (adapta todos los datos a los del formulario(adapta todos los datos a los del formulario y no escribas nada del estilo de: De acuerdo, aquí tienes el resumen del análisis del formulario del CFO, listo para ser usado:):):

                        ### 📋 Briefing de Oportunidad: [Industria del Cliente] - Análisis del CFO
                        -   **Preparado para:** Equipo de Consulting
                        -   **Fuente:** Formulario de Diagnóstico
                        -   **Nivel de Oportunidad:** Alto
                        
                        ---

                        ### 👤 Perfil del Prospecto (CFO)

                        -   **Industria:** Banca
                        -   **Rol:** CFO
                        -   **Tiempo de Cierre de Período:** 4 días (Rápido, indica eficiencia en ciertas áreas).
                        -   **Participación en Definición Tecnológica:** 10/10 (Decisor clave).
                        -   **Valoración Usabilidad ERP:** 5/10 (Punto de dolor significativo).
                        -   **Autonomía del Equipo (Datos):** 7/10 (Bueno, pero con margen de mejora).
                        -   **Nivel de Automatización (Cierre/Reporting):** 6/10 (Oportunidad clara).
                        -   **Tema de Seguridad Relevante:** Cumplimiento SOX.
                        
                        ---

                        ### 🎯 Puntos de Dolor y Ganchos de Venta

                        -   **Fricción con el ERP actual:** A pesar de un cierre rápido, la baja usabilidad (5/10) y el uso de Excel para "cuadres" es un **gancho claro** para nuestra solución de integración y automatización. El equipo es eficiente *a pesar* de sus herramientas, no gracias a ellas.
                        -   **Dependencia de Procesos Manuales:** La ausencia de un software EPM/CPM y la gestión manual del flujo de caja son ineficiencias críticas. Esto representa nuestro **principal ángulo de venta**: la automatización de la planificación financiera para liberar tiempo estratégico.
                        -   **Necesidad de Inteligencia de Negocio:** La petición explícita de "más inteligencia" para el reporting es una puerta de entrada directa para nuestras capacidades de BI. Quieren pasar de reportar el pasado a predecir el futuro.
                        -   **Presión Regulatoria (SOX):** La mención de SOX es un gancho de alto valor. Podemos posicionar nuestras soluciones no solo como una mejora de eficiencia, sino como una **herramienta para robustecer el control interno** y asegurar el cumplimiento.
                        
                        ---

                        ### 💡 Ángulo de Venta y Solución Propuesta

                        -   **Problema:** Procesos manuales y sistemas poco usables que frenan a un equipo eficiente.
                            -   **Nuestra Solución:** Implementación de una plataforma de EPM/CPM que centralice la planificación, presupuestación y forecasting, integrada con su ERP para eliminar los "cuadres" en Excel.
                            -   **Argumento de Venta:** "Te ayudamos a que tus herramientas estén al nivel de tu equipo, automatizando tareas de bajo valor para que puedan enfocarse en el análisis estratégico que la dirección demanda".
                        -   **Problema:** Reporting básico que no ofrece insights accionables.
                            -   **Nuestra Solución:** Desarrollo de dashboards de Business Intelligence a medida, conectados en tiempo real a sus fuentes de datos.
                            -   **Argumento de Venta:** "Transforma tu reporting de un simple espejo retrovisor a un GPS financiero que guíe tus decisiones futuras".
                        -   **Problema:** Riesgo de cumplimiento y seguridad (SOX).
                            -   **Nuestra Solución:** Evaluación y fortalecimiento de controles de acceso y políticas de seguridad dentro de la nueva plataforma.
                            -   **Argumento de Venta:** "Gana eficiencia y, al mismo tiempo, blinda tu operación financiera para cumplir con SOX con total tranquilidad".
                        
                        ---

                        ### ⚠️ Riesgos Potenciales y Próximos Pasos

                        -   **Riesgos a Considerar:**
                            -   Mencionar riesgos a considerar
                        -   **Próximos Pasos Recomendados:**
                            1.  Mencionar próximos pasos recomendados

                        ## **Datos del Formulario del CFO para Analizar:**"""]

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

        return {"status": "ok", "message": "Processing started"}
    
    except Exception as e:
        logger.error(f"[{submission_id}] Error procesando webhook: {e}", exc_info=True)
        # Devolver error 500 si algo falla aquí es crítico
        raise HTTPException(status_code=500, detail="Internal server error")
    

# # --- GET METHOD (Defined AFTER the PUT for the same path) ---
# @app.get("/results/{submission_id}", response_class=HTMLResponse)
# async def get_results_page(request: Request, submission_id: str):

#     final_status = STATUS_NOT_FOUND # Estado por defecto si no encontramos la key de estado
#     result_value = False # Indica si hay resultado
#     error_message = None
#     http_status_code = 404 # Por defecto es Not Found

#     logger.info(f"[{submission_id}] GET /results. Consultando Supabase (ID: {submission_id}).")

#     try:
#         # Obtener el estado en Supabase

#         data = supabase_client.table("form_AI_DB").select("*").eq("submission_id", submission_id).execute()
#         supabase_status = data.data[0]['status'] if data.data else None # Extraer el estado si existe
#         supabase_result = data.data[0]['result_client'] if data.data else None # Extraer el resultado si existe
#         logger.info(f"[{submission_id}] Estado en Supabase: {supabase_status}).")

#         if supabase_status == STATUS_PROCESSING:
#             final_status = STATUS_PROCESSING
#             http_status_code = 200 # Página encontrada, pero está procesando
#             logger.info(f"[{submission_id}] Estado Supabase: {STATUS_PROCESSING}")
#         elif supabase_status == STATUS_SUCCESS:
#             final_status = STATUS_SUCCESS
#             http_status_code = 200
#             result_value = supabase_result
#             logger.info(f"[{submission_id}] Estado Supabase: {STATUS_SUCCESS}. Resultado obtenido.")
#         elif supabase_status == STATUS_ERROR:
#             final_status = STATUS_ERROR
#             http_status_code = 200 # Mostramos la página de error normalmente
#             error_message = supabase_result
#             logger.warning(f"[{submission_id}] Estado Supabase: {STATUS_ERROR}. Mensaje/marcador: {error_message}")
#         elif supabase_status is None:
#             # La key de estado no existe, por lo tanto "not found"
#             final_status = STATUS_NOT_FOUND
#             http_status_code = 404
#             logger.warning(f"[{submission_id}] No se encontró estado en Supabase (ID: {submission_id}).")
#         else:
#             # Estado inesperado guardado en Supabase
#             final_status = STATUS_ERROR
#             http_status_code = 500  # Error interno porque el estado es inválido
#             error_message = f"Error interno: Estado inválido '{supabase_status}' encontrado en Supabase."
#             logger.error(f"[{submission_id}] {error_message}")  

#         # Contexto para la plantilla
#         context = {
#             "request": request,
#             "submission_id": submission_id,
#             "result_client": result_value if final_status == STATUS_SUCCESS else None,
#             "error_message": error_message if final_status == STATUS_ERROR else None,
#             "status": final_status # Pasar el estado final a la plantilla
#         }
#         logger.info(f"linea 270 - [{submission_id}] - request: {request}") #chivato
#         logger.info(f"linea 271 - [{submission_id}] - submission_id: {submission_id}") #chivato
#         logger.info(f"linea 272 - [{submission_id}] - result_client: {result_value}") #chivato
#         logger.info(f"linea 273 - [{submission_id}] - error_message: {error_message}") #chivato 
#         logger.info(f"linea 274 - [{submission_id}] - status: {final_status}") #chivato
#         logger.info(f"linea 275 - [{submission_id}] - status_code: {http_status_code}") #chivato
               
#         return templates.TemplateResponse("results.html", context, status_code=http_status_code)
    
#     except Exception as e:
#         logger.error(f"[{submission_id}] Error inesperado en GET /results: {e}", exc_info=True)
#         # Devolver error 500 si algo falla aquí es crítico
#         context = {"request": request, "submission_id": submission_id, "status": "critical_error", "error_message": "Error interno del servidor."}
#         return templates.TemplateResponse("results.html", context, status_code=500)


@app.get("/")
async def root():
    """Endpoint raíz simple para verificar que la app funciona."""
    return {"message": "Hola! Soy el procesador de Tally a Gemini."}


# --- Para ejecutar localmente (opcional, Vercel usa su propio método) ---
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run("main:app", host="0.0.0.0", port=8000)

