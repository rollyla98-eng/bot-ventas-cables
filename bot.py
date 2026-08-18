import os
import requests
from google import genai

# Leer las claves secretas configuradas en GitHub
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_PAGE_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")

# Configurar el cliente de Gemini
client = genai.Client(api_key=GEMINI_KEY)

# Pedirle a Gemini que redacte la publicación
prompt = (
    "Genera una publicación corta, llamativa y con emojis para Facebook "
    "promocionando cables de carga rápida, cargadores y accesorios para la página El Comelon 420. "
    "Incluye llamado a la acción para pedidos por mensaje o WhatsApp. No incluyas comillas ni explicaciones adicionales."
)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt,
)

mensaje_generado = response.text.strip()

print("--- TEXTO GENERADO POR IA ---")
print(mensaje_generado)

# Publicar el mensaje en Facebook Graph API
url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
payload = {
    "message": mensaje_generado,
    "access_token": FB_PAGE_TOKEN
}

fb_response = requests.post(url, data=payload)

print("--- RESPUESTA DE FACEBOOK ---")
print(fb_response.json())
