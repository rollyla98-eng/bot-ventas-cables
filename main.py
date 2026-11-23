import os
import re
import threading
import time
import traceback
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import requests
from fastapi import FastAPI, BackgroundTasks, Request, Response, Query
from google import genai
from google.genai import types

import conversacion
import mercado_pago
import venta_panel

app = FastAPI()
_executor = ThreadPoolExecutor(max_workers=4)

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
WHATSAPP_PEDIDOS = os.environ.get("WHATSAPP_PEDIDOS", "51910371606")
GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v20.0")
# Modelo de Gemini configurable; los de respaldo se prueban si el principal falla.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_MODELS_FALLBACK = [
    m.strip()
    for m in os.environ.get("GEMINI_MODELS_FALLBACK", "gemini-3.5-flash,gemini-2.5-flash").split(",")
    if m.strip()
]

# Filtro de prueba: si tiene valor, el bot SOLO responde a esos números.
MODO_PRUEBA_NUMEROS = os.environ.get("MODO_PRUEBA_NUMEROS", "").strip()
# Horas de silencio en un chat cuando Rolly escribe desde la app Business.
SILENCIO_DUENO_HORAS = float(os.environ.get("SILENCIO_DUENO_HORAS", "4"))

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

# ---------------------------------------------------------------------------
# Filtros de ventas / silencio / prueba (funciones testables)
# ---------------------------------------------------------------------------
# Palabras/frases que califican un chat como ventas (substrings, sin acentos).
_KEYWORDS_VENTA = (
    "panel",
    "panel financiero",
    "cable",
    "cables",
    "precio",
    "precios",
    "comprar",
    "compra",
    "yape",
    "plin",
    "tarjeta",
    "mercado pago",
    "mercadopago",
    "tiktok",
    "shopify",
)


def normalizar_telefono(numero: str | None) -> str:
    """Quita +, espacios, guiones y deja solo dígitos."""
    if not numero:
        return ""
    return re.sub(r"\D", "", str(numero))


