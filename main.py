import os
import threading
import time
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

SHOPIFY_SHOP = os.environ.get("SHOPIFY_SHOP", "0hh7zs-bw.myshopify.com")
SHOPIFY_CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET")
_SHOPIFY_API = "2025-01"
_CATALOGO_TTL = 300  # segundos de caché del catálogo
_lock = threading.Lock()
_shop_token = {"value": None, "exp": 0.0}
_catalogo_cache = {"text": None, "ts": 0.0}

INFO_TIENDA = """
Pagos: Yape / Plin / Efectivo.
Envíos: express Lima y provincias.
Para cerrar pedido, pide nombre, distrito/ciudad, producto y cantidad.
"""


def _shopify_token() -> str:
    now = time.time()
    if _shop_token["value"] and now < _shop_token["exp"] - 300:
        return _shop_token["value"]
    res = requests.post(
        f"https://{SHOPIFY_SHOP}/admin/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": SHOPIFY_CLIENT_ID,
            "client_secret": SHOPIFY_CLIENT_SECRET,
        },
        timeout=15,
    )
    res.raise_for_status()
    body = res.json()
    _shop_token["value"] = body["access_token"]
    _shop_token["exp"] = now + float(body.get("expires_in") or 3600)
    return _shop_token["value"]


_QUERY_PRODUCTOS = """
{
  products(first: 50, query: "status:active") {
    nodes {
      title
      description
      variants(first: 20) {
        nodes { title price inventoryQuantity }
      }
    }
  }
}
"""


def _leer_catalogo_shopify() -> str:
    res = requests.post(
        f"https://{SHOPIFY_SHOP}/admin/api/{_SHOPIFY_API}/graphql.json",
        json={"query": _QUERY_PRODUCTOS},
        headers={"X-Shopify-Access-Token": _shopify_token()},
        timeout=15,
    )
    res.raise_for_status()
    data = res.json()
    if data.get("errors"):
        raise RuntimeError(f"Shopify errors: {data['errors']}")
    lineas = []
    for n, p in enumerate(data["data"]["products"]["nodes"], start=1):
        desc = " ".join((p.get("description") or "").split())[:200]
        lineas.append(f"{n}) {p['title']}" + (f" — {desc}" if desc else ""))
        for v in p["variants"]["nodes"]:
            stock = v.get("inventoryQuantity")
            if stock is None:
                estado = "stock no confirmado"
            elif stock > 0:
                estado = f"{stock} en stock"
            else:
                estado = "AGOTADO"
            nombre_v = v["title"] if v["title"] != "Default Title" else "Única"
            lineas.append(f"   - Variante {nombre_v}: S/ {v['price']} ({estado})")
    if not lineas:
        return "Catálogo: no hay productos activos en la tienda ahora mismo."
    return "Catálogo en vivo de la tienda (precios en soles peruanos):\n" + "\n".join(lineas)


def obtener_catalogo():
    """Catálogo en vivo con caché. None si Shopify no responde y no hay copia reciente."""
    with _lock:
        now = time.time()
        if _catalogo_cache["text"] and now - _catalogo_cache["ts"] < _CATALOGO_TTL:
            return _catalogo_cache["text"]
        try:
            texto = _leer_catalogo_shopify()
            _catalogo_cache["text"] = texto
            _catalogo_cache["ts"] = now
            print(f"Shopify catalogo OK chars={len(texto)}", flush=True)
            return texto
        except Exception as e:
            print(f"Error Shopify: {e}", flush=True)
            if _catalogo_cache["text"] and now - _catalogo_cache["ts"] < 3600:
                return _catalogo_cache["text"]
            return None


def construir_system_prompt() -> str:
    catalogo = obtener_catalogo()
    if catalogo:
        bloque = (
            catalogo
            + "\nUsa SOLO estos productos, precios y stock. Si una variante está AGOTADA, dilo y ofrece otra."
        )
    else:
        bloque = (
            "AVISO: ahora mismo no se pudo consultar el catálogo de la tienda. "
            "NO des ningún precio ni confirmes stock. Si preguntan precio o stock, di con amabilidad "
            "que en este momento no puedes confirmar el precio y que en unos minutos le confirmas."
        )
    return f"""
Eres el asesor de ventas por WhatsApp de una tienda en Lima, Perú, que vende cables LDNIO de carga rápida.
Siempre responde en español peruano, claro y corto (2 a 5 oraciones). Sé amable, natural y orientado a cerrar la venta.
No inventes productos, precios, promociones ni stock fuera del catálogo.
Si mandan saludos, responde breve y ofrece ayuda para elegir cable.
Si piden algo que no vendemos (por ejemplo un celular), aclara con amabilidad que solo vendemos cables y ofrece el que le sirva.
Siempre que encaje, cierra con una pregunta.
Número de pedidos (si lo piden): +{WHATSAPP_PEDIDOS}
No menciones que eres una IA ni hables de APIs o código.
Nunca respondas en inglés.

{bloque}
{INFO_TIENDA}
""".strip()


genai.configure(api_key=GEMINI_API_KEY)
_MODELOS = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-flash-latest",
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
                            "Cuéntame qué cable buscas y te ayudo a elegir.",
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

    system_prompt = construir_system_prompt()
    last_err = None
    for nombre in _MODELOS:
        try:
            modelo = genai.GenerativeModel(
                model_name=nombre,
                system_instruction=system_prompt,
            )
            ai_response = modelo.generate_content(prompt, request_options={"timeout": 15})
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
        "En este momento no puedo confirmarte precios, dame unos minutos y te respondo."
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
