import os
import requests
from fastapi import FastAPI, Request, Response, Query
import google.generativeai as genai

app = FastAPI()

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
WHATSAPP_PEDIDOS = os.environ.get("WHATSAPP_PEDIDOS", "51910371606")

_missing = [
    n
    for n, v in [
        ("WHATSAPP_TOKEN", WHATSAPP_TOKEN),
        ("PHONE_NUMBER_ID", PHONE_NUMBER_ID),
        ("VERIFY_TOKEN", VERIFY_TOKEN),
        ("GEMINI_API_KEY", GEMINI_API_KEY),
    ]
    if not v
]
if _missing:
    raise RuntimeError(
        "Faltan variables de entorno: "
        + ", ".join(_missing)
        + ". Configúralas en tu hosting o en un archivo .env local (no lo subas a GitHub)."
    )

CATALOGO_TEXTO = """
Catálogo actual (precios en soles peruanos):
1) Cable Tipo C Carga Rápida Ultra Resistente — S/ 35 (2 por S/ 60). Trenzado anti-quiebre, carga turbo.
2) Cable LDNIO 65W Carga Ultra Rápida — S/ 35 (2 por S/ 60). Ideal celulares, tablets y laptops.
3) Cable LDNIO LS441 TPE Charge & Sync — S/ 25 (2 por S/ 45). Flexible, resistente a tirones.
4) Cable de Carga Rápida LDNIO Alta Resistencia — S/ 35. Durabilidad y velocidad.

Pagos: Yape / Plin / Efectivo.
Envíos: express Lima y provincias.
Para cerrar pedido, pide nombre, distrito/ciudad, producto y cantidad.
"""

SYSTEM_PROMPT = f"""
Eres el asesor de ventas por WhatsApp de una tienda en Lima, Perú, especializada en cables LDNIO de carga rápida.
Habla en español peruano, claro y corto (2 a 5 oraciones). Sé amable, confiado y orientado a cerrar la venta.
No inventes productos ni precios fuera del catálogo.
Si preguntan por stock, asume disponible salvo que digan lo contrario.
Si mandan saludos, responde breve y ofrece ayuda para elegir cable.
Si dudan entre modelos, recomienda según uso: celular diario → Tipo C o LS441; laptop/carga fuerte → 65W; combo → 2 unidades en promo.
Siempre que encaje, cierra con una pregunta (¿para celular o laptop? ¿uno o el combo?).
Métodos de pago y entrega: Yape / Plin / Efectivo. Envíos express a Lima y provincias.
Número de pedidos (si lo piden): +{WHATSAPP_PEDIDOS}
No menciones que eres una IA ni hables de APIs o código.

{CATALOGO_TEXTO}
""".strip()

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name="gemini-2.0-flash",
    system_instruction=SYSTEM_PROMPT,
)

# Memoria corta por número (se pierde al reiniciar el servidor; suficiente para el chat)
_historial: dict[str, list[str]] = {}
_MAX_TURNOS = 8


@app.get("/")
def home():
    return {"status": "Servidor activo", "bot": "ventas-whatsapp"}


@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(content="Token inválido", status_code=403)


@app.post("/webhook")
async def receive_message(request: Request):
    data = await request.json()
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages") or []
                for message_obj in messages:
                    user_phone = message_obj.get("from")
                    if not user_phone:
                        continue
                    msg_type = message_obj.get("type")

                    if msg_type == "text":
                        user_text = (message_obj.get("text") or {}).get("body", "").strip()
                        if not user_text:
                            continue
                        reply = generar_respuesta(user_phone, user_text)
                        send_whatsapp_message(user_phone, reply)
                    else:
                        send_whatsapp_message(
                            user_phone,
                            "Por ahora respondo mejor por texto 😊 "
                            "¿Buscas cable Tipo C, 65W o el LS441? Te ayudo a elegir.",
                        )
    except Exception as e:
        print(f"Error webhook: {e}")

    return {"status": "ok"}


def generar_respuesta(user_phone: str, user_text: str) -> str:
    historial = _historial.setdefault(user_phone, [])
    historial.append(f"Cliente: {user_text}")
    historial[:] = historial[-_MAX_TURNOS:]

    prompt = (
        "Conversación reciente:\n"
        + "\n".join(historial)
        + "\n\nResponde solo el mensaje para el cliente, sin prefijos."
    )

    try:
        ai_response = model.generate_content(prompt)
        texto = (ai_response.text or "").strip()
        if not texto:
            texto = (
                "¡Hola! Vendemos cables LDNIO de carga rápida. "
                "¿Lo necesitas para celular o laptop?"
            )
    except Exception as e:
        print(f"Error Gemini: {e}")
        texto = (
            "Disculpa, tuve un problema un segundo. "
            "¿Me escribes de nuevo qué cable buscas (Tipo C, 65W o LS441)?"
        )

    historial.append(f"Asesor: {texto}")
    historial[:] = historial[-_MAX_TURNOS:]
    return texto


def send_whatsapp_message(to: str, message: str) -> None:
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": message},
    }
    res = requests.post(url, json=payload, headers=headers, timeout=30)
    print(f"WhatsApp send: {res.status_code} - {res.text[:300]}")
