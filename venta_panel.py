"""Panel Financiero por WhatsApp: plan Gratis con límites y plan Pro sin límites.

Modelo de negocio:
- Plan Gratis (para siempre): hasta PLAN_GRATIS_MOVIMIENTOS movimientos al mes, hasta PLAN_GRATIS_FIADOS
  fiados activos, solo el resumen del mes actual, sin exportar a CSV/Excel y sin Asesor IA.
- Plan Pro: S/ PRO_PRECIO_MENSUAL al mes o S/ PRO_PRECIO_ANUAL al año: movimientos y fiados ilimitados,
  historial completo, reportes, exportación y Asesor IA (este asistente por WhatsApp).
- Cuentas nuevas: PRO_PRUEBA_DIAS días de Pro gratis desde que se entrega el Panel (= se crea la cuenta);
  después pasan a Gratis sin perder datos.
- Los límites del plan Gratis los aplica el Panel (la app); el bot solo los comunica.

Flujo:
1) Gemini responde con la info de panel_financiero.md (o PANEL_INFO_URL) y emite marcadores:
   [[ENVIAR_PANEL]]     → se envían enlace /copy + video sin cobrar y arranca la prueba de Pro.
   [[ENVIAR_PAGO_PRO]]  → se envían el QR y las instrucciones de pago de Pro (y el link de Mercado Pago si hay token).
   [[ENVIAR_LINK_MP]]   → se envía el link de pago con tarjeta (Checkout Pro). Si falta el token, se cae al QR.
   [[ASESOR_IA]]        → la respuesta es del Asesor IA (función Pro); si el cliente no tiene Pro vigente
                          el código la reemplaza por la oferta de Pro con el QR (y el link de tarjeta si hay token).
2) Si el cliente manda una imagen, Gemini Vision lee el comprobante y el código exige: monto exacto
   PRO_PRECIO_MENSUAL (pro_mensual) o PRO_PRECIO_ANUAL (pro_anual), fecha de hoy en Lima, hora dentro de
   PAGO_VENTANA_MIN, destinatario con PAGO_DESTINATARIO_CLAVES y confianza >= 0.8. Sin duplicados.
3) Pago válido: pro_mensual = +PRO_DIAS_MENSUAL días, pro_anual = +PRO_DIAS_ANUAL días, desde hoy o desde
   el vencimiento si Pro aún está vigente. Se guarda en Firestore (ARO Finance Pro) y se avisa a Rolly.
   La hoja de ingresos queda como respaldo opcional si está configurada.

Planes guardados en el bot: prueba_pro, gratis, pro_mensual, pro_anual.
En la app (users/{uid}.subscription) eso es trial / monthly / annual (status active|trial|expired).
Estado: memoria del proceso + Firestore (se relee al arrancar o al primer mensaje).
Si faltan FIREBASE_SERVICE_ACCOUNT_JSON y FIREBASE_PROJECT_ID, el estado queda solo en memoria
(y en la hoja, si está configurada). Toda la configuración va por variables de entorno. Aquí no hay claves.
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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------------------
# Configuración (variables de entorno)
# ---------------------------------------------------------------------------
TZ = ZoneInfo(os.environ.get("ZONA_HORARIA", "America/Lima"))
PANEL_NOMBRE = os.environ.get("PANEL_NOMBRE", "Panel Financiero")
PANEL_INFO_URL = os.environ.get("PANEL_INFO_URL", "").strip()
PANEL_APP_URL = os.environ.get("PANEL_APP_URL", "").strip()
PANEL_COPY_URL = os.environ.get("PANEL_COPY_URL", "").strip()
PANEL_VIDEO_URL = os.environ.get("PANEL_VIDEO_URL", "").strip()
PANEL_APP_DEFAULT = "https://aro-finance-pro.web.app"

PRO_NOMBRE = os.environ.get("PRO_NOMBRE", "Pro")
PRO_PRECIO_MENSUAL = float(os.environ.get("PRO_PRECIO_MENSUAL", "19"))
PRO_PRECIO_ANUAL = float(os.environ.get("PRO_PRECIO_ANUAL", "149"))
PRO_PRUEBA_DIAS = int(os.environ.get("PRO_PRUEBA_DIAS", "15"))
PRO_DIAS_MENSUAL = int(os.environ.get("PRO_DIAS_MENSUAL", "30"))
PRO_DIAS_ANUAL = int(os.environ.get("PRO_DIAS_ANUAL", "365"))
# Límites del plan Gratis (viven en la app; el bot solo los comunica).
PLAN_GRATIS_MOVIMIENTOS = int(os.environ.get("PLAN_GRATIS_MOVIMIENTOS", "30"))
PLAN_GRATIS_FIADOS = int(os.environ.get("PLAN_GRATIS_FIADOS", "3"))

PLANES_PRO = ("prueba_pro", "pro_mensual", "pro_anual")

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
# A:J como antes + K=plan, L=inicio de prueba, M=vencimiento (compatible con filas viejas A:J).
SHEET_INGRESOS_RANGO = os.environ.get("SHEET_INGRESOS_RANGO", "Ingresos!A:M")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

NOTIFICAR_A = os.environ.get("NOTIFICAR_A", "").strip()  # opcional: número de Rolly, ej. 519XXXXXXXX

MARCADOR_PANEL = "[[ENVIAR_PANEL]]"
MARCADOR_PAGO = "[[ENVIAR_PAGO_PRO]]"
MARCADOR_LINK_MP = "[[ENVIAR_LINK_MP]]"
MARCADOR_ASESOR = "[[ASESOR_IA]]"
MARCADORES = (MARCADOR_PANEL, MARCADOR_PAGO, MARCADOR_LINK_MP, MARCADOR_ASESOR)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_lock = threading.RLock()
_info_cache = {"text": None, "ts": 0.0}
_entregados: set[str] = set()  # claves de pagos ya registrados (memoria)
# teléfono → {"plan": "prueba_pro"|"gratis"|"pro_mensual"|"pro_anual"|None,
#             "inicio_prueba": "YYYY-MM-DD"|"", "vencimiento": "YYYY-MM-DD"|""}
_suscripciones: dict[str, dict] = {}


def _ahora() -> datetime:
    return datetime.now(TZ)


def _hoy() -> date:
    return _ahora().date()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def _tel(t: str) -> str:
    return re.sub(r"\D", "", str(t or ""))


def _soles(x: float) -> str:
    return f"S/ {x:.0f}" if float(x).is_integer() else f"S/ {x:.2f}"


def _fecha_txt(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return iso or "?"


def url_panel() -> str:
    """Link real del Panel (app web). Nunca se promete un envío a mano."""
    return PANEL_APP_URL or PANEL_COPY_URL or PANEL_APP_DEFAULT


def texto_planes() -> str:
    return f"{_soles(PRO_PRECIO_MENSUAL)} al mes o {_soles(PRO_PRECIO_ANUAL)} al año"


def texto_plan_gratis() -> str:
    return (f"Plan Gratis (para siempre): hasta {PLAN_GRATIS_MOVIMIENTOS} movimientos al mes, hasta "
            f"{PLAN_GRATIS_FIADOS} fiados activos, solo el resumen del mes actual, sin exportar a CSV/Excel "
            f"y sin Asesor IA.")


def texto_plan_pro() -> str:
    return (f"Plan {PRO_NOMBRE}: {texto_planes()}, con movimientos y fiados ilimitados, historial completo, "
            f"reportes, exportación y Asesor IA.")


def texto_modelo() -> str:
    return (f"{texto_plan_gratis()}\n{texto_plan_pro()}\n"
            f"Cuentas nuevas: empiezan con {PRO_PRUEBA_DIAS} días de {PRO_NOMBRE} gratis y después pasan al plan "
            f"Gratis sin perder sus datos, o pasan a {PRO_NOMBRE} si pagan.")


def _valores_env() -> dict:
    def n(x):
        return f"{x:.0f}" if float(x).is_integer() else f"{x:.2f}"
    return {
        "PLAN_GRATIS_MOVIMIENTOS": str(PLAN_GRATIS_MOVIMIENTOS), "PLAN_GRATIS_FIADOS": str(PLAN_GRATIS_FIADOS),
        "PRO_PRECIO_MENSUAL": n(PRO_PRECIO_MENSUAL), "PRO_PRECIO_ANUAL": n(PRO_PRECIO_ANUAL),
        "PRO_PRUEBA_DIAS": str(PRO_PRUEBA_DIAS), "PRO_DIAS_MENSUAL": str(PRO_DIAS_MENSUAL),
        "PRO_DIAS_ANUAL": str(PRO_DIAS_ANUAL),
    }


def _rellenar(texto: str) -> str:
    """Reemplaza {VARIABLE} por su valor de env (solo las conocidas; el resto del texto queda igual)."""
    for k, v in _valores_env().items():
        texto = texto.replace("{" + k + "}", v)
    return texto


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
                texto = f"{PANEL_NOMBRE}.\n{texto_modelo()}"
        texto = _rellenar(texto)
        _info_cache.update(text=texto, ts=now)
        return texto


def bloque_estado(telefono: str) -> str:
    """Línea para el prompt con el plan actual del cliente."""
    e = estado_plan(telefono)
    if e["pro"]:
        return (f"Estado del cliente: tiene {PRO_NOMBRE} ACTIVO (plan {e['plan']}, vence el "
                f"{_fecha_txt(e['vencimiento'])}). Puedes responder como Asesor IA.")
    if e["plan"] == "gratis":
        return (f"Estado del cliente: está en el plan Gratis (su {PRO_NOMBRE} terminó el "
                f"{_fecha_txt(e['vencimiento'])}). Responde dudas del Panel y de venta. NO respondas como Asesor IA "
                f"ni ayudes con funciones Pro. Si pide pagar o se habla de precio, {PRO_NOMBRE} cuesta {texto_planes()} "
                f"y, si acepta, escribe {MARCADOR_PAGO}.")
    return (f"Estado del cliente: aún no tiene el {PANEL_NOMBRE}. NO respondas como Asesor IA. "
            f"Si habla de plata, gastos, fiados, deudas, negocio o precio (o pide el Panel), "
            f"ofrécele el {PANEL_NOMBRE} gratis (empieza con {PRO_PRUEBA_DIAS} días de {PRO_NOMBRE}); "
            f"si no, solo responde lo que preguntó.")


def bloque_prompt_panel(telefono: str = "") -> str:
    return f"""
