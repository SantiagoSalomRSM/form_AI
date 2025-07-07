from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=API_KEY)


response = client.responses.create(
    model="gpt-4.1",
    input="in my project i have a prompt generator as a prompt.py file, that's concatenated to a certain info and then sent to the model. i want to know if the prompt can be modified "
print(response.output_text)