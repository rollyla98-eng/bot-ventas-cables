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
# 2. PRODUCTOS Y PROMPTS VISUALES EXACTOS
# ==========================================
PRODUCTOS_LDNIO = [
    {
        "producto": "Cable original LDNIO Tipo C a Tipo C (doble punta Tipo C)",
        "beneficio": "Carga ultra rápida Power Delivery (PD) de alta potencia para celulares modernos y laptops. Trenzado de alta densidad que no se rompe.",
        "prompt_img": "Commercial product photography of a premium braided dual USB-C to USB-C charging cable, neatly coiled on a modern wooden desk, showing both metal USB-C connector tips clearly in focus, clean studio lighting, 8k photorealistic"
    },
    {
        "producto": "Cable original LDNIO Tipo C a USB estándar reforzado",
        "beneficio": "Compatible con cargadores estándar, motos, autos y computadoras. Protección reforzada en las uniones para máxima durabilidad.",
        "prompt_img": "Commercial product photography of a durable black braided USB-A to USB-C charging cable, neatly coiled on a sleek surface, sharp focus on the metal USB connectors, studio lighting, 8k retail photography"
    },
    {
        "producto": "Cable original LDNIO de carga rápida para iPhone (Lightning / Tipo C a Lightning)",
        "beneficio": "Carga estable y segura sin errores de compatibilidad en iOS, transferencia rápida de datos y blindaje anti-tirones.",
        "prompt_img": "Commercial product photography of a premium nylon braided charging cable for Apple iPhone, showing the silver Lightning connector head clearly, coiled neatly on a minimalist desk, bright professional studio lighting, 8k photorealistic"
    },
    {
        "producto": "Combo Pro: Cable reforzado LDNIO + Dado (cabezal) de carga rápida",
        "beneficio": "Kit completo listo para usar en pared. Carga a máxima velocidad protegiendo la vida útil de la batería contra sobrecalentamiento.",
        "prompt_img": "Commercial product photography of a fast-charging set: a braided USB cable neatly plugged into a compact white wall charger adapter cube, placed on a clean modern tabletop, 8k photorealistic commercial shot"
    }
]

seleccion = random.choice(PRODUCTOS_LDNIO)

prompt_completo = (
    f"Eres el redactor comercial de la tienda de tecnología 'ARO Tech'. "
    f"Crea un post publicitario persuasivo y directo para Facebook vendiendo el producto: {seleccion['producto']}. "
    f"Destaca que es de la prestigiosa marca internacional LDNIO, conocida por su extrema durabilidad y velocidad real de carga. "
    f"Beneficio clave a resaltar: {seleccion['beneficio']}. "
    "Menciona que se acabaron los cables desechables que se doblan o dejan de cargar en pocas semanas. "
    "Usa emojis estratégicos, llamado a la acción claro y tono vendedor profesional. "
    "NO incluyas números de teléfono ni enlaces en este texto, solo el copy de venta."
)

# ==========================================
# 3. GENERACIÓN DE TEXTO CON GEMINI
# ==========================================
modelos = ["gemini-3.6-flash", "gemini-3.7-flash"]
cuerpo_mensaje = None

for modelo in modelos:
    for intento in range(3):
        try:
            print(f"Generando texto con {modelo}...")
            response = client.models.generate_content(
                model=modelo,
                contents=prompt_completo,
            )
            if response.text:
                cuerpo_mensaje = response.text.strip()
                break
        except Exception as e:
            print(f"Reintento {intento + 1} en {modelo}: {e}")
            time.sleep(3)
    if cuerpo_mensaje:
        break

if not cuerpo_mensaje:
    raise Exception("No se pudo obtener respuesta de la API de Gemini.")

# Usamos directamente el prompt visual optimizado del producto
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
print("Prompt visual:", prompt_imagen_ia)
semilla = int(time.time())
url_imagen_ia = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt_imagen_ia)}?width=1080&height=1080&nologo=true&seed={semilla}"

img_response = requests.get(url_imagen_ia, timeout=45)
img_response.raise_for_status()

nombre_archivo = "foto_publicacion.jpg"
with open(nombre_archivo, "wb") as f:
    f.write(img_response.content)

print(f"Foto generada y guardada: {nombre_archivo}")

# ==========================================
# 6. PUBLICACIÓN EN FACEBOOK Y EN INSTAGRAM
# ==========================================

# --- A) PUBLICAR EN FACEBOOK ---
print("--- PUBLICANDO EN FACEBOOK ---")
url_fb = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
payload_fb = {
    "caption": mensaje_final_fb,
    "access_token": FB_PAGE_TOKEN
}

with open(nombre_archivo, "rb") as f:
    files = {"source": f}
    fb_response = requests.post(url_fb, data=payload_fb, files=files)

resultado_fb = fb_response.json()
print("Respuesta de Facebook:", resultado_fb)

if "id" in resultado_fb:
    print("✅ ¡Publicado con éxito en Facebook!")
else:
    raise Exception(f"❌ Error en Facebook: {resultado_fb}")

# --- B) PUBLICAR EN INSTAGRAM ---
print("--- PUBLICANDO EN INSTAGRAM ---")
try:
    url_ig_account = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}?fields=instagram_business_account&access_token={FB_PAGE_TOKEN}"
    res_ig = requests.get(url_ig_account).json()
    
    if "instagram_business_account" in res_ig:
        ig_user_id = res_ig["instagram_business_account"]["id"]
        
        url_crear_media = f"https://graph.facebook.com/v20.0/{ig_user_id}/media"
        payload_media = {
            "image_url": url_imagen_ia,
            "caption": mensaje_final_fb,
            "access_token": FB_PAGE_TOKEN
        }
        res_media = requests.post(url_crear_media, data=payload_media).json()
        
        if "id" in res_media:
            creation_id = res_media["id"]
            time.sleep(5)
            
            url_publicar_ig = f"https://graph.facebook.com/v20.0/{ig_user_id}/media_publish"
            payload_publicar = {
                "creation_id": creation_id,
                "access_token": FB_PAGE_TOKEN
            }
            res_final_ig = requests.post(url_publicar_ig, data=payload_publicar).json()
            print("Respuesta de Instagram:", res_final_ig)
            print("✅ ¡Publicado con éxito en Instagram!")
        else:
            print(f"⚠️ No se pudo crear el contenedor en Instagram: {res_media}")
    else:
        print("ℹ️ No hay cuenta de Instagram vinculada a esta página de Facebook.")
except Exception as err:
    print(f"⚠️ Error al conectar con Instagram: {err}")
