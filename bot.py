import os
import time
import urllib.parse
import requests
from google import genai

# Leer claves secretas de GitHub
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_PAGE_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")

# Configurar cliente de Gemini
client = genai.Client(api_key=GEMINI_KEY)

# 1. GENERAR TEXTO PUBLICITARIO Y PROMPT VISUAL CON GEMINI
prompt_completo = (
    "Eres el creador de contenido de la tienda 'El Comelon 420'. "
    "Elige al azar uno de estos productos: cables de carga rápida reforzados, cargadores de alta potencia tipo C, "
    "adaptadores de carga para autos/motos, o powerbanks con pantalla digital. "
    "Genera dos cosas separadas por el texto '---PROMPT_IMG---':\n"
    "1. Una publicación llamativa para Facebook con emojis, llamada a la acción y WhatsApp.\n"
    "---PROMPT_IMG---\n"
    "2. Una descripción corta en inglés para generar una foto publicitaria 8k realista del producto con iluminación neón y fondo oscuro elegante."
)

modelos = ["gemini-3.6-flash", "gemini-3.7-flash"]
respuesta_texto = None

for modelo in modelos:
    for intento in range(3):
        try:
            print(f"Consultando a {modelo}...")
            response = client.models.generate_content(
                model=modelo,
                contents=prompt_completo,
            )
            respuesta_texto = response.text.strip()
            break
        except Exception as e:
            print(f"Reintentando conexión... ({e})")
            time.sleep(3)
    if respuesta_texto:
        break

# Separar texto para Facebook y descripción para la imagen
if "---PROMPT_IMG---" in respuesta_texto:
    partes = respuesta_texto.split("---PROMPT_IMG---")
    mensaje_facebook = partes[0].strip()
    prompt_imagen_ia = partes[1].strip().replace("\n", " ")
else:
    mensaje_facebook = respuesta_texto
    prompt_imagen_ia = "Commercial product photography of fast charging braided USB cables, neon studio lights, 8k render, photorealistic"

print("--- MENSAJE FACEBOOK ---")
print(mensaje_facebook)
print("--- PROMPT DE LA FOTO DIBUJADA ---")
print(prompt_imagen_ia)

# 2. LA IA DIBUJA LA IMAGEN DEL PRODUCTO
print("--- GENERANDO FOTO CON IA ---")
url_imagen_ia = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt_imagen_ia)}?width=1080&height=1080&nologo=true&seed={int(time.time())}"

img_response = requests.get(url_imagen_ia, timeout=30)
nombre_archivo = "foto_producto_ia.jpg"

with open(nombre_archivo, "wb") as f:
    f.write(img_response.content)

print(f"Foto generada con éxito: {nombre_archivo}")

# 3. PUBLICAR LA FOTO + TEXTO EN FACEBOOK
url_fb = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
payload = {
    "caption": mensaje_facebook,
    "access_token": FB_PAGE_TOKEN
}

with open(nombre_archivo, "rb") as f:
    files = {"source": f}
    fb_response = requests.post(url_fb, data=payload, files=files)

print("--- RESPUESTA DE FACEBOOK ---")
print(fb_response.json())
