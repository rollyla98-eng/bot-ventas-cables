"""Venta automática del Panel Financiero por WhatsApp.

Flujo:
1) El asesor (Gemini) responde dudas con la info de panel_financiero.md
   (o de PANEL_INFO_URL si está configurada).
2) Cuando el cliente quiere comprar, se envían las instrucciones de pago y el QR.
3) Cuando el cliente manda una imagen, se descarga de WhatsApp y Gemini Vision
   extrae los datos del comprobante. Las reglas (monto, fecha, hora, destinatario)
   se verifican en el código, no en el modelo.
4) Si todo cuadra: se envía el enlace /copy de Google Sheets y el video tutorial,
   se registra la venta en la hoja de ingresos y se avisa a Rolly (opcional).

Toda la configuración va por variables de entorno. Aquí no hay claves.
"""

import base64
import hashlib
import json
import os
import re
import threading
import time
import traceback
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------------------
# Configuración (variables de entorno)
# ---------------------------------------------------------------------------
TZ = ZoneInfo(os.environ.get("ZONA_HORARIA", "America/Lima"))
PANEL_NOMBRE = os.environ.get("PANEL_NOMBRE", "Panel Financiero")
PANEL_PRECIO = float(os.environ.get("PANEL_PRECIO", "40"))
PANEL_INFO_URL = os.environ.get("PANEL_INFO_URL", "").strip()
PANEL_COPY_URL = os.environ.get("PANEL_COPY_URL", "").strip()
PANEL_VIDEO_URL = os.environ.get("PANEL_VIDEO_URL", "").strip()

PAGO_METODO = os.environ.get("PAGO_METODO", "Plin o Yape (escanea el QR)")
PAGO_TITULAR = os.environ.get("PAGO_TITULAR", "Rolly Arnulfo López Aro")
PAGO_NUMERO = os.environ.get("PAGO_NUMERO", "").strip()  # opcional: número/celular de Plin
PAGO_QR_URL = os.environ.get("PAGO_QR_URL", "").strip()  # enlace HTTPS público a la imagen del QR
PAGO_QR_MEDIA_ID = os.environ.get("PAGO_QR_MEDIA_ID", "").strip()  # alternativa: media id ya subido a WhatsApp
# Palabras que deben aparecer en el destinatario del comprobante (separadas por coma)
PAGO_DESTINATARIO_CLAVES = [
    p.strip()
    for p in os.environ.get("PAGO_DESTINATARIO_CLAVES", "rolly,lopez").split(",")
    if p.strip()
]
PAGO_VENTANA_MIN = int(os.environ.get("PAGO_VENTANA_MIN", "60"))

SHEET_INGRESOS_ID = os.environ.get("SHEET_INGRESOS_ID", "").strip()
SHEET_INGRESOS_RANGO = os.environ.get("SHEET_INGRESOS_RANGO", "Ingresos!A:J")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

NOTIFICAR_A = os.environ.get("NOTIFICAR_A", "").strip()  # opcional: número de Rolly, ej. 519XXXXXXXX

MARCADOR_PAGO = "[[ENVIAR_PAGO_PANEL]]"

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_lock = threading.Lock()
_info_cache = {"text": None, "ts": 0.0}
_entregados: set[str] = set()  # claves de pagos ya entregados (memoria)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


# ---------------------------------------------------------------------------
# 1) Información del producto
# ---------------------------------------------------------------------------
def info_panel() -> str:
    with _lock:
        now = time.time()
        if _info_cache["text"] and now - _info_cache["ts"] < 600:
            return _info_cache["text"]
        texto = None
        if PANEL_INFO_URL:
            try:
                r = requests.get(PANEL_INFO_URL, timeout=10)
                r.raise_for_status()
                texto = r.text[:8000]
            except Exception as e:
                print(f"Error PANEL_INFO_URL: {e}", flush=True)
        if not texto:
            try:
                with open(os.path.join(_BASE_DIR, "panel_financiero.md"), encoding="utf-8") as f:
                    texto = f.read()
            except Exception as e:
                print(f"Error panel_financiero.md: {e}", flush=True)
                texto = f"{PANEL_NOMBRE}: precio S/ {PANEL_PRECIO:.2f}."
        _info_cache.update(text=texto, ts=now)
        return texto