def _sin_acentos(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def es_mensaje_venta(texto: str | None, message_obj: dict | None = None) -> bool:
    """True si el texto menciona keywords de ventas o el mensaje trae referral de anuncio."""
    if message_obj and message_obj.get("referral"):
        return True
    t = _sin_acentos(texto or "")
    if not t:
        return False
    # "PANEL" CTA de TikTok ya queda cubierto por substring "panel"
    return any(k in t for k in _KEYWORDS_VENTA)


def numeros_prueba() -> set[str]:
    """Set de teléfonos normalizados de MODO_PRUEBA_NUMEROS (vacío = sin restricción)."""
    raw = MODO_PRUEBA_NUMEROS
    if not raw:
        return set()
    return {normalizar_telefono(p) for p in raw.split(",") if normalizar_telefono(p)}


def en_modo_prueba(telefono: str) -> bool:
    """
    Si MODO_PRUEBA_NUMEROS tiene valor, True solo si el teléfono está en la lista.
    Si está vacío/ausente, True para cualquiera (no hay restricción de prueba).
    """
    permitidos = numeros_prueba()
    if not permitidos:
        return True
    return normalizar_telefono(telefono) in permitidos


def es_mensaje_grupo(message_obj: dict | None, value: dict | None = None) -> bool:
    """Heurística: ignora grupos. Cloud API 1:1 no trae group_id; si aparece, skip."""
    if not message_obj:
        return False
    if message_obj.get("group_id"):
        return True
    # Algunos payloads usan from con sufijo @g.us (estilo on-prem / legacy)
    frm = str(message_obj.get("from") or "")
    if "@g.us" in frm or frm.endswith("-group"):
        return True
    ctx = message_obj.get("context") or {}
    if ctx.get("group_id") or ctx.get("from_me") and "@g.us" in str(ctx.get("id") or ""):
        return True
    # Si contacts/metadata indican conversación de grupo
    if value and (value.get("group_id") or value.get("metadata", {}).get("group_id")):
        return True
    return False


# Memoria en proceso (se pierde al dormir el free plan de Render).
_chats_venta: dict[str, float] = {}  # teléfono normalizado → timestamp de activación
_silencio_dueno: dict[str, float] = {}  # teléfono del cliente → hasta cuándo (epoch)
_historial: dict[str, list[str]] = {}
_MAX_TURNOS = 20
_seen_ids: set[str] = set()


def chat_en_modo_venta(telefono: str) -> bool:
    return normalizar_telefono(telefono) in _chats_venta


def activar_modo_venta(telefono: str) -> None:
    with _lock:
        _chats_venta[normalizar_telefono(telefono)] = time.time()


def marcar_silencio_dueno(telefono_cliente: str, horas: float | None = None) -> None:
    """Cuando Rolly escribe desde la app, el bot se calla en ese chat N horas."""
    h = SILENCIO_DUENO_HORAS if horas is None else horas
    with _lock:
        _silencio_dueno[normalizar_telefono(telefono_cliente)] = time.time() + max(0.0, h) * 3600


def chat_en_silencio(telefono: str) -> bool:
    tel = normalizar_telefono(telefono)
    hasta = _silencio_dueno.get(tel)
    if hasta is None:
        return False
    if time.time() >= hasta:
        with _lock:
            _silencio_dueno.pop(tel, None)
        return False
    return True


def debe_responder(
    telefono: str,
    texto: str | None = None,
    message_obj: dict | None = None,
    value: dict | None = None,
) -> tuple[bool, str]:
    """
    Decide si el bot contesta. Devuelve (ok, motivo_log).
    Orden: grupo → silencio dueño → modo prueba → modo venta / keywords.
    """
    if es_mensaje_grupo(message_obj, value):
        return False, "grupo"
    if chat_en_silencio(telefono):
        return False, "silencio_dueno"
    if not en_modo_prueba(telefono):
        return False, "fuera_modo_prueba"
    if chat_en_modo_venta(telefono):
        return True, "modo_venta"
    if es_mensaje_venta(texto, message_obj):
        activar_modo_venta(telefono)
        return True, "activar_venta"
    return False, "no_es_venta"


# ---------------------------------------------------------------------------
# Shopify (se conserva por si se reactiva cables; NO se usa en el prompt actual)
# ---------------------------------------------------------------------------
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
    """Catálogo en vivo con caché. None si Shopify no responde y no hay copia reciente.
    Conservado por compatibilidad; el prompt actual del Panel NO lo usa.
    """
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


def construir_system_prompt(user_phone: str = "", user_text: str = "") -> str:
    """Prompt: Panel Financiero con plan Gratis (con límites) y plan Pro (sin límites + Asesor IA). Sin cables."""
    return f"""
Eres el asesor por WhatsApp de Aro Control (Lima, Perú).
Producto activo: el {venta_panel.PANEL_NOMBRE} (app web {venta_panel.url_panel()}), con plan Gratis (con límites) y plan {venta_panel.PRO_NOMBRE} ({venta_panel.texto_planes()}, sin límites y con Asesor IA). Las cuentas nuevas empiezan con {venta_panel.PRO_PRUEBA_DIAS} días de {venta_panel.PRO_NOMBRE} gratis.
Responde siempre en español peruano, natural y corto: 2 o 3 líneas, una sola pregunta al final.
No inventes precios, funciones ni beneficios fuera de la información oficial.
Si preguntan por cables u otros productos físicos, di con amabilidad que por ahora no hay cables disponibles. No ofrezcas el Panel salvo que hablen de plata o lo pidan.
Número de contacto (si lo piden): +{WHATSAPP_PEDIDOS}
No hables de APIs ni de código.
Nunca respondas en inglés.

{conversacion.bloque_reglas(user_phone, user_text)}

{venta_panel.bloque_prompt_panel(user_phone)}
""".strip()


gemini_client = genai.Client(api_key=GEMINI_API_KEY)
_MODELOS = [GEMINI_MODEL] + [m for m in GEMINI_MODELS_FALLBACK if m != GEMINI_MODEL]


@app.get("/")
def home():
    return {"status": "Servidor activo", "bot": "ventas-whatsapp"}


@app.post("/cron/seguimiento")
async def cron_seguimiento(request: Request):
    """Dispara los seguimientos. No envía nada si SEGUIMIENTO_ACTIVO=0 (por defecto)."""
    secreto = (os.environ.get("CRON_SECRETO") or "").strip()
    if secreto:
        enviado = (request.headers.get("x-cron-secret") or request.query_params.get("secret") or "")
        if enviado != secreto:
            return Response(content="no autorizado", status_code=401)
    if not conversacion.seguimiento_activo():
        return {"status": "off"}
    hechos = conversacion.procesar_seguimientos(
        en_modo_prueba=en_modo_prueba,
        chat_en_silencio=chat_en_silencio,
    )
    return {"status": "ok", "enviados": len(hechos)}


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


@app.get("/mp/webhook")
def mp_webhook_ping():
    return {"status": "ok"}


@app.post("/mp/webhook")
async def mp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Notificaciones de Mercado Pago. Responde 200 al toque y procesa en segundo plano.
    Si hay MP_WEBHOOK_SECRET y la firma no calza, responde 401 y no activa nada."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    query = {k: v for k, v in request.query_params.items()}
    headers = {k.lower(): v for k, v in request.headers.items()}
    if mercado_pago.webhook_secret() and not mercado_pago.firma_valida(body, headers, query):
        print("MP webhook firma inválida", flush=True)
        return Response(content="firma inválida", status_code=401)
    background_tasks.add_task(mercado_pago.procesar_notificacion, body, query)
    return {"status": "ok"}


def _procesar_ecos_dueno(value: dict) -> None:

    """smb_message_echoes: Rolly escribió desde la app → silenciar ese chat."""
    ecos = value.get("smb_message_echoes") or []
    if not ecos:
        return
    for eco in ecos:
        # En coexistencia, "to" es el cliente al que Rolly le escribió.
        destino = eco.get("to") or (eco.get("recipient_id"))
        if not destino:
            # Fallback: algunos payloads ponen el peer en context o contacts
            continue
        marcar_silencio_dueno(destino)
        print(f"Silencio dueño chat={normalizar_telefono(destino)} horas={SILENCIO_DUENO_HORAS}", flush=True)


def procesar_webhook(data: dict) -> None:
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if value.get("statuses"):
                    print(f"Webhook status update count={len(value.get('statuses') or [])}")

                # Ecos de mensajes que Rolly manda desde la app Business (NO son inbound).
                _procesar_ecos_dueno(value)

                # Solo messages entrantes; nunca tratar smb_message_echoes como mensajes.
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
                        ok, motivo = debe_responder(user_phone, user_text, message_obj, value)
                        if not ok:
                            print(f"Silencio total from={user_phone} motivo={motivo} text={user_text[:60]}", flush=True)
                            continue
                        print(f"Inbound text={user_text[:80]} filtro={motivo}")
                        if venta_panel.procesar_posible_correo(_WA, user_phone, user_text):
                            continue
                        manejar_texto(_WA, user_phone, user_text)

                    elif msg_type == "image":
                        caption = (message_obj.get("image") or {}).get("caption") or ""
                        ok, motivo = debe_responder(user_phone, caption, message_obj, value)
                        # Imagen sin modo venta y sin caption de ventas → silencio total
                        if not ok:
                            print(f"Silencio imagen from={user_phone} motivo={motivo}", flush=True)
                            continue
                        media_id = (message_obj.get("image") or {}).get("id")
                        tratado = media_id and venta_panel.procesar_imagen(
                            _WA, gemini_client, _MODELOS, user_phone, media_id
                        )
                        if not tratado:
                            send_whatsapp_message(
                                user_phone,
                                "Recibí tu imagen 😊 Si es tu comprobante de pago del plan Pro, asegúrate de que se vean "
                                "monto, fecha, hora y destinatario.",
                            )

                    else:
                        # audio, sticker, etc.
                        ok, motivo = debe_responder(user_phone, None, message_obj, value)
                        if not ok:
                            print(f"Silencio tipo={msg_type} from={user_phone} motivo={motivo}", flush=True)
                            continue
                        send_whatsapp_message(
                            user_phone,
                            "Por ahora respondo mejor por texto 😊 "
                            "Cuéntame qué duda tienes del Panel Financiero y te ayudo.",
                        )
    except Exception as e:
        print(f"Error webhook: {e}")
        traceback.print_exc()


def generar_respuesta(user_phone: str, user_text: str) -> str:
    """Llama a Gemini con el hilo (hasta 20 mensajes). El post-proceso va en manejar_texto."""
    hilo = conversacion.historial_texto(user_phone, n=_MAX_TURNOS)
    if not hilo:
        hilo = f"Cliente: {user_text}"
    prompt = (
        "Conversación reciente (responde PRIMERO lo último que dijo el cliente):\n"
        + hilo
        + "\n\nResponde solo el mensaje para el cliente en español, sin prefijos, 2 o 3 líneas, una sola pregunta."
    )

    system_prompt = construir_system_prompt(user_phone, user_text)
    last_err = None
    for nombre in _MODELOS:
        try:
            ai_response = gemini_client.models.generate_content(
                model=nombre,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    http_options=types.HttpOptions(timeout=15000),
                ),
            )
            texto = (ai_response.text or "").strip()
            if texto:
                print(f"Gemini OK model={nombre}")
                return texto
        except Exception as e:
            last_err = e
            print(f"Error Gemini model={nombre}: {e}")

    print(f"Gemini all models failed: {last_err}")
    return "Dame un momento, ya te respondo 🙏"


