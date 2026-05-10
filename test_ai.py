import os
import google.generativeai as genai
from dotenv import load_dotenv

# 1. Load your .env file
load_dotenv()

# 2. Get the key and connect
api_key = os.environ.get("GEMINI_API_KEY")
print(f"Checking API Key: {api_key[:10]}... (Hidden for security)")

genai.configure(api_key=api_key)

# 3. Ask Google what models you are allowed to use
print("\n--- AVAILABLE MODELS FOR THIS KEY ---")
try:
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(m.name)
    print("-------------------------------------")
except Exception as e:
    print(f"FATAL ERROR: {e}")