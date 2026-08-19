import os
import time
import random
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
# 2. CATÁLOGO REAL CON ÁNGULOS PSICOLÓGICOS
# ==========================================
CATALOGO_LDNIO = [
    {
        "archivo_foto": "cable_65w.jpg",
        "nombre": "Cable LDNIO 65W Turbo Power USB-C a USB-C (1 Metro)",
        "especificaciones": "Potencia Turbo 65W, tecnología Power Delivery (PD), chip inteligente que no calienta el equipo. Para celulares de gama alta, tablets y laptops.",
        "arquetipo_publico": "Jóvenes universitarios, gamers, creadores de contenido y usuarios exigentes que odian esperar horas para que su celular llegue al 100%."
    },
    {
        "archivo_foto": "cable_ls441.jpg",
        "nombre": "Cable LDNIO LS441 Carga Rápida 2.4A (1 Metro)",
        "especificaciones": "Carga rápida 2.4A Max, sincronización de datos, material TPE flexible con protección anti-tirones y uniones reforzadas.",
        "arquetipo_publico": "Gente en constante movimiento, delivery, choferes, estudiantes y trabajadores que necesitan un cable de uso rudo que no se rompa ni se pele al doblarlo."
    }
]

producto_hoy = random.choice(CATALOGO_LDNIO)

# Gatillos psicológicos y situaciones cotidianas que conectan con jóvenes
GATILLOS_PSICOLOGICOS = [
    "EL DOLOR DE LA BATERÍA BAJA EN EL PEOR MOMENTO: Estar en la calle, jugando Free Fire/TikTok o a punto de salir y tener solo 4% de batería con un cargador genérico que no sube nada.",
    "LO BARATO SALE CARO: La rabia de gastar 5 o 10 soles en cables bamba que duran una semana, dejan de cargar y terminan malogrando el pin de carga o la batería del teléfono.",
    "USO RUDO Y CERO ESTRÉS: La tranquilidad de tener un cable original LDNIO reforzado que puedes meter a la mochila, doblar o jalar sin miedo a que se rompa el cuello del conector.",
    "POTENCIA Y VELOCIDAD REAL: La satisfacción de ver el aviso de 'Carga Rápida / Turbo Charge' activado en pantalla y tener tu cel listo para el día en minutos."
]

gatillo_del_dia = random.choice(GATILLOS_PSICOLOGICOS)

# ==========================================
# 3. PROMPT ESTRATÉGICO DE NEUROMARKETING
# ==========================================
prompt_marketing = (
    "Eres un copywriter experto en neuromarketing digital y ventas en redes sociales para la tienda 'ARO Tech'.\n"
    "Tu objetivo es crear un post publicitario altamente persuasivo, moderno y viral para Facebook e Instagram.\n\n"
    f"📌 PRODUCTO: {producto_hoy['nombre']}\n"
    f"📌 DETALLES TÉCNICOS: {producto_hoy['especificaciones']}\n"
    f"📌 PÚBLICO OBJETIVO: {producto_hoy['arquetipo_publico']}\n"
    f"🎯 ENFOQUE PSICOLÓGICO DE HOY: {gatillo_del_dia}\n\n"
    "REGLAS OBLIGATORIAS DE REDACCIÓN:\n"
    "1. GANCHO INICIAL DISRUPTIVO (Hook): Inicia con una pregunta o frase corta que capture la atención en los primeros 2 segundos.\n"
    "2. IDENTIFICACIÓN Y DOLOR: Toca la emoción cotidiana del usuario (frustración con cables malos vs. la solución definitiva).\n"
    "3. BENEFICIOS CLAROS (No solo datos técnicos): Explica cómo LDNIO le soluciona la vida real.\n"
    "4. TONO: Juvenil, dinámico, seguro, con autoridad tecnológica y emojis bien colocados. Cero texto aburrido.\n"
    "5. LLAMADO A LA ACCIÓN (CTA): Invita a asegurar su cable escribiendo al WhatsApp antes de que se agoten.\n"
    "6. IMPORTANTE: NO inventes números de teléfono ni enlaces en el cuerpo del texto (el pie de página ya los contiene)."
)

# ==========================================
# 4. GENERACIÓN DE TEXTO CON GEMINI
# ==========================================
modelos = ["gemini-3.6-flash", "gemini-3.7-flash"]
cuerpo_mensaje = None

for modelo in modelos:
    for intento in range(3):
        try:
            print(f"Generando copy estratégico con {modelo}...")
            response = client.models.generate_content(
                model=modelo,
                contents=prompt_marketing,
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

# ==========================================
# 5. PIE DE CONTACTO Y CONVERSIÓN
# ==========================================
pie_contacto = (
    "\n\n══════════════════════════════\n"
    "⚡ **ARO Tech | Accesorios & Cables LDNIO Originales** ⚡\n"
    f"🔌 Modelo en promoción: {producto_hoy['nombre']}\n"
    "🔌 100% Original | Carga Rápida Real | Blindaje Antiroturas\n"
    "🔌 Consulta también por combos con dado / cabezal de pared\n"
    "💳 Medios de pago: Yape / Plin / Transferencias / Contraentrega\n"
    "📦 Envíos rápidos y seguros a todo Lima\n"
    "══════════════════════════════\n"
    "📲 **Pide el tuyo al WhatsApp:** +51 910 371 606\n"
    "👉 **Haz clic aquí y haz tu pedido al instante:** https://wa.me/51910371606?text=Hola%20ARO%20Tech,%20quiero%20hacer%20un%20pedido%20de%20cables%20LDNIO\n\n"
    "#AROTech #LDNIO #CablesLDNIO #CargaRapida #CablesTipoC #CablesiPhone #GamingPeru #AccesoriosLima #TecnologiaPeru"
)

mensaje_final_fb = f"{cuerpo_mensaje}{pie_contacto}"

# ==========================================
# 6. VERIFICACIÓN Y PUBLICACIÓN EN FACEBOOK
# ==========================================
foto_a_publicar = producto_hoy["archivo_foto"]

if not os.path.exists(foto_a_publicar):
    raise Exception(f"No se encontró el archivo '{foto_a_publicar}' en el repositorio. Asegúrate de haberlo subido.")

print(f"--- PUBLICANDO EN FACEBOOK CON FOTO: {foto_a_publicar} ---")
url_fb = f"https://graph.facebook.com/v20.0/{FB_PAGE_ID}/photos"
payload = {
    "caption": mensaje_final_fb,
    "access_token": FB_PAGE_TOKEN
}

with open(foto_a_publicar, "rb") as f:
    files = {"source": f}
    fb_response = requests.post(url_fb, data=payload, files=files)

resultado_fb = fb_response.json()
print("Respuesta de Facebook:", resultado_fb)

if "id" in resultado_fb:
    print("✅ ¡Publicación psicológica y comercial creada con éxito en Facebook!")
else:
    raise Exception(f"❌ Error al publicar en Facebook: {resultado_fb}")