def manejar_texto(wa, user_phone: str, user_text: str) -> str:
    """Registra el hilo, pide la respuesta, aplica las reglas de conversación y los marcadores."""
    conversacion.registrar_inbound(user_phone, user_text)
    reply = generar_respuesta(user_phone, user_text)
    reply = conversacion.postprocesar(user_phone, reply, texto_in=user_text)
    venta_panel.procesar_respuesta(wa, user_phone, reply)
    conversacion.registrar_outbound(user_phone, reply)
    return reply


def _graph(path: str) -> str:
    return f"https://graph.facebook.com/{GRAPH_API_VERSION}/{path}"


def _wa_headers() -> dict:
    return {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}


def _wa_post(to: str, tipo: str, cuerpo: dict) -> None:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": tipo,
        tipo: cuerpo,
    }
    try:
        res = requests.post(_graph(f"{PHONE_NUMBER_ID}/messages"), json=payload, headers=_wa_headers(), timeout=30)
        print(f"WhatsApp send type={tipo} status={res.status_code} body={res.text[:400]}")
    except Exception as e:
        print(f"WhatsApp send exception: {e}")
        traceback.print_exc()


def send_whatsapp_message(to: str, message: str, preview_url: bool = False) -> None:
    cuerpo = {"body": message[:4000]}
    if preview_url:
        cuerpo["preview_url"] = True
    _wa_post(to, "text", cuerpo)


