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
# # Cargar la fuente Prelo desde un archivo base64
# with open("fonts/prelo_base64.txt", "r") as f:
#     prelo_base64 = f.read().replace('\n', '')

# st.markdown(f"""
# <style>
# @font-face {{
#   font-family: 'Prelo';
#   src: url(data:font/otf;base64,{prelo_base64}) format('opentype');
#   font-weight: normal;
#   font-style: normal;
# }}
# html, body, [class*="st-"] {{
#     font-family: 'Prelo', 'Segoe UI', Arial, sans-serif !important;
# }}
# </style>
# """, unsafe_allow_html=True)


LOGO_URL = "https://raw.githubusercontent.com/SantiagoSalomRSM/form_AI/master/images/RSM Standard Colour_White letters Logo RGB.png" 

# Título y configuración de la página icono logo
st.set_page_config(page_title="Análisis de Resultados del Formulario",
                   layout="wide",
                   page_icon=LOGO_URL)

# Use columns to center the image
col1, col2, col3 = st.columns([1, 1, 1])
with col2:
    st.image(
        LOGO_URL,
        use_container_width=True, 
    )

#st.title("Análisis de Resultados del Formulario")

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
    result_text = data.data[0]['result_consulting'] if data.data else None # Extraer el resultado 
    user_responses = data.data[0].get('user_responses', None) if data.data else None # Extraer las respuestas del usuario

    # Mostrar el estado del análisis y resultados
    if status == STATUS_PROCESSING:
        progress_text = "⏳ Procesando respuestas... Por favor, espera unos segundos."
        my_bar = st.progress(0, text=progress_text)

        for percent_complete in range(100):
            # take 2 seconds to simulate processing
            time.sleep(0.02)  # Simula el tiempo de procesamiento
            my_bar.progress(percent_complete, text=progress_text)
        
        my_bar.empty()
        st.rerun()

    elif status == STATUS_SUCCESS:
        st.balloons() # Celebrar
        #st.success("Análisis Completado!")
        
        st.markdown(result_text) # Muestra el resultado del análisis

        if user_responses:
            st.divider()
            with st.expander("Mostrar respuestas del usuario"):
                st.markdown(user_responses)

    elif status == STATUS_ERROR:
        st.error(f"El análisis falló con el siguiente mensaje: {result_text}")
    else:
        st.error(f"Status desconocido: {status}.")

except Exception as e:
    st.error(f"Un error ocurrió al buscar los resultados: {e}")

hide_streamlit_style = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)