Producto activo: el {PANEL_NOMBRE} (Aro Control / ARO Finance Pro), app web en {url_panel()}.
Planes (explícalos con exactitud, sin cambiar números):
{texto_modelo()}
El {PANEL_NOMBRE} se entrega sin cobrar (el enlace de la app; el video tutorial solo si el sistema lo envía). Al entregarlo empieza su
prueba de {PRO_NOMBRE}. NO digas que todo el panel es gratis ni que es ilimitado sin pagar: tiene un plan Gratis con límites y un plan {PRO_NOMBRE} de pago.
NO lo presentes como una hoja de cálculo. NO prometas que un asesor enviará el Panel, el video ni nada que no puedas cumplir.
Información oficial (usa SOLO esto; si preguntan algo que no está aquí, dilo con honestidad y no inventes):
---
{info_panel()}
---
Pago de {PRO_NOMBRE}: Plin/Yape con QR o tarjeta con Mercado Pago.
El QR oficial acepta Plin y Yape, a nombre de {PAGO_TITULAR}.
También se puede pagar con tarjeta (débito o crédito) por Mercado Pago; el sistema envía el link (no lo escribas tú).
Montos exactos: {_soles(PRO_PRECIO_MENSUAL)} (mensual) o {_soles(PRO_PRECIO_ANUAL)} (anual). No inventes otros montos.
Si preguntan por cables u otros productos físicos, di con amabilidad que por ahora no hay cables disponibles.
No ofrezcas el {PANEL_NOMBRE} en ese momento salvo que la persona hable de plata, gastos o lo pida.
Marcadores (escríbelos al final, cada uno en una línea aparte, tal cual; el sistema los reemplaza):
- Si el cliente quiere el {PANEL_NOMBRE} (o empezar su prueba de {PRO_NOMBRE}): {MARCADOR_PANEL} (no escribas tú los enlaces).
- Si el cliente quiere pagar {PRO_NOMBRE}: {MARCADOR_PAGO} (no escribas tú los datos de pago ni los links).
- Si el cliente pide pagar solo con tarjeta o Mercado Pago: {MARCADOR_LINK_MP} (no escribas tú el link).
  Si ya eligió mensual o anual, usa {MARCADOR_LINK_MP}:pro_mensual o {MARCADOR_LINK_MP}:pro_anual.
