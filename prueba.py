from openai import OpenAI
client = OpenAI(api_key=KEY)

response = client.responses.create(
    model="gpt-4.1-nano",
    input="explain in 10 sentences the formation of the earth"
)

print(response.output_text)