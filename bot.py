import os
import time
import random
import urllib.parse
import requests
from google import genai

# ==========================================
# 1. CREDENCIALES
# ==========================================
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_PAGE_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")

client = genai.Client(api_key=GEMINI_KEY)

# ==========================================
# 2. PRODUCTOS REALES MARCA LDNIO EN STOCK
# ==========================================
PRODUCTOS_LDNIO = [
    {
        "producto": "Cable original LDNIO Tipo C a Tipo C (doble punta Tipo C)",
        "beneficio": "Carga ultra rápida Power Delivery (PD) de alta potencia para celulares modernos y laptops. Trenzado de alta densidad que no se rompe.",
        "prompt_img": "Commercial product photography of a premium heavy-duty braided dual USB-C to USB-C fast charging cable branded LDNIO style, glowing cyan neon studio light, dark futuristic high-tech surface, 8k resolution, photorealistic"
    },
    {
        "producto": "Cable original LDNIO Tipo C a USB estándar reforzado",
        "beneficio": "Compatible con cargadores estándar, motos, autos y computadoras. Protección reforzada en las uniones para máxima durabilidad.",
        "prompt_img": "Commercial product photography of a durable nylon braided USB-A to USB-C fast charging cable, neon magenta lighting accents, sleek black metallic table, 8k photorealistic product shot"
    },
    {
        "producto": "Cable original LDNIO de carga rápida para iPhone (Lightning / Tipo C a Lightning)",
        "beneficio": "Carga estable y segura sin errores de compatibilidad en iOS, transferencia rápida de datos y blindaje anti-tirones.",
        "prompt_img": "Commercial product photography of a sleek braided fast charging cable for iPhone, subtle purple and electric blue neon glow, cyber tech background, 8k render"
    },
    {
        "producto": "Combo Pro: Cable reforzado LDNIO + Dado (cabezal) de carga rápida",
        "beneficio": "Kit completo listo para usar en pared. Carga a máxima velocidad protegiendo la vida útil de la batería contra sobrecalentamiento.",
        "prompt_img": "Commercial product photography of a braided LDNIO fast charging cable plugged into a compact fast charger wall adapter cube, neon blue lights, dark reflective desk, photorealistic 8k"
    }
]

seleccion = random.choice(PRODUCTOS_LDNIO)

prompt_completo = (
    f"Eres el redactor comercial de la tienda de tecnología 'ARO Tech'. "
    f"Crea un post publicitario persuasivo y directo para Facebook vendiendo el producto: {seleccion['producto']}. "
    f"Destaca que es de la prestigiosa marca internacional LDNIO, conocida por su extrema durabilidad y velocidad real de carga. "
    f"Beneficio clave a resaltar: {seleccion['beneficio']}. "
    "Menciona que se acabaron los cables desechables que se doblan o dejan de cargar en pocas semanas. "
    "Usa emojis estratégicos, llamado a la acción claro y tono vendedor profesional.\n\n"
    "Genera dos partes separadas exactamente por '---PROMPT_IMG---':\n"
    "1. El texto publicitario del post (sin números ni enlaces inventados, solo el copy de venta).\n"
    "---PROMPT_IMG---\n"
    f"2. {seleccion['prompt_img']}"
)

# ==========================================
# 3. GENERACIÓN DE TEXTO CON GEMINI (3.6 / 3.7)
# ==========================================
modelos = ["gemini-3.6-flash", "gemini-3.7-flash"]
respuesta_texto = None

for modelo in modelos:
    for intento in range(3):
        try:
            print(f"Generando contenido con {modelo}...")
            response = client.models.generate_content(
                model=modelo,
                contents=prompt_completo,
            )
            if response.text:
                respuesta_texto = response.text.strip()
                break
        except Exception as e:
            print(f"Reintento {intento + 1} en {modelo}: {e}")
            time.sleep(3)
    if respuesta_texto:
        break

if not respuesta_texto:
    raise Exception("No se pudo obtener respuesta de la API de Gemini.")

# Separar texto del post y prompt visual
if "---PROMPT_IMG---" in respuesta_texto:
    partes = respuesta_texto.split("---PROMPT_IMG---")
    cuerpo_mensaje = partes[0].strip()
    prompt_imagen_ia = partes[1].strip().replace("\n", " ")
else:
    cuerpo_mensaje = respuesta_texto
    prompt_imagen_ia = seleccion["prompt_img"]

# ==========================================
# 4. PIE DE CONTACTO FIJO CON WHATSAPP
# ==========================================
pie_contacto = (
    "\n\n══════════════════════════════\n"
    "⚡ **ARO Tech | Distribuidor de Accesorios LDNIO** ⚡\n"
    "🔌 Calidad original LDNIO: Carga rápida y cables reforzados\n"
    "🔌 Modelos: Tipo C a Tipo C | Tipo C a USB | iPhone (Lightning)\n"
    "🔌 Consulta por tu combo con dado / cabezal de carga rápida\n"
    "💳 Pagos: Contraentrega / Yape / Plin / Transferencias\n"
    "📦 Envíos rápidos y seguros a todo Lima\n"
    "══════════════════════════════\n"
    "📲 **Haz tu pedido al WhatsApp:** +51 910 371 606\n"
    "👉 **Pide directo con un clic aquí:** https://wa.me/51910371606?text=Hola%20ARO%20Tech,%20quiero%20hacer%20un%20pedido%20de%20cables%20LDNIO\n\n"
    "#AROTech #LDNIO #CablesLDNIO #CargaRapida #CablesTipoC #CablesiPhone #AccesoriosLima #TecnologiaPeru"
)

mensaje_final_fb = f"{cuerpo_mensaje}{pie_contacto}"

# ==========================================
# 5. GENERACIÓN Y DESCARGA DE IMAGEN
# ==========================================
print("--- GENERANDO FOTO CON IA ---")
semilla = int(time.time())
url_imagen_ia = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt_imagen_ia)}?width=1080&height=1080&nologo=true&seed={semilla}"

img_response = requests.get(url_imagen_ia, timeout=45)
img_response.raise_for_status()

nombre_archivo = "foto_publicacion.jpg"
with open(nombre_archivo, "wb") as f:
    f.write(img_response.content)

print(f"Foto generada y guardada: {nombre_archivo}")

# ==========================================
# 6. PUBLICACIÓN EN FACEBOOK GRAPH API
# ==========================================
print("--- PUBLICANDO EN FACEBOOK ---")
url_fb = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
payload = {
    "caption": mensaje_final_fb,
    "access_token": FB_PAGE_TOKEN
}

with open(nombre_archivo, "rb") as f:
    files = {"source": f}
    fb_response = requests.post(url_fb, data=payload, files=files)

resultado_fb = fb_response.json()
print("Respuesta de Facebook:", resultado_fb)

if "id" in resultado_fb:
    print("✅ ¡Publicación realizada exitosamente en Facebook!")
else:
    raise Exception(f"❌ Error en la publicación: {resultado_fb}")
