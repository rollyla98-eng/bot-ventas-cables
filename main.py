import os
import traceback
from concurrent.futures import ThreadPoolExecutor

import requests
from fastapi import FastAPI, BackgroundTasks, Request, Response, Query
import google.generativeai as genai

app = FastAPI()
_executor = ThreadPoolExecutor(max_workers=4)

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
Siempre responde en español peruano, claro y corto (2 a 5 oraciones). Sé amable, confiado y orientado a cerrar la venta.
No inventes productos ni precios fuera del catálogo.
Si preguntan por stock, asume disponible salvo que digan lo contrario.
Si mandan saludos, responde breve y ofrece ayuda para elegir cable.
Si dudan entre modelos, recomienda según uso: celular diario → Tipo C o LS441; laptop/carga fuerte → 65W; combo → 2 unidades en promo.
Siempre que encaje, cierra con una pregunta (¿para celular o laptop? ¿uno o el combo?).
Métodos de pago y entrega: Yape / Plin / Efectivo. Envíos express a Lima y provincias.
Número de pedidos (si lo piden): +{WHATSAPP_PEDIDOS}
No menciones que eres una IA ni hables de APIs o código.
Nunca respondas en inglés.

{CATALOGO_TEXTO}
""".strip()

genai.configure(api_key=GEMINI_API_KEY)
_MODELOS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-flash-latest",
    "gemini-1.5-flash",
]

_historial: dict[str, list[str]] = {}
_MAX_TURNOS = 8
_seen_ids: set[str] = set()


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
        print("Webhook GET verify OK")
        return Response(content=hub_challenge, media_type="text/plain")
    print("Webhook GET verify FAIL")
    return Response(content="Token inválido", status_code=403)


@app.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    print(f"Webhook POST keys={list(data.keys())} object={data.get('object')}")
    background_tasks.add_task(procesar_webhook, data)
    return {"status": "ok"}


def procesar_webhook(data: dict) -> None:
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if value.get("statuses"):
                    print(f"Webhook status update count={len(value.get('statuses') or [])}")
                messages = value.get("messages") or []
                print(f"Webhook messages count={len(messages)}")
                for message_obj in messages:
                    mid = message_obj.get("id")
                    if mid and mid in _seen_ids:
                        print(f"Skip duplicate message id={mid}")
                        continue
                    if mid:
                        _seen_ids.add(mid)
                        if len(_seen_ids) > 500:
                            _seen_ids.clear()

                    user_phone = message_obj.get("from")
                    if not user_phone:
                        continue
                    msg_type = message_obj.get("type")
                    print(f"Inbound from={user_phone} type={msg_type}")

                    if msg_type == "text":
                        user_text = (message_obj.get("text") or {}).get("body", "").strip()
                        if not user_text:
                            continue
                        print(f"Inbound text={user_text[:80]}")
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
        traceback.print_exc()


def generar_respuesta(user_phone: str, user_text: str) -> str:
    historial = _historial.setdefault(user_phone, [])
    historial.append(f"Cliente: {user_text}")
    historial[:] = historial[-_MAX_TURNOS:]

    prompt = (
        "Conversación reciente:\n"
        + "\n".join(historial)
        + "\n\nResponde solo el mensaje para el cliente en español, sin prefijos."
    )

    last_err = None
    for nombre in _MODELOS:
        try:
            modelo = genai.GenerativeModel(
                model_name=nombre,
                system_instruction=SYSTEM_PROMPT,
            )
            ai_response = modelo.generate_content(prompt)
            texto = (getattr(ai_response, "text", None) or "").strip()
            if texto:
                print(f"Gemini OK model={nombre}")
                historial.append(f"Asesor: {texto}")
                historial[:] = historial[-_MAX_TURNOS:]
                return texto
        except Exception as e:
            last_err = e
            print(f"Error Gemini model={nombre}: {e}")

    print(f"Gemini all models failed: {last_err}")
    texto = (
        "¡Hola! Vendemos cables LDNIO de carga rápida. "
        "¿Lo necesitas para celular o laptop? Tenemos Tipo C, 65W y LS441."
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
        "text": {"body": message[:4000]},
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=30)
        print(f"WhatsApp send status={res.status_code} body={res.text[:400]}")
    except Exception as e:
        print(f"WhatsApp send exception: {e}")
        traceback.print_exc()
