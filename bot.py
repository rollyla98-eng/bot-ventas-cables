import os
import time
import requests
from google import genai

# Leer las claves secretas configuradas en GitHub
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_PAGE_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")

# Configurar el cliente de Gemini
client = genai.Client(api_key=GEMINI_KEY)

prompt = (
    "Genera una publicación corta, llamativa y con emojis para Facebook "
    "promocionando cables de carga rápida, cargadores y accesorios para la página El Comelon 420. "
    "Incluye llamado a la acción para pedidos por mensaje o WhatsApp. No incluyas comillas ni explicaciones adicionales."
)

# Lista de modelos y reintentos automáticos
modelos = ["gemini-3.6-flash", "gemini-3.7-flash"]
mensaje_generado = None

for modelo in modelos:
    for intento in range(3):
        try:
            print(f"Generando texto con {modelo} (intento {intento + 1})...")
            response = client.models.generate_content(
                model=modelo,
                contents=prompt,
            )
            mensaje_generado = response.text.strip()
            break
        except Exception as e:
            print(f"Servidor ocupado, reintentando en 3s... ({e})")
            time.sleep(3)
    if mensaje_generado:
        break

if not mensaje_generado:
    raise Exception("Los servidores de IA están ocupados temporalmente. Intenta nuevamente en un minuto.")

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
