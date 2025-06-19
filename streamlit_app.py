import streamlit as st
from supabase import create_client, Client
import os
import time

# --- Configuración Supabase ---
SUPABASE_URL = st.secrets.get("SUPABASE_URL") 
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY")

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
st.set_page_config(layout="wide")

st.title("Análisis de Resultados del Formulario")
st.caption("Esta aplicación muestra los resultados del análisis del formulario obtenidos con la IA Gemini.")

# Obtener el ID de envío desde los parámetros de la URL 
submission_id = st.query_params.get("submission_id")

if not submission_id:
    st.warning("Por favor, proporciona un **Submission ID** válido en la URL para ver los resultados.")
    st.stop()

# Búsqueda de resultados en Supabase
try:
    # Busca el registro en la base de datos usando el Submission ID
    data = supabase_client.table("form_AI_DB").select("*").eq("submission_id", submission_id).execute()

    if not data:
        st.error(f"No se encontraron resultados para el Submission ID: **{submission_id}**")
        st.stop()

    status = data.data[0]['status'] if data.data else None # Extraer el estado 
    result_text = data.data[0]['result'] if data.data else None # Extraer el resultado 
    user_responses = data.data[0].get('user_responses', None) if data.data else None # Extraer las respuestas del usuario

    # Mostrar el estado del análisis y resultados
    if status == STATUS_PROCESSING:
        with st.spinner("La IA está procesando los datos..."):
            # Actualizar la pagina periódicamente para ver si el análisis ha finalizado
            time.sleep(5)  # Espera 5 segundos antes de actualizar
            st.rerun()  # Vuelve a ejecutar la aplicación 
    elif status == STATUS_SUCCESS:
        st.balloons() # Celebrar
        st.success("Análisis Completado!")
        
        st.markdown("### Análisis del Formulario")
        st.markdown(result_text) # Muestra el resultado del análisis

        if user_responses:
            with st.expander("Mostrar respuestas del usuario"):
                st.markdown(user_responses)

    elif status == STATUS_ERROR:
        st.error(f"El análisis falló con el siguiente mensaje: {result_text}")
    else:
        st.error(f"Status desconocido: {status}.")

except Exception as e:
    st.error(f"Un error ocurrió al buscar los resultados: {e}")