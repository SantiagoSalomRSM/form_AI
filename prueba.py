from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=API_KEY)


response = client.responses.create(
    model="gpt-4.1",
    input="explain etinction of dinosaurs")
print(response.output_text)