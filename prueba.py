from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=API_KEY)


response = client.responses.create(
    model="gpt-4.1-nano",
    input="explain in 10 sentences the formation of the earth"
)

print(response.output_text)