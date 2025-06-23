import base64

with open("fonts/Prelo-Book.otf", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
    print(b64)