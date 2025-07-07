from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=API_KEY)


response = client.responses.create(
    model="gpt-4.1",
    input="one doubt: i currently have a project that works the following way: i have a form that is filled by a CFO with tally. the response is then processed by an api deployed in vercel, whi" \
    "ch generates a prompt and sends it to gemini or other ai agents. the responses are then stored in a supabase database. then a streamlit app retrieves the data from supabase and displays " \
    "it in a user-friendly format. i want to know if this can be done with azure, mantaining tally because it allows to trigger webhooks and redirect on completion, but changing everything else.")

print(response.output_text)