def send_whatsapp_image(to: str, link: str | None = None, media_id: str | None = None, caption: str = "") -> None:
    imagen = {"id": media_id} if media_id else {"link": link}
    if caption:
        imagen["caption"] = caption[:1024]
    _wa_post(to, "image", imagen)


def send_whatsapp_template(to: str, name: str, lang: str = "es") -> None:
    """Plantilla de WhatsApp (necesaria fuera de la ventana de 24 h). No imprime tokens."""
    if not name:
        return
    cuerpo = {"name": name, "language": {"code": lang or "es"}}
    _wa_post(to, "template", cuerpo)


def download_whatsapp_media(media_id: str) -> tuple[bytes, str]:
    auth = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    meta = requests.get(_graph(media_id), headers=auth, timeout=15)
    meta.raise_for_status()
    info = meta.json()
    archivo = requests.get(info["url"], headers=auth, timeout=30)
    archivo.raise_for_status()
    return archivo.content, info.get("mime_type") or "image/jpeg"


class _WhatsApp:
    """Adaptador que usa venta_panel para enviar y descargar."""

    send_text = staticmethod(send_whatsapp_message)
    send_image = staticmethod(send_whatsapp_image)
    download_media = staticmethod(download_whatsapp_media)


_WA = _WhatsApp()
mercado_pago.configurar_wa(_WA)
conversacion.configurar(enviar_texto=send_whatsapp_message, enviar_plantilla=send_whatsapp_template)
