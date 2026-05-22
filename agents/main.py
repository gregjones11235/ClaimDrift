import os
from dotenv import load_dotenv

load_dotenv()

from google import genai
from google.genai import types

# 用新 SDK 走 Vertex AI 路径(GOOGLE_GENAI_USE_VERTEXAI=TRUE 已在 .env 里设了)
client = genai.Client(
    vertexai=True,
    project=os.environ["GOOGLE_CLOUD_PROJECT"],
    location=os.environ["GOOGLE_CLOUD_LOCATION"],
)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Say hi in one word.",
)
print("✓ Vertex AI works:", response.text.strip())