def bloque_prompt_panel() -> str:
    return f"""
Producto activo: el {PANEL_NOMBRE} (Aro Control), plantilla automatizada en Google Sheets.
Información oficial del {PANEL_NOMBRE} (usa SOLO esto; si preguntan algo que no está aquí, di que un asesor
lo confirma pronto; no inventes ni describas funciones o beneficios que no estén aquí):
---
{info_panel()}
---
Precio del {PANEL_NOMBRE}: S/ {PANEL_PRECIO:.2f}.
Pago: {PAGO_METODO} a nombre de {PAGO_TITULAR}. El QR oficial acepta transferencias desde Plin y desde Yape.
No inventes otros medios de pago ni otros montos.
Si preguntan por cables u otros productos físicos, di con amabilidad que por ahora no hay cables disponibles
y ofrece el {PANEL_NOMBRE}.
Cuando el cliente confirme que quiere comprar o pagar el {PANEL_NOMBRE}, responde breve diciendo que le
envías los datos de pago y escribe al final, en una línea aparte, exactamente {MARCADOR_PAGO}
(el sistema lo reemplaza por los datos de pago y el QR). No escribas tú los datos de pago.
""".strip()


# ---------------------------------------------------------------------------
# 2) Instrucciones de pago
# ---------------------------------------------------------------------------
def texto_instrucciones_pago() -> str:
    linea_num = f"\nNúmero: {PAGO_NUMERO}" if PAGO_NUMERO else ""
    return (
        f"Para comprar el {PANEL_NOMBRE} (S/ {PANEL_PRECIO:.2f}):\n"
        f"1) Paga S/ {PANEL_PRECIO:.2f} con Plin o Yape escaneando el QR "
        f"(a nombre de {PAGO_TITULAR}).{linea_num}\n"
        "2) Envíame aquí la captura del pago (que se vea monto, fecha, hora y destinatario).\n"
        "Apenas lo valide te llega el enlace de tu plantilla y el video tutorial. 🙌"
    )


def enviar_instrucciones_pago(wa, to: str) -> None:
    """wa: módulo/objeto con send_text(to, body, preview_url) y send_image(to, link, media_id, caption)."""
    texto = texto_instrucciones_pago()
    if PAGO_QR_URL or PAGO_QR_MEDIA_ID:
        wa.send_image(to, link=PAGO_QR_URL or None, media_id=PAGO_QR_MEDIA_ID or None,
                      caption=f"QR Plin/Yape – {PAGO_TITULAR}")
    wa.send_text(to, texto)


# ---------------------------------------------------------------------------
# 3) Validación del comprobante con Gemini Vision
# ---------------------------------------------------------------------------
_PROMPT_VISION = """
Analiza la imagen. Puede ser una captura de un pago móvil peruano (Plin, Interbank, BCP, BBVA, etc.) o cualquier otra cosa.
Devuelve SOLO un JSON con estas claves:
{
 "es_comprobante": true/false,           // ¿es una constancia de pago/transferencia exitosa?
 "monto": número o null,                  // monto pagado, ej. 40.00
 "moneda": "PEN"/"USD"/null,
 "fecha": "YYYY-MM-DD" o null,            // fecha de la operación tal como aparece
 "hora": "HH:MM" (24h) o null,
 "destinatario": texto o null,            // nombre de quien RECIBE el pago, tal como aparece
 "numero_operacion": texto o null,        // código/número de operación si aparece
 "app": texto o null,
 "confianza": número entre 0 y 1,         // tu seguridad de que los datos leídos son correctos
 "observaciones": texto corto
}
No inventes datos: si algo no se lee con claridad, pon null.
Si la imagen parece editada o no es un pago exitoso, pon es_comprobante=false.
""".strip()


