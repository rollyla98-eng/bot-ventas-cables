import os
import requests
import google.generativeai as genai

# Leer las claves secretas configuradas en GitHub
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_PAGE_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")

# Configurar Gemini
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Pedirle a Gemini que redacte la publicación
prompt = (
    "Genera una publicación corta, llamativa y con emojis para Facebook "
    "promocionando cables de carga rápida, cargadores y accesorios para la página El Comelon 420. "
    "Incluye llamado a la acción para pedidos por mensaje o WhatsApp. No incluyas comillas ni explicaciones adicionales."
)

response = model.generate_content(prompt)
mensaje_generado = response.text.strip()

print("--- TEXTO GENERADO POR IA ---")
print(mensaje_generado)
print("------------------------------")

# Publicar en el muro de la página de Facebook
url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/feed"
payload = {
    "message": mensaje_generado,
    "access_token": FB_PAGE_TOKEN
}

res = requests.post(url, data=payload)
respuesta = res.json()

print(f"Respuesta de Facebook: {respuesta}")

if res.status_code == 200:
    print("¡Publicado exitosamente en Facebook con IA!")
else:
    raise Exception(f"Error en la publicación: {respuesta}")
