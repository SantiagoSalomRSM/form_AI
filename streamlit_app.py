import streamlit as st
from supabase import create_client, Client
import os
import time

# --- Configuración Supabase ---
SUPABASE_URL: str = os.getenv("SUPABASE_URL")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY")

# --- Constantes de estado ---
STATUS_PROCESSING = "processing"
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_NOT_FOUND = "not_found"

# --- Inicializar cliente Supabase ---
if not SUPABASE_URL:
    raise ValueError("SUPABASE_URL environment variable is not set.")
try:
    # Crear cliente Supabase desde la URL. 
    supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    raise ConnectionError(f"No se pudo conectar a Supabase: {e}") from e


# --- Streamlit App UI ---

# Título y configuración de la página (AÑADIR ICONO RSM, mirar page_icon en la documentación de Streamlit)
st.set_page_config(page_title="Análisis de Resultados del Formulario")

st.title("Análisis de Resultados del Formulario")
st.caption("Esta aplicación muestra los resultados del análisis del formulario obtenidos con la IA Gemini.")

# Obtener el ID de envío desde los parámetros de la URL 
submission_id = st.query_params.get("submission_id")

if not submission_id:
    st.warning("Por favor, proporciona un **Submission ID** válido en la URL para ver los resultados.")
    st.stop()

st.info("Buscando resultados para el Submission ID: **{submission_id}**")

# Búsqueda de resultados en Supabase
try:
    # Busca el registro en la base de datos usando el Submission ID
    response = supabase_client.table("form_AI_DB").select("*").eq("submission_id", submission_id).execute()
    data = response.data

    if not data:
        st.error(f"No se encontraron resultados para el Submission ID: **{submission_id}**")
        st.stop()

    status = data.get("status")
    result_text = data.get("result")
    user_responses = data.get("user_responses")

    # Mostrar el estado del análisis y resultados
    if status == STATUS_PROCESSING:
        st.subheader("Análisis en progreso...")
        with st.spinner("La IA está procesando los datos..."):
            # Actualizar la pagina periódicamente para ver si el análisis ha finalizado
            time.sleep(5)  # Espera 5 segundos antes de actualizar
            st.rerun()  # Vuelve a ejecutar la aplicación 
    elif status == STATUS_SUCCESS:
        st.balloons() # Celebrar
        st.subheader("Análisis Completado")
        
        st.markdown("### Análisis del Formulario")
        st.success(result_text) # Muestra el resultado del análisis

        if user_responses:
            with st.expander("Mostrar respuestas del usuario"):
                st.json(user_responses) # Muestra las respuestas originales del usuario

    elif status == STATUS_ERROR:
        st.subheader("Un error ocurrió durante el análisis")
        st.error(f"El nálisis falló con el siguiente mensaje: {result_text}")
    else:
        st.warning(f"Status desconocido: {status}.")

except Exception as e:
    st.error(f"Un error ocurrió al buscar los resultados: {e}")