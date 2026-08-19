import os
import random
import time
import requests
from google import genai

# ==========================================
# 1. CATÁLOGO DE PRODUCTOS (FOTOS Y VIDEOS)
# ==========================================
CATALOGO = [
    {
        "archivo": "cable_65w.jpg",
        "tipo": "foto",
        "nombre": "Cable LDNIO 65W Carga Ultra Rápida",
        "detalles": "Potencia de 65W ideal para laptops, celulares y tablets. Conectores reforzados y máxima durabilidad.",
        "precio": "S/ 35"
    },
    {
        "archivo": "cable_ls441.jpg",
        "tipo": "foto",
        "nombre": "Cable LDNIO LS441 TPE Charge & Sync",
        "detalles": "Material TPE ultra flexible y resistente a tirones. Carga rápida y transferencia de datos estable.",
        "precio": "S/ 25"
    },
    {
        "archivo": "video_cable.mp4",
        "tipo": "video",
        "nombre": "Cable de Carga Rápida LDNIO de Alta Resistencia",
        "detalles": "Demostración de durabilidad, flexibilidad extrema y velocidad de carga en segundos.",
        "precio": "S/ 35"
    }
]

# ==========================================
# 2. CONFIGURACIÓN DE APIS Y VARIABLES
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")
IG_USER_ID = os.environ.get("IG_USER_ID")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")


def generar_texto_venta(producto):
    """Usa la IA oficial de Gemini para redactar un anuncio comercial persuasivo."""
    prompt = f"""
    Eres un experto en marketing digital y ventas en redes sociales en Perú.
    Escribe un post persuasivo y llamativo para Facebook e Instagram vendiendo el siguiente producto:

    - Producto: {producto['nombre']}
    - Características clave: {producto['detalles']}
    - Precio referencia: {producto['precio']}
    - Formato de publicación: {'Video/Reel' if producto['tipo'] == 'video' else 'Foto de producto'}

    Estructura requerida:
    1. Gancho inicial potente (emojis y pregunta o beneficio directo).
    2. 2 o 3 beneficios destacados con viñetas claras.
    3. Llamado a la acción claro (escribir al DM o al WhatsApp para pedidos contra entrega / envíos).
    4. 4 a 6 hashtags relevantes (#CargaRapida #LDNIO #AccesoriosCelular #TecnologiaPeru #Lima).

    Mantén el tono directo, confiable y vendedor. No incluyas notas explicativas, solo el texto final listo para publicar.
    """
    
    cliente = genai.Client(api_key=GEMINI_API_KEY)
    respuesta = cliente.models.generate_content(
        model='gemini-3.6-flash',
        contents=prompt,
    )
    return respuesta.text.strip()


def publicar_en_facebook(producto, texto):
    """Publica foto o video en la página de Facebook."""
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        print("⚠️ Variables de Facebook no configuradas. Saltando...")
        return

    archivo = producto["archivo"]
    if not os.path.exists(archivo):
        print(f"❌ Error: El archivo {archivo} no existe en el repositorio.")
        return

    if producto["tipo"] == "foto":
        url = f"https://graph.facebook.com/v19.0/{FB_PAGE_ID}/photos"
        with open(archivo, "rb") as f:
            files = {"source": f}
            data = {
                "caption": texto,
                "access_token": FB_PAGE_ACCESS_TOKEN
            }
            res = requests.post(url, files=files, data=data)
            print(f"📡 Respuesta Facebook Foto: {res.status_code} - {res.text}")

    elif producto["tipo"] == "video":
        url = f"https://graph-video.facebook.com/v19.0/{FB_PAGE_ID}/videos"
        with open(archivo, "rb") as f:
            files = {"source": f}
            data = {
                "description": texto,
                "title": producto["nombre"],
                "access_token": FB_PAGE_ACCESS_TOKEN
            }
            res = requests.post(url, files=files, data=data)
            print(f"📡 Respuesta Facebook Video: {res.status_code} - {res.text}")


def publicar_en_instagram(producto, texto):
    """Publica foto o video/Reel en la cuenta de Instagram conectada."""
    if not IG_USER_ID or not FB_PAGE_ACCESS_TOKEN or not GITHUB_REPOSITORY:
        print("⚠️ Variables de Instagram no configuradas o incompletas. Saltando...")
        return

    url_media = f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/main/{producto['archivo']}"
    url_crear = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media"
    
    if producto["tipo"] == "foto":
        payload = {
            "image_url": url_media,
            "caption": texto,
            "access_token": FB_PAGE_ACCESS_TOKEN
        }
    else:  # video / reel
        payload = {
            "media_type": "REELS",
            "video_url": url_media,
            "caption": texto,
            "access_token": FB_PAGE_ACCESS_TOKEN
        }

    res_crear = requests.post(url_crear, data=payload)
    datos_crear = res_crear.json()
    print(f"📡 Creación contenedor Instagram: {datos_crear}")

    creation_id = datos_crear.get("id")
    if not creation_id:
        print("❌ No se pudo crear el contenedor en Instagram.")
        return

    if producto["tipo"] == "video":
        print("⏳ Procesando video en Instagram (esperando 20 segundos)...")
        time.sleep(20)
    else:
        time.sleep(5)

    url_publicar = f"https://graph.facebook.com/v19.0/{IG_USER_ID}/media_publish"
    res_publicar = requests.post(url_publicar, data={
        "creation_id": creation_id,
        "access_token": FB_PAGE_ACCESS_TOKEN
    })
    print(f"📡 Publicación Instagram final: {res_publicar.status_code} - {res_publicar.text}")


# ==========================================
# 3. EJECUCIÓN PRINCIPAL
# ==========================================
if __name__ == "__main__":
    producto_seleccionado = random.choice(CATALOGO)
    print(f"🎯 Producto elegido hoy: {producto_seleccionado['nombre']} ({producto_seleccionado['tipo']})")

    print("🤖 Generando texto publicitario con Gemini...")
    texto_publicacion = generar_texto_venta(producto_seleccionado)
    print("\n--- Texto Generado ---\n" + texto_publicacion + "\n----------------------\n")

    print("🚀 Publicando en Facebook...")
    publicar_en_facebook(producto_seleccionado, texto_publicacion)

    print("🚀 Publicando en Instagram...")
    publicar_en_instagram(producto_seleccionado, texto_publicacion)

    print("✅ Proceso completado exitosamente.")