def analizar_comprobante(gemini_client, modelos: list[str], imagen: bytes, mime: str) -> dict | None:
    from google.genai import types

    for nombre in modelos:
        try:
            resp = gemini_client.models.generate_content(
                model=nombre,
                contents=[types.Part.from_bytes(data=imagen, mime_type=mime or "image/jpeg"), _PROMPT_VISION],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0,
                    http_options=types.HttpOptions(timeout=30000),
                ),
            )
            texto = (resp.text or "").strip()
            texto = re.sub(r"^```(?:json)?|```$", "", texto).strip()
            datos = json.loads(texto)
            print(f"Vision OK model={nombre} datos={ {k: datos.get(k) for k in ('es_comprobante','monto','fecha','hora','confianza')} }", flush=True)
            return datos
        except Exception as e:
            print(f"Error Vision model={nombre}: {e}", flush=True)
    return None


def _parse_hora(fecha: str, hora: str) -> datetime | None:
    try:
        h = re.sub(r"\s+", " ", hora.strip().lower().replace(".", ""))
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %I:%M %p"):
            try:
                return datetime.strptime(f"{fecha} {h}", fmt).replace(tzinfo=TZ)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def verificar(datos: dict | None, ahora: datetime | None = None) -> tuple[bool, list[str]]:
    """Reglas conservadoras. Devuelve (valido, motivos_de_rechazo)."""
    if not datos:
        return False, ["no se pudo leer la imagen"]
    ahora = ahora or datetime.now(TZ)
    motivos = []
    if not datos.get("es_comprobante"):
        motivos.append("no parece un comprobante de pago")
    try:
        monto = float(str(datos.get("monto")).replace(",", "."))
        if abs(monto - PANEL_PRECIO) > 0.009:
            motivos.append(f"monto {monto:.2f} distinto de {PANEL_PRECIO:.2f}")
    except Exception:
        motivos.append("monto ilegible")
    if (datos.get("moneda") or "PEN").upper() not in ("PEN", "S/", "SOLES"):
        motivos.append("moneda distinta a soles")
    fecha = datos.get("fecha") or ""
    if fecha != ahora.strftime("%Y-%m-%d"):
        motivos.append(f"fecha {fecha or 'ilegible'} no es hoy")
    else:
        dt = _parse_hora(fecha, datos.get("hora") or "")
        if not dt:
            motivos.append("hora ilegible")
        elif dt > ahora + timedelta(minutes=5) or ahora - dt > timedelta(minutes=PAGO_VENTANA_MIN):
            motivos.append(f"hora {datos.get('hora')} fuera de los últimos {PAGO_VENTANA_MIN} min")
    dest = _norm(datos.get("destinatario") or "")
    faltan = [c for c in PAGO_DESTINATARIO_CLAVES if _norm(c) not in dest]
    if not dest or faltan:
        motivos.append(f"destinatario '{datos.get('destinatario')}' no coincide")
    try:
        if float(datos.get("confianza") or 0) < 0.8:
            motivos.append("lectura poco segura")
    except Exception:
        motivos.append("lectura poco segura")
    return (not motivos), motivos


# ---------------------------------------------------------------------------
# 4) Hoja de ingresos (Google Sheets con cuenta de servicio)
# ---------------------------------------------------------------------------
_sheets_session = None


def _sesion_sheets():
    global _sheets_session
    if _sheets_session is not None:
        return _sheets_session
    if not (SHEET_INGRESOS_ID and GOOGLE_SERVICE_ACCOUNT_JSON):
        return None
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    raw = GOOGLE_SERVICE_ACCOUNT_JSON
    if not raw.startswith("{"):
        raw = base64.b64decode(raw).decode("utf-8")  # se acepta también en base64
    creds = service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    _sheets_session = AuthorizedSession(creds)
    return _sheets_session


def _url_valores(sufijo: str = "") -> str:
    rango = requests.utils.quote(SHEET_INGRESOS_RANGO, safe="")
    return f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_INGRESOS_ID}/values/{rango}{sufijo}"


def ya_registrado(clave: str) -> bool:
    if clave in _entregados:
        return True
    s = _sesion_sheets()
    if not s:
        return False
    try:
        r = s.get(_url_valores(), timeout=15)
        r.raise_for_status()
        for fila in r.json().get("values", []):
            if clave in fila:
                _entregados.add(clave)
                return True
    except Exception as e:
        print(f"Error leyendo hoja de ingresos: {e}", flush=True)
    return False


