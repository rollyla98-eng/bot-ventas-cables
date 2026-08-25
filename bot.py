import datetime
import os
import random
import time
import urllib.parse
import requests
from google import genai

# ==========================================
# 1. CATÁLOGO DE PRODUCTOS (FOTOS Y VIDEOS)
# ==========================================
CATALOGO = [
    {
        "archivo": "cable tipo c.mp4",
        "tipo": "video",
        "nombre": "Cable Tipo C Carga Rápida Ultra Resistente",
        "detalles": "Efecto de carga turbo veloz, animación de potencia y cable trenzado anti-quiebre.",
        "precio": "S/ 35 (Promoción: 2 por S/ 60)"
    },
    {
        "archivo": "cable_65w.jpg",
        "tipo": "foto",
        "nombre": "Cable LDNIO 65W Carga Ultra Rápida",
        "detalles": "Potencia de 65W ideal para laptops, celulares y tablets. Conectores reforzados y máxima durabilidad.",
        "precio": "S/ 35 (Lleva 2 por S/ 60)"
    },
    {
        "archivo": "cable_ls441.jpg",
        "tipo": "foto",
        "nombre": "Cable LDNIO LS441 TPE Charge & Sync",
        "detalles": "Material TPE ultra flexible y resistente a tirones. Carga rápida y transferencia estable.",
        "precio": "S/ 25 (Lleva 2 por S/ 45)"
    },
    {
        "archivo": "video_cable.mp4",
        "tipo": "video",
        "nombre": "Cable de Carga Rápida LDNIO de Alta Resistencia",
        "detalles": "Demostración de durabilidad, flexibilidad extrema y velocidad de carga en segundos.",
        "precio": "S/ 35"
    }
]

ANGULOS_VENTA = [
    "Enfoque en evitar la molestia de cables descartables que se rompen en la punta.",
    "Enfoque en velocidad de carga rápida para no perder tiempo pegado al tomacorriente.",
    "Enfoque en llevar el combo de 2 unidades para tener uno en casa y otro en el trabajo/auto.",
    "Enfoque en seguridad para la batería de celulares y laptops gama media y alta."
]

# ==========================================
# 2. CONFIGURACIÓN DE VARIABLES
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
IG_USER_ID = os.environ.get("IG_USER_ID")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "rollyla98-eng/bot-ventas-cables")

DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
dia_actual = DIAS_SEMANA[datetime.datetime.now().weekday()]


def generar_texto_venta(producto):
    """Usa Gemini con ganchos dinámicos y enlaces directos de compra."""
    angulo = random.choice(ANGULOS_VENTA)
    mensaje_wsp = urllib.parse.quote(f"Hola, quiero pedir el {producto['nombre']} que vi en su publicación.")
    link_wsp = f"https://wa.me/51910371606?text={mensaje_wsp}"

    prompt = f"""
    Eres un experto en ventas online en Lima, Perú.
    Escribe un post de alto impacto comercial para Facebook e Instagram.

    - Contexto: Hoy es {dia_actual}.
    - Estrategia del día: {angulo}
    - Producto: {producto['nombre']}
    - Detalles técnicos: {producto['detalles']}
    - Precio/Promoción: {producto['precio']}

    Estructura obligatoria:
    1. Gancho inicial potente adaptado al día ({dia_actual}) y a la estrategia de venta.
    2. 3 beneficios claros con emojis.
    3. Precio y promoción destacados.
    4. Llamado a la acción directo con este enlace exacto:
       👉 Haz tu pedido por WhatsApp aquí: {link_wsp}
    5. Métodos de pago y entrega: Yape / Plin / Efectivo. Envíos express a todo Lima y provincias.
    6. 4 a 5 hashtags relevantes (#CargaRapida #LDNIO #TecnologiaLima #OfertasPeru).

    Devuelve solo el texto final listo para publicar, sin explicaciones ni notas.
    """

    cliente = genai.Client(api_key=GEMINI_API_KEY)
    respuesta = cliente.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
    )
    return respuesta.text.strip()


def publicar_en_facebook(producto, texto):
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        print("⚠️ Facebook no configurado.")
        return

    archivo = producto["archivo"]
    if not os.path.exists(archivo):
        print(f"❌ Archivo {archivo} no encontrado.")
        return

    if producto["tipo"] == "foto":
        url = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
        with open(archivo, "rb") as f:
            res = requests.post(url, files={"source": f}, data={"caption": texto, "access_token": FB_PAGE_ACCESS_TOKEN})
            print(f"📡 Facebook Foto: {res.status_code} - {res.text}")
    else:
        url = f"https://graph-video.facebook.com/v20.0/{FB_PAGE_ID}/videos"
        with open(archivo, "rb") as f:
            res = requests.post(url, files={"source": f}, data={"description": texto, "title": producto["nombre"], "access_token": FB_PAGE_ACCESS_TOKEN})
            print(f"📡 Facebook Video: {res.status_code} - {res.text}")


def publicar_en_instagram(producto, texto):
    if not IG_USER_ID or not FB_PAGE_ACCESS_TOKEN or not GITHUB_REPOSITORY:
        print("⚠️ Instagram no configurado o incompleto.")
        return

    # Usamos main y quote para soportar nombres con espacios como "cable tipo c.mp4"
    archivo_codificado = urllib.parse.quote(producto['archivo'])
    url_media = f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/main/{archivo_codificado}"
    url_crear = f"https://graph.facebook.com/v20.0/{IG_USER_ID}/media"

    if producto["tipo"] == "foto":
        payload = {"image_url": url_media, "caption": texto, "access_token": FB_PAGE_ACCESS_TOKEN}
    else:
        payload = {"media_type": "REELS", "video_url": url_media, "caption": texto, "access_token": FB_PAGE_ACCESS_TOKEN}

    res_crear = requests.post(url_crear, data=payload).json()
    creation_id = res_crear.get("id")

    if not creation_id:
        print("❌ Error al crear contenedor Instagram:", res_crear)
        return

    time.sleep(20 if producto["tipo"] == "video" else 5)

    url_publicar = f"https://graph.facebook.com/v20.0/{IG_USER_ID}/media_publish"
    res_pub = requests.post(url_publicar, data={"creation_id": creation_id, "access_token": FB_PAGE_ACCESS_TOKEN})
    print(f"📡 Instagram Final: {res_pub.status_code} - {res_pub.text}")


# ==========================================
# 3. EJECUCIÓN PRINCIPAL
# ==========================================
if __name__ == "__main__":
    producto_seleccionado = random.choice(CATALOGO)
    print(f"🎯 Producto elegido: {producto_seleccionado['nombre']} ({producto_seleccionado['tipo']})")

    print("🤖 Generando texto persuasivo...")
    texto_publicacion = generar_texto_venta(producto_seleccionado)
    print("\n--- Texto Generado ---\n" + texto_publicacion + "\n")

    print("🚀 Publicando en Facebook...")
    publicar_en_facebook(producto_seleccionado, texto_publicacion)

    print("🚀 Publicando en Instagram...")
    publicar_en_instagram(producto_seleccionado, texto_publicacion)

    print("✅ Proceso completado exitosamente.")