- Si tu respuesta es del Asesor IA (consejo de finanzas personales, análisis o ayuda con funciones {PRO_NOMBRE}
  como historial, reportes o exportación), empieza con {MARCADOR_ASESOR}. Las dudas de venta o de cómo es el
  {PANEL_NOMBRE} y sus planes NO llevan ese marcador.
{bloque_estado(telefono) if telefono else ''}
""".strip()


# ---------------------------------------------------------------------------
# 2) Entrega del Panel (sin cobrar) = creación de la cuenta → arranca la prueba de Pro
# ---------------------------------------------------------------------------
def entregar_panel(wa, telefono: str) -> bool:
    """Envía el enlace de la app (y el video solo si hay PANEL_VIDEO_URL) y arranca la prueba de Pro (una vez por número).
    Devuelve True si se entregó."""
    tel = _tel(telefono)
    venc = iniciar_prueba_pro(tel)  # None si ya tenía cuenta
    if venc:
        txt_plan = (f"Empiezas con {PRO_PRUEBA_DIAS} días de {PRO_NOMBRE} gratis (hasta el {_fecha_txt(venc)}). "
                    f"Luego sigues en el plan Gratis con límites (hasta {PLAN_GRATIS_MOVIMIENTOS} movimientos al mes "
                    f"y {PLAN_GRATIS_FIADOS} fiados activos) sin perder tus datos, o pasas a {PRO_NOMBRE} por {texto_planes()}.")
    else:
        e = estado_plan(tel)
        txt_plan = (f"Tu plan actual: {e['plan']}" + (f" (vence el {_fecha_txt(e['vencimiento'])})." if e["pro"] else "."))
    link = url_panel()
    wa.send_text(telefono, f"¡Aquí tienes tu {PANEL_NOMBRE}! 🎁\n"
                           f"Entra aquí (es gratis y tienes {PRO_PRUEBA_DIAS} días de {PRO_NOMBRE}):\n{link}",
                 preview_url=True)
    if PANEL_VIDEO_URL:
        wa.send_text(telefono, f"Mira el video para empezar:\n{PANEL_VIDEO_URL}", preview_url=True)
    wa.send_text(telefono, txt_plan)
    try:
        import conversacion as _cv
        _cv.marcar_oferta(tel, stage="prueba" if venc else "ofrecido")
    except Exception:
        pass
    if venc:
        ok = registrar_fila(tel, f"{PANEL_NOMBRE} (cuenta nueva)", 0, estado="entregado", plan="prueba_pro",
                            inicio_prueba=_hoy().isoformat(), vencimiento=venc)
        notificar(wa, f"🎁 {PANEL_NOMBRE} entregado a +{tel}; {PRO_NOMBRE} de prueba hasta {_fecha_txt(venc)}."
                      + ("" if ok else " (OJO: no se pudo registrar en la hoja; solo en memoria)"))
    return True



# ---------------------------------------------------------------------------
# 3) Estado del plan (prueba_pro / gratis / pro_mensual / pro_anual)
# ---------------------------------------------------------------------------
def _cargar_de_hoja(tel: str) -> dict:
    """Estado persistido: Firestore primero, hoja de ingresos como respaldo."""
    sub = {"plan": None, "inicio_prueba": "", "vencimiento": ""}
    try:
        import venta_firestore as vf
        fs = vf.cargar_estado(tel)
        if fs.get("plan") or fs.get("inicio_prueba"):
            return {
                "plan": fs.get("plan"),
                "inicio_prueba": fs.get("inicio_prueba") or "",
                "vencimiento": fs.get("vencimiento") or "",
            }
    except Exception as e:
        print(f"Error leyendo Firestore: {e}", flush=True)
    for fila in _leer_filas():
        fila = list(fila) + [""] * (13 - len(fila))
        if _tel(fila[4]) != tel:
            continue
        if fila[11]:
            sub["inicio_prueba"] = fila[11]
        if fila[10]:
            sub["plan"], sub["vencimiento"] = fila[10], fila[12] or sub["vencimiento"]
    return sub


def _sub(tel: str) -> dict:
    with _lock:
        if tel not in _suscripciones:
            _suscripciones[tel] = _cargar_de_hoja(tel)
        return _suscripciones[tel]


def estado_plan(telefono: str, hoy: date | None = None) -> dict:
    """{'plan', 'inicio_prueba', 'vencimiento', 'pro'}.
    'plan' es el plan efectivo: un plan Pro vencido se muestra como 'gratis'. Pro vigente si hoy < vencimiento."""
    s = dict(_sub(_tel(telefono)))
    hoy = hoy or _hoy()
    try:
        vigente = bool(s["vencimiento"]) and hoy < date.fromisoformat(s["vencimiento"])
    except ValueError:
        vigente = False
    s["pro"] = s["plan"] in PLANES_PRO and vigente
    if s["plan"] in PLANES_PRO and not vigente:
        s["plan"] = "gratis"
    return s


def iniciar_prueba_pro(telefono: str) -> str | None:
    """Arranca la prueba de Pro si el número aún no tiene cuenta. Devuelve el vencimiento o None."""
    tel = _tel(telefono)
    with _lock:
        e = estado_plan(tel)
        if e["plan"] or e["inicio_prueba"]:
            return None
        hoy = _hoy()
        venc = (hoy + timedelta(days=PRO_PRUEBA_DIAS)).isoformat()
        _suscripciones[tel] = {"plan": "prueba_pro", "inicio_prueba": hoy.isoformat(), "vencimiento": venc}
    try:
        import venta_firestore as vf
        vf.registrar_prueba(tel, hoy.isoformat(), venc)
    except Exception as e:
        print(f"Error guardando prueba Pro en Firestore: {e}", flush=True)
    return venc


def _pasar_a_gratis(wa, tel: str) -> None:
    """Anota una vez en la hoja que el cliente pasó a Gratis (sus datos siguen en el Panel)."""
    with _lock:
        guardado = _suscripciones.get(tel) or {}
        if guardado.get("plan") not in PLANES_PRO:
            return
        venc = guardado.get("vencimiento", "")
        _suscripciones[tel] = {"plan": "gratis", "inicio_prueba": guardado.get("inicio_prueba", ""), "vencimiento": venc}
    try:
        import venta_firestore as vf
        vf.guardar_estado(tel, "gratis", guardado.get("inicio_prueba", ""), venc)
    except Exception as e:
        print(f"Error guardando paso a Gratis en Firestore: {e}", flush=True)
    registrar_fila(tel, f"{PANEL_NOMBRE} (pasa a Gratis)", 0, estado="paso-a-gratis", plan="gratis",
                   inicio_prueba=guardado.get("inicio_prueba", ""), vencimiento=venc)
    notificar(wa, f"⏸️ +{tel} pasó al plan Gratis (su {PRO_NOMBRE} terminó el {_fecha_txt(venc)}).")


def mensaje_ofrecer_pro() -> str:
    try:
        import mercado_pago as _mp
        con_tarjeta = _mp.mp_disponible()
    except Exception:
        con_tarjeta = False
    cierre = ("Te envío el QR de Plin/Yape y el link para pagar con tarjeta. 🙌"
              if con_tarjeta else "Te envío el QR para pagar. 🙌")
    return (f"El Asesor IA y las funciones avanzadas son parte del plan {PRO_NOMBRE} ⭐ Ahora estás en el plan Gratis "
            f"(hasta {PLAN_GRATIS_MOVIMIENTOS} movimientos al mes y {PLAN_GRATIS_FIADOS} fiados activos; tus datos siguen ahí). "
            f"Con {PRO_NOMBRE} tienes todo ilimitado, historial, reportes, exportación y Asesor IA por {texto_planes()}. "
            f"{cierre}")


def mensaje_ofrecer_panel() -> str:
    return (f"El Asesor IA es parte del plan {PRO_NOMBRE} 🤖 Pide tu {PANEL_NOMBRE} y empiezas con "
            f"{PRO_PRUEBA_DIAS} días de {PRO_NOMBRE} gratis. ¿Te lo envío?")


def aplicar_pago(tel: str, plan: str) -> str:
    """Extiende Pro. Devuelve el nuevo vencimiento (ISO)."""
    dias = PRO_DIAS_ANUAL if plan == "pro_anual" else PRO_DIAS_MENSUAL
    with _lock:
        e = estado_plan(tel)
        hoy = _hoy()
        base = date.fromisoformat(e["vencimiento"]) if e["pro"] else hoy
        venc = (base + timedelta(days=dias)).isoformat()
        _suscripciones[tel] = {"plan": plan, "inicio_prueba": e["inicio_prueba"], "vencimiento": venc}
    return venc


def activar_pro(telefono: str, plan: str) -> str:
    """Hook de almacenamiento: activa o extiende Pro (mismos días que el flujo QR).
    La capa Firestore se escribe en registrar_pago. Se puede sustituir esta función."""
    return aplicar_pago(_tel(telefono), plan)


def pago_id_ya_registrado(payment_id: str, metodo: str = "mercadopago") -> bool:
    """True si ese id de pago ya se usó para activar Pro."""
    pid = str(payment_id or "").strip()
    if not pid:
        return False
    clave = f"mp:{pid}" if metodo == "mercadopago" else pid
    return ya_registrado(clave)


def procesar_respuesta(wa, telefono: str, reply: str) -> None:
    """Aplica los marcadores de Gemini y el corte del Asesor IA, y envía la respuesta."""
    tel = _tel(telefono)
    flags = {m: (m in reply) for m in MARCADORES}
    texto = reply
    for m in MARCADORES:
        texto = texto.replace(m, "")
    texto = re.sub(r"\[\[ENVIAR_LINK_MP(?::[a-z_]+)?\]\]", "", texto)
    texto = texto.strip()

    e = estado_plan(tel)
    if e["plan"] == "gratis":
        _pasar_a_gratis(wa, tel)

    if flags[MARCADOR_ASESOR] and not e["pro"]:
        print(f"Corte Asesor IA tel={tel} plan={e['plan']} venc={e['vencimiento']}", flush=True)
        if e["plan"]:
            wa.send_text(telefono, mensaje_ofrecer_pro())
            enviar_instrucciones_pago(wa, telefono)
        else:
            wa.send_text(telefono, mensaje_ofrecer_panel())
        return

    if texto:
        wa.send_text(telefono, texto)
    if flags[MARCADOR_PANEL]:
        entregar_panel(wa, telefono)
    if flags[MARCADOR_PAGO] or flags.get(MARCADOR_LINK_MP):
        _enviar_pago_pro(wa, telefono, reply, solo_tarjeta=flags.get(MARCADOR_LINK_MP) and not flags[MARCADOR_PAGO])


# ---------------------------------------------------------------------------
# 4) Instrucciones de pago
# ---------------------------------------------------------------------------
def texto_instrucciones_pago() -> str:
    linea_num = f"\nNúmero: {PAGO_NUMERO}" if PAGO_NUMERO else ""
    return (
        f"Para pasar a {PRO_NOMBRE} (ilimitado + historial, reportes, exportación y Asesor IA):\n"
        f"1) Paga con Plin o Yape escaneando el QR (a nombre de {PAGO_TITULAR}).{linea_num}\n"
        f"   • Mensual: {_soles(PRO_PRECIO_MENSUAL)} ({PRO_DIAS_MENSUAL} días)\n"
        f"   • Anual: {_soles(PRO_PRECIO_ANUAL)} ({PRO_DIAS_ANUAL} días)\n"
        "2) Envíame aquí la captura del pago (que se vea monto, fecha, hora y destinatario).\n"
        f"Apenas lo valide, tu {PRO_NOMBRE} queda activo. 🙌"
    )


def enviar_instrucciones_pago(wa, to: str) -> None:
    """wa: objeto con send_text(to, body, preview_url) y send_image(to, link, media_id, caption)."""
    if PAGO_QR_URL or PAGO_QR_MEDIA_ID:
        wa.send_image(to, link=PAGO_QR_URL or None, media_id=PAGO_QR_MEDIA_ID or None,
                      caption=f"QR Plin/Yape – {PAGO_TITULAR}")
    wa.send_text(to, texto_instrucciones_pago())


def _enviar_pago_pro(wa, telefono: str, reply: str = "", solo_tarjeta: bool = False) -> None:
    """Ofrece QR (flujo actual) y, si hay token, el link de Mercado Pago.
    Si el cliente pidió solo tarjeta y no hay token, se cae al QR en silencio."""
    plan = None
    try:
        import mercado_pago as _mp
        plan = _mp.plan_desde_texto(reply)
        con_mp = _mp.mp_disponible()
    except Exception:
        _mp = None
        con_mp = False
    if solo_tarjeta and con_mp:
        if not _mp.enviar_links_mp(wa, telefono, plan=plan):
            enviar_instrucciones_pago(wa, telefono)
        return
    enviar_instrucciones_pago(wa, telefono)
    if con_mp:
        _mp.enviar_links_mp(wa, telefono, plan=plan)

# ---------------------------------------------------------------------------
# 5) Validación del comprobante con Gemini Vision
# ---------------------------------------------------------------------------
_PROMPT_VISION = """
Analiza la imagen. Puede ser una captura de un pago móvil peruano (Plin, Yape, Interbank, BCP, BBVA, etc.) o cualquier otra cosa.
Devuelve SOLO un JSON con estas claves:
{
 "es_comprobante": true/false,           // ¿es una constancia de pago/transferencia exitosa?
 "monto": número o null,                  // monto pagado tal como aparece, con decimales
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


def plan_por_monto(monto: float) -> str | None:
    if abs(monto - PRO_PRECIO_MENSUAL) < 0.01:
        return "pro_mensual"
    if abs(monto - PRO_PRECIO_ANUAL) < 0.01:
        return "pro_anual"
    return None


def verificar(datos: dict | None, ahora: datetime | None = None) -> tuple[bool, list[str], str | None]:
    """Reglas conservadoras. Devuelve (valido, motivos_de_rechazo, plan)."""
    if not datos:
        return False, ["no se pudo leer la imagen"], None
    ahora = ahora or _ahora()
    motivos = []
    plan = None
    if not datos.get("es_comprobante"):
        motivos.append("no parece un comprobante de pago")
    try:
        monto = float(str(datos.get("monto")).replace(",", "."))
        plan = plan_por_monto(monto)
        if not plan:
            motivos.append(f"monto {monto:.2f} no es {PRO_PRECIO_MENSUAL:.2f} (mensual) ni {PRO_PRECIO_ANUAL:.2f} (anual)")
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
    return (not motivos), motivos, (plan if not motivos else None)


# ---------------------------------------------------------------------------
# 6) Hoja de ingresos (Google Sheets con cuenta de servicio)
#    Columnas: A fecha registro | B producto | C monto | D método | E teléfono | F destinatario |
#              G n° operación | H fecha-hora comprobante | I clave | J estado |
#              K plan | L inicio prueba | M vencimiento
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


def _leer_filas() -> list[list[str]]:
    s = _sesion_sheets()
    if not s:
        return []
    try:
        r = s.get(_url_valores(), timeout=15)
        r.raise_for_status()
        return r.json().get("values", [])
    except Exception as e:
        print(f"Error leyendo hoja de ingresos: {e}", flush=True)
        return []


def ya_registrado(clave: str) -> bool:
    if clave in _entregados:
        return True
    try:
        import venta_firestore as vf
        if vf.pago_ya_registrado(clave):
            _entregados.add(clave)
            return True
    except Exception as e:
        print(f"Error buscando duplicado en Firestore: {e}", flush=True)
    for fila in _leer_filas():
        if clave in fila:
            _entregados.add(clave)
            return True
    return False


def registrar_pago(telefono: str, plan: str, monto: float, metodo: str = "",
                   payment_id: str = "", clave: str = "", datos: dict | None = None,
                   vencimiento: str = "", inicio_prueba: str = "",
                   receipt_ok: bool = True, wamid: str = "") -> dict:
    """Hook único de registro: Firestore (pagos + subscription) y hoja de respaldo.
    El QR y Mercado Pago pasan por aquí. Firestore puede ampliarse sin cambiar a los llamadores.
    Devuelve {ok, cuenta_app, fs_on, hoja, uid}."""
    tel = _tel(telefono)
    datos = dict(datos or {})
    if not clave:
        clave = f"mp:{payment_id}" if (metodo == "mercadopago" and payment_id) else (payment_id or "")
    if clave:
        with _lock:
            _entregados.add(clave)
    inicio = inicio_prueba or estado_plan(tel)["inicio_prueba"]
    fs = {"ok": False, "cuenta_app": False, "uid": ""}
    fs_on = False
    try:
        import venta_firestore as vf
        fs = vf.registrar_pago(
            tel, plan, monto, inicio, vencimiento,
            clave=clave, wamid=wamid, datos=datos, receipt_ok=receipt_ok,
            metodo=metodo, payment_id=str(payment_id or ""),
        )
        fs_on = vf.configurado()
    except Exception as e:
        print(f"Error guardando pago en Firestore: {e}", flush=True)
    nombre_plan = "anual" if plan == "pro_anual" else "mensual"
    hoja = registrar_fila(
        tel,
        f"{PANEL_NOMBRE} {PRO_NOMBRE} ({nombre_plan})",
        monto,
        estado="pagado",
        datos=datos,
        clave=clave,
        plan=plan,
        inicio_prueba=inicio,
        vencimiento=vencimiento,
        metodo=metodo,
    )
    return {
        "ok": bool(fs.get("ok") or hoja),
        "cuenta_app": bool(fs.get("cuenta_app")),
        "fs_on": fs_on,
        "hoja": bool(hoja),
        "uid": fs.get("uid") or "",
    }


def aviso_falta_cuenta_app(wa, telefono: str, res: dict) -> str:
    """Si el pago quedó en el bot pero no hay cuenta de la app, pide el correo. Texto extra para Rolly."""
    if res.get("fs_on") and res.get("ok") and not res.get("cuenta_app"):
        wa.send_text(
            telefono,
            "Tu pago quedó registrado. Para activar el Pro en ARO Finance Pro, respóndeme el correo "
            "con el que te registraste en https://aro-finance-pro.web.app 🙌",
        )
        return " (OJO: no hay cuenta de la app vinculada a este WhatsApp; pide el correo de registro)"
    if not res.get("fs_on"):
        return " (OJO: Firestore no configurado; el Pro en la app se activa cuando esté la clave)"
    return ""


def registrar_fila(telefono: str, producto: str, monto: float, estado: str, datos: dict | None = None,
                   clave: str = "", plan: str = "", inicio_prueba: str = "", vencimiento: str = "",
                   metodo: str = "") -> bool:
    s = _sesion_sheets()
    if not s:
        print(f"Hoja de ingresos no configurada; fila no registrada ({estado}). Estado solo en memoria.", flush=True)
        return False
    datos = datos or {}
    fila = [
        _ahora().strftime("%Y-%m-%d %H:%M:%S"),
        producto,
        f"{monto:.2f}",
        metodo or (PAGO_METODO if monto else "sin cobro"),
        _tel(telefono),
        datos.get("destinatario") or "",
        datos.get("numero_operacion") or "",
        f"{datos.get('fecha') or ''} {datos.get('hora') or ''}".strip(),
        clave,
        estado,
        plan,
        inicio_prueba,
        vencimiento,
    ]
    try:
        r = s.post(_url_valores(":append"), params={"valueInputOption": "USER_ENTERED"},
                   json={"values": [fila]}, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"Error registrando fila: {e}", flush=True)
        return False


# ---------------------------------------------------------------------------
# Orquestación cuando llega una imagen
# ---------------------------------------------------------------------------
def procesar_imagen(wa, gemini_client, modelos: list[str], telefono: str, media_id: str) -> bool:
    """Devuelve True si la imagen se trató como comprobante (válido o no)."""
    tel = _tel(telefono)
    try:
        imagen, mime = wa.download_media(media_id)
    except Exception as e:
        print(f"Error descargando media: {e}", flush=True)
        return False
    datos = analizar_comprobante(gemini_client, modelos, imagen, mime)
    if not datos or not datos.get("es_comprobante"):
        if datos is None:
            wa.send_text(telefono, "Recibí tu imagen, pero no pude revisarla ahora. Un asesor la revisa y te escribe pronto. 🙏")
            notificar(wa, f"⚠️ No se pudo analizar una imagen de +{tel}. Revisar a mano.")
            return True
        return False  # no es un comprobante: que siga el flujo normal

    h = hashlib.sha256(imagen).hexdigest()[:16]
    op = re.sub(r"\W", "", str(datos.get("numero_operacion") or ""))
    clave = f"op:{op}" if op else f"img:{h}"

    with _lock:
        duplicado = ya_registrado(clave) or ya_registrado(f"img:{h}")
        valido, motivos, plan = (False, [], None)
        if not duplicado:
            valido, motivos, plan = verificar(datos)
            if valido:
                _entregados.update({clave, f"img:{h}"})
    if duplicado:
        wa.send_text(telefono, "Este pago ya fue registrado antes. Si tu plan Pro no se activó, un asesor te ayuda en breve. 🙌")
        notificar(wa, f"ℹ️ +{tel} reenvió un comprobante ya registrado ({clave}).")
        return True

    if not valido:
        print(f"Pago rechazado tel={tel} motivos={motivos}", flush=True)
        wa.send_text(telefono, "Gracias por tu captura. No pude validar el pago automáticamente; "
                               "un asesor lo revisa y te escribe en breve. 🙏")
        notificar(wa, f"❌ Pago NO validado de +{tel}: " + "; ".join(motivos))
        return True

    monto = PRO_PRECIO_ANUAL if plan == "pro_anual" else PRO_PRECIO_MENSUAL
    inicio = estado_plan(tel)["inicio_prueba"]
    venc = activar_pro(tel, plan)
    nombre_plan = "anual" if plan == "pro_anual" else "mensual"
    wa.send_text(telefono, f"¡Pago validado! ✅ Tu plan {PRO_NOMBRE} {nombre_plan} está activo hasta el "
                           f"{_fecha_txt(venc)}: movimientos y fiados ilimitados, historial, reportes, exportación "
                           f"y Asesor IA. ¡Gracias! 🙌")
    res = registrar_pago(tel, plan, monto, metodo=PAGO_METODO, clave=clave, datos=datos,
                         vencimiento=venc, inicio_prueba=inicio, receipt_ok=True)
    aviso_fs = aviso_falta_cuenta_app(wa, telefono, res)
    notificar(wa, f"✅ Pago {PRO_NOMBRE} {nombre_plan} {_soles(monto)} validado de +{tel}; vence {_fecha_txt(venc)}."
                  + ("" if res.get("hoja") else " (OJO: no se pudo registrar en la hoja; solo en memoria)")
                  + aviso_fs)
    return True


def notificar(wa, texto: str) -> None:
    if NOTIFICAR_A:
        try:
            wa.send_text(NOTIFICAR_A, texto)
        except Exception:
            traceback.print_exc()


def extraer_correo(texto: str) -> str:
    import venta_firestore as vf
    return vf.extraer_correo(texto)


def es_solo_correo(texto: str) -> bool:
    import venta_firestore as vf
    return vf.es_solo_correo(texto)


def procesar_posible_correo(wa, telefono: str, texto: str) -> bool:
    """Si el mensaje trae un correo, lo vincula a la cuenta de la app.
    Si el texto es solo el correo, responde y devuelve True (no pasar por Gemini)."""
    import venta_firestore as vf
    email = vf.extraer_correo(texto or "")
    if not email or not vf.configurado():
        return False
    res = vf.vincular_correo(telefono, email)
    solo = vf.es_solo_correo(texto or "")
    if not solo:
        return False
    tel = _tel(telefono)
    if res.get("cuenta_app") and res.get("plan") in PLANES_PRO:
        wa.send_text(telefono, f"Listo, vinculé tu correo {email} con este WhatsApp. "
                               f"Tu plan {PRO_NOMBRE} ya está activo en ARO Finance Pro"
                               + (f" hasta el {_fecha_txt(res.get('vencimiento') or '')}." if res.get("vencimiento") else ".")
                               + " Entra a https://aro-finance-pro.web.app y recarga la página. 🙌")
        notificar(wa, f"🔗 +{tel} vinculó {email} (uid={res.get('uid') or 'sí'}); plan {res.get('plan')}.")
    elif res.get("uid"):
        wa.send_text(telefono, f"Encontré tu cuenta de ARO Finance Pro con {email}. "
                               "Cuando se active un plan desde este WhatsApp, también se verá en la app.")
        notificar(wa, f"🔗 +{tel} vinculó {email} (cuenta encontrada, sin plan pendiente).")
    else:
        wa.send_text(telefono, f"Guardé tu correo {email}. No encontré aún una cuenta con ese correo en ARO Finance Pro. "
                               "Cuando te registres en https://aro-finance-pro.web.app, escríbeme de nuevo este correo "
                               "para activar tu plan en la app. 🙌")
        notificar(wa, f"🔗 +{tel} envió {email}, pero no hay cuenta en la app con ese correo.")
    # refrescar memoria por si se aplicó un plan
    with _lock:
        _suscripciones.pop(tel, None)
    return True