def registrar_venta(telefono: str, datos: dict, clave: str, estado: str) -> bool:
    s = _sesion_sheets()
    if not s:
        print("Hoja de ingresos no configurada; venta no registrada", flush=True)
        return False
    fila = [
        datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        PANEL_NOMBRE,
        f"{PANEL_PRECIO:.2f}",
        PAGO_METODO,
        telefono,
        datos.get("destinatario") or "",
        datos.get("numero_operacion") or "",
        f"{datos.get('fecha') or ''} {datos.get('hora') or ''}".strip(),
        clave,
        estado,
    ]
    try:
        r = s.post(_url_valores(":append"), params={"valueInputOption": "USER_ENTERED"},
                   json={"values": [fila]}, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"Error registrando venta: {e}", flush=True)
        return False


# ---------------------------------------------------------------------------
# Orquestación cuando llega una imagen
# ---------------------------------------------------------------------------
def procesar_imagen(wa, gemini_client, modelos: list[str], telefono: str, media_id: str) -> bool:
    """Devuelve True si la imagen se trató como comprobante (válido o no)."""
    try:
        imagen, mime = wa.download_media(media_id)
    except Exception as e:
        print(f"Error descargando media: {e}", flush=True)
        return False
    datos = analizar_comprobante(gemini_client, modelos, imagen, mime)
    if not datos or not datos.get("es_comprobante"):
        if datos is None:
            wa.send_text(telefono, "Recibí tu imagen, pero no pude revisarla ahora. Un asesor la revisa y te escribe pronto. 🙏")
            notificar(wa, f"⚠️ No se pudo analizar una imagen de +{telefono}. Revisar a mano.")
            return True
        return False  # no es un comprobante: que siga el flujo normal

    h = hashlib.sha256(imagen).hexdigest()[:16]
    op = re.sub(r"\W", "", str(datos.get("numero_operacion") or ""))
    clave = f"op:{op}" if op else f"img:{h}"

    with _lock:
        duplicado = ya_registrado(clave) or ya_registrado(f"img:{h}")
        if not duplicado:
            valido, motivos = verificar(datos)
            if valido:
                _entregados.update({clave, f"img:{h}"})
    if duplicado:
        wa.send_text(telefono, "Este pago ya fue registrado antes. Si no te llegó el enlace, un asesor te ayuda en breve. 🙌")
        notificar(wa, f"ℹ️ +{telefono} reenvió un comprobante ya registrado ({clave}).")
        return True

    if not valido:
        print(f"Pago rechazado tel={telefono} motivos={motivos}", flush=True)
        wa.send_text(telefono, "Gracias por tu captura. No pude validar el pago automáticamente; "
                               "un asesor lo revisa y te escribe en breve. 🙏")
        notificar(wa, f"❌ Pago NO validado de +{telefono}: " + "; ".join(motivos))
        return True

    if not (PANEL_COPY_URL and PANEL_VIDEO_URL):
        wa.send_text(telefono, "¡Pago recibido! ✅ En breve un asesor te envía tu plantilla y el tutorial.")
        notificar(wa, f"✅ Pago validado de +{telefono} pero faltan PANEL_COPY_URL/PANEL_VIDEO_URL: enviar a mano.")
        registrar_venta(telefono, datos, clave, "validado-sin-entrega")
        return True

    wa.send_text(telefono, f"¡Pago validado! ✅ Gracias por tu compra del {PANEL_NOMBRE}.\n\n"
                           f"1) Haz tu copia de la plantilla aquí:\n{PANEL_COPY_URL}", preview_url=True)
    wa.send_text(telefono, f"2) Mira el video tutorial para empezar:\n{PANEL_VIDEO_URL}", preview_url=True)
    ok = registrar_venta(telefono, datos, clave, "entregado")
    notificar(wa, f"✅ Venta {PANEL_NOMBRE} S/ {PANEL_PRECIO:.2f} validada y entregada a +{telefono}."
                  + ("" if ok else " (OJO: no se pudo registrar en la hoja)"))
    return True


def notificar(wa, texto: str) -> None:
    if NOTIFICAR_A:
        try:
            wa.send_text(NOTIFICAR_A, texto)
        except Exception:
            traceback.print_exc()
