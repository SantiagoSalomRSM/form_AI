KEY = "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"
from openai import OpenAI
client = OpenAI(api_key=KEY)

response = client.responses.create(
    model="gpt-4.1-nano",
    input="explain in 10 sentences the formation of the earth"
)

print(response.output_text)