"""Reglas de conversación del bot de ventas (español peruano).

1) Método de venta: saluda con una pregunta, primero ofrece el Panel gratis
   (con 15 días de Pro), al día siguiente hace seguimiento, y antes de que
   termine la prueba da el precio y cierra con «¿mensual o anual?».
   El seguimiento sale SOLO si SEGUIMIENTO_ACTIVO=1 (por defecto está apagado).
2) Seguir el hilo: se le pasan a Gemini los últimos 10-20 mensajes; se responde
   primero lo último que dijo la persona. Un keyword de venta abre el chat pero
   no obliga a vender. Solo se ofrece el Panel si hablan de plata/gastos/etc.
   En temas de salud o problemas personales: solo empatía, sin venta.
   No se repite la oferta en 24 h.
3) Sonar humano: un solo saludo al empezar el día (Lima), no repetir «hola»
   ni frases hechas.
4) Mensajes cortos: 2-3 líneas, una sola pregunta. Se exige en el prompt y
   se recorta después.

El estado por chat (historial, last_offer_at, last_greeting_date, stage) vive
en memoria y se guarda en Firestore botEstados.convo cuando hay cliente.
"""

from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("ZONA_HORARIA", "America/Lima"))
MAX_HIST = 20
MAX_LINEAS = 3
MAX_CHARS = 300
VENTANA_OFERTA_HORAS = 24
VENTANA_WA_HORAS = 24

PANEL_APP_DEFAULT = "https://aro-finance-pro.web.app"

_lock = threading.RLock()
_convos: dict[str, dict] = {}
_enviar_texto: Callable | None = None
_enviar_plantilla: Callable | None = None
_ahora_fn = None  # tests pueden inyectar

# Temas con los que SÍ se puede ofrecer el Panel.
_TEMAS_DINERO = (
    "plata", "dinero", "lucas", "soles", "gasto", "gastos", "ingreso", "ingresos",
    "fiado", "fiados", "deuda", "deudas", "negocio", "precio", "precios",
    "cobrar", "cobro", "cobranza", "venta", "ventas", "ganar", "ganancia",
    "ahorro", "ahorros", "presupuesto", "caja", "contabilidad", "utilidad",
    "panel", "aro control", "aro finance", "plan pro", "plan gratis",
    "yape", "plin", "pagar", "pago", "mensual", "anual", "mercadopago",
    "tarjeta",
)

# Problemas de salud o personales: solo empatía, sin venta.
_TEMAS_SENSIBLE = (
    "cita", "gastro", "medicina", "medicinas", "medicamento", "medicamentos",
    "pastilla", "pastillas", "receta", "doctor", "doctora", "medico", "médico",
    "enfermo", "enferma", "enfermedad", "hospital", "clinica", "clínica",
    "farmacia", "muerte", "muerto", "muerta", "fallecio", "falleció",
    "fallecimiento", "triste", "tristeza", "depresion", "depresión",
    "ansiedad", "duelo", "cancer", "cáncer", "operacion", "operación",
    "dolor", "urgencia", "emergencia", "pediatra", "consulta medica",
    "consulta médica", "me duele", "estoy mal", "se murio", "se murió",
)

_FRASES_STOCK = (
    "te hablo ahorita en 2 minutos",
    "te hablo ahorita en dos minutos",
    "te hablo en 2 minutos",
    "te hablo en dos minutos",
    "te hablo al toque",
    "te hablo ahorita",
    "en un ratito te escribo",
    "en breve te escribo",
    "en breve te ayudo",
    "ya te escribo en un momento",
    "dame un momentito",
    "te respondo en un ratito",
)

_RE_SALUDO_INICIO = re.compile(
    r"^\s*(?:¡?\s*)(?:hola|buenos d[ií]as|buenas tardes|buenas noches|"
    r"buen d[ií]a|qu[eé] tal|hey|buenas)\s*[!,.¡¿]?\s*",
    re.I,
)
_RE_HOLA = re.compile(r"(?:^|[\s,])hola(?:\s|[!,.]|$)", re.I)
_RE_MARCADOR = re.compile(r"\[\[.*?\]\]")
_RE_VENTA = re.compile(
    r"panel financiero|aro-finance-pro|aro finance|"
    r"\b15 d[ií]as de pro\b|prueba de pro|prueba gratis|"
    r"s/\s*19|s/\s*149|plan pro\b|te lo env[ií][eo]|"
    r"quieres tu panel|quieres el panel|quieres que te lo|"
    r"te mando el (?:link|enlace|panel)|te lo mando",
    re.I,
)


def _ahora() -> datetime:
    if _ahora_fn:
        return _ahora_fn()
    return datetime.now(TZ)


def set_ahora(fn) -> None:
    global _ahora_fn
    _ahora_fn = fn


def _tel(t: str) -> str:
    return re.sub(r"\D", "", str(t or ""))


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def seguimiento_activo() -> bool:
    raw = (os.environ.get("SEGUIMIENTO_ACTIVO") or "0").strip().lower()
    return raw in ("1", "true", "si", "sí", "on", "yes")


def vacio() -> dict:
    return {
        "history": [],
        "last_offer_at": "",
        "last_greeting_date": "",
        "stage": "nuevo",
        "last_inbound_at": "",
        "last_outbound_at": "",
        "seguimiento_dia2_enviado": False,
        "fin_prueba_avisado": False,
    }


def configurar(enviar_texto=None, enviar_plantilla=None) -> None:
    global _enviar_texto, _enviar_plantilla
    if enviar_texto is not None:
        _enviar_texto = enviar_texto
    if enviar_plantilla is not None:
        _enviar_plantilla = enviar_plantilla


def reset() -> None:
    """Solo para pruebas: limpia la memoria de conversaciones."""
    with _lock:
        _convos.clear()


# ---------------------------------------------------------------------------
# Persistencia (memoria + botEstados.convo)
# ---------------------------------------------------------------------------
def _desde_fs(tel: str) -> dict | None:
    try:
        import venta_firestore as vf
        if not vf.configurado():
            return None
        doc = vf.get_client().estado_get(tel) or {}
        convo = doc.get("convo")
        return dict(convo) if isinstance(convo, dict) else None
    except Exception as e:
        print(f"Error leyendo convo de Firestore: {e}", flush=True)
        return None


def _a_fs(tel: str, estado: dict) -> None:
    try:
        import venta_firestore as vf
        if not vf.configurado():
            return
        vf.get_client().estado_set(tel, {
            "convo": dict(estado),
            "updatedAt": _ahora().isoformat(),
        })
    except Exception as e:
        print(f"Error guardando convo en Firestore: {e}", flush=True)


def cargar(telefono: str) -> dict:
    tel = _tel(telefono)
    with _lock:
        if tel not in _convos:
            leido = _desde_fs(tel) or {}
            base = vacio()
            base.update({k: leido[k] for k in base if k in leido})
            if isinstance(leido.get("history"), list):
                base["history"] = list(leido["history"])[-MAX_HIST:]
            _convos[tel] = base
        return _convos[tel]


def guardar(telefono: str) -> None:
    tel = _tel(telefono)
    with _lock:
        estado = _convos.get(tel)
        if estado is None:
            return
        _a_fs(tel, estado)


def estado(telefono: str) -> dict:
    return dict(cargar(telefono))


# ---------------------------------------------------------------------------
# Detección de temas
# ---------------------------------------------------------------------------
def es_tema_sensible(texto: str | None) -> bool:
    t = _norm(texto or "")
    if not t:
        return False
    return any(p in t for p in _TEMAS_SENSIBLE)


def es_tema_dinero(texto: str | None) -> bool:
    t = _norm(texto or "")
    if not t:
        return False
    return any(p in t for p in _TEMAS_DINERO)


def pide_panel(texto: str | None) -> bool:
    t = _norm(texto or "")
    if not t:
        return False
    claves = (
        "quiero el panel", "me das el panel", "mandame el panel",
        "mándame el panel", "pasame el panel", "pásame el panel",
        "el link del panel", "enlace del panel", "quiero la prueba",
        "enviame el panel", "envíame el panel", "pasame el link",
        "pásame el link",
    )
    if any(c in t for c in claves):
        return True
    # CTA corto de anuncio
    return t.strip() in ("panel", "panel financiero", "aro control")


def oferta_reciente(telefono: str, ahora: datetime | None = None) -> bool:
    e = cargar(telefono)
    raw = e.get("last_offer_at") or ""
    if not raw:
        return False
    ahora = ahora or _ahora()
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=TZ)
    except ValueError:
        return False
    return (ahora - ts) < timedelta(hours=VENTANA_OFERTA_HORAS)


def puede_ofrecer(telefono: str, texto: str | None, ahora: datetime | None = None) -> bool:
    """True solo si hablan de plata/panel, no es un tema sensible y no se ofreció en 24 h."""
    if es_tema_sensible(texto):
        return False
    if not (es_tema_dinero(texto) or pide_panel(texto)):
        return False
    if oferta_reciente(telefono, ahora=ahora) and not pide_panel(texto):
        # Pedido explícito del link sí se puede reenviar; el pitch automático no.
        return False
    if oferta_reciente(telefono, ahora=ahora) and pide_panel(texto):
        return True
    return True


def marcar_oferta(telefono: str, ahora: datetime | None = None, stage: str = "ofrecido") -> None:
    e = cargar(telefono)
    e["last_offer_at"] = (ahora or _ahora()).isoformat()
    if e.get("stage") in ("nuevo", "conversando", "ofrecido", ""):
        e["stage"] = stage
    guardar(telefono)


# ---------------------------------------------------------------------------
# Historial
# ---------------------------------------------------------------------------
def registrar_inbound(telefono: str, texto: str, ahora: datetime | None = None) -> None:
    e = cargar(telefono)
    now = ahora or _ahora()
    e["last_inbound_at"] = now.isoformat()
    e.setdefault("history", []).append({
        "role": "cliente", "text": (texto or "")[:2000], "ts": now.isoformat(),
    })
    e["history"] = e["history"][-MAX_HIST:]
    if e.get("stage") in ("nuevo", "", None):
        e["stage"] = "conversando"
    guardar(telefono)


def registrar_outbound(telefono: str, texto: str, ahora: datetime | None = None) -> None:
    e = cargar(telefono)
    now = ahora or _ahora()
    limpio = _RE_MARCADOR.sub("", texto or "").strip()
    e["last_outbound_at"] = now.isoformat()
    e.setdefault("history", []).append({
        "role": "bot", "text": limpio[:2000], "ts": now.isoformat(),
    })
    e["history"] = e["history"][-MAX_HIST:]
    guardar(telefono)


def historial_texto(telefono: str, n: int = MAX_HIST) -> str:
    hist = cargar(telefono).get("history") or []
    lineas = []
    for item in hist[-n:]:
        quien = "Cliente" if item.get("role") == "cliente" else "Asesor"
        lineas.append(f"{quien}: {item.get('text') or ''}")
    return "\n".join(lineas)


def mensajes_bot(telefono: str) -> list[str]:
    return [i.get("text") or "" for i in (cargar(telefono).get("history") or []) if i.get("role") == "bot"]


def dentro_ventana_wa(telefono: str, ahora: datetime | None = None) -> bool:
    raw = cargar(telefono).get("last_inbound_at") or ""
    if not raw:
        return False
    ahora = ahora or _ahora()
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=TZ)
    except ValueError:
        return False
    return (ahora - ts) <= timedelta(hours=VENTANA_WA_HORAS)


# ---------------------------------------------------------------------------
# Saludo y post-proceso
# ---------------------------------------------------------------------------
def saludo_del_dia(ahora: datetime | None = None) -> str:
    h = (ahora or _ahora()).hour
    if 5 <= h < 12:
        return "Buenos días"
    if 12 <= h < 19:
        return "Buenas tardes"
    return "Buenas noches"


def _dijo_hola(telefono: str) -> bool:
    return any(_RE_HOLA.search(t or "") for t in mensajes_bot(telefono))


def _ya_saludo_hoy(telefono: str, ahora: datetime | None = None) -> bool:
    hoy = (ahora or _ahora()).date().isoformat()
    return (cargar(telefono).get("last_greeting_date") or "") == hoy


def _quitar_saludo_inicial(texto: str) -> str:
    prev = None
    out = texto or ""
    while prev != out:
        prev = out
        out = _RE_SALUDO_INICIO.sub("", out, count=1)
    return out.lstrip(" ,.-¡!")


def _marcar_saludo(telefono: str, ahora: datetime | None = None) -> None:
    cargar(telefono)["last_greeting_date"] = (ahora or _ahora()).date().isoformat()
    guardar(telefono)


def aplicar_saludo(telefono: str, texto: str, ahora: datetime | None = None) -> str:
    ahora = ahora or _ahora()
    cuerpo = _quitar_saludo_inicial(texto or "").strip()
    if _dijo_hola(telefono):
        # Nunca repetir «hola»; si el modelo lo puso, se quita.
        texto_sin_hola = _RE_HOLA.sub(" ", texto or "")
        texto_sin_hola = re.sub(r"\s{2,}", " ", texto_sin_hola).strip(" ,")
        cuerpo = _quitar_saludo_inicial(texto_sin_hola).strip()
    if _ya_saludo_hoy(telefono, ahora):
        return cuerpo
    saludo = saludo_del_dia(ahora)
    if cuerpo:
        out = f"{saludo}. {cuerpo[0].upper() + cuerpo[1:] if len(cuerpo) > 1 else cuerpo}"
    else:
        out = f"{saludo}."
    # Si el texto original ya empezaba con el saludo correcto, no lo dupliques.
    # (cuerpo ya lo tiene recortado; out lo reponemos una vez)
    _marcar_saludo(telefono, ahora)
    return out


def _frases_ya_dichas(telefono: str) -> set[str]:
    dichas = set()
    for msg in mensajes_bot(telefono):
        n = _norm(msg)
        for f in _FRASES_STOCK:
            if f in n:
                dichas.add(f)
    return dichas


def quitar_frases_repetidas(telefono: str, texto: str) -> str:
    dichas = _frases_ya_dichas(telefono)
    out = texto or ""
    # También evita soltar una frase stock si ya se usó, y nunca las de «te hablo en 2 minutos».
    for f in _FRASES_STOCK:
        if f in dichas or True:
            # Siempre cortamos estas frases hechas: no suenan humanas si se repiten,
            # y Rolly pidió no usar «te hablo ahorita en 2 minutos».
            out = re.sub(re.escape(f), "", out, flags=re.I)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([.,!?])", r"\1", out)
    return out.strip(" ,;")


def _oraciones(texto: str) -> list[str]:
    partes = re.split(r"(?<=[.!?…])\s+", (texto or "").strip())
    return [p.strip() for p in partes if p.strip()]


def _ultima_pregunta(texto: str) -> str | None:
    """La pregunta corta del final (desde el último ¿ o desde un recorte cercano al ?)."""
    j = (texto or "").rfind("?")
    if j < 0:
        return None
    i = (texto or "").rfind("¿", 0, j + 1)
    if i >= 0:
        return texto[i:j + 1].strip()
    return texto[max(0, j - 60):j + 1].strip()


def una_pregunta(texto: str) -> str:
    """Si hay más de una pregunta, deja las frases enunciativas y solo la última pregunta."""
    orac = _oraciones(texto)
    if not orac:
        return (texto or "").strip()
    preguntas = [o for o in orac if "?" in o]
    if len(preguntas) <= 1:
        return (texto or "").strip()
    ultima = preguntas[-1]
    otras = [o for o in orac if "?" not in o]
    otras.append(ultima)
    return " ".join(otras).strip()


def acortar(texto: str, max_lineas: int = MAX_LINEAS, max_chars: int = MAX_CHARS) -> str:
    texto = una_pregunta((texto or "").strip())
    lineas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    if len(lineas) > max_lineas:
        # Conserva la última pregunta si quedó abajo.
        pregunta = next((ln for ln in reversed(lineas) if "?" in ln), None)
        head = lineas[:max_lineas]
        if pregunta and pregunta not in head:
            head = head[: max_lineas - 1] + [pregunta]
        lineas = head
        texto = "\n".join(lineas)
    if len(texto) > max_chars:
        # No cortar una URL a la mitad: si hay un http, se permite un poco más.
        if "http" in texto and len(texto) <= max_chars + 80:
            return texto
        recorte = texto[:max_chars].rsplit(" ", 1)[0].rstrip(",;:")
        if "?" in texto and "?" not in recorte:
            pregunta = _ultima_pregunta(texto)
            if pregunta and len(pregunta) <= max_chars:
                espacio = max_chars - len(pregunta) - 1
                pref = recorte[: max(0, espacio)].rsplit(" ", 1)[0] if espacio > 20 else ""
                recorte = (pref + " " + pregunta).strip() if pref else pregunta
        texto = recorte[:max_chars].strip()
    return texto.strip()


def _separar_marcadores(texto: str) -> tuple[str, str]:
    marks = _RE_MARCADOR.findall(texto or "")
    cuerpo = _RE_MARCADOR.sub("", texto or "").strip()
    return cuerpo, "\n".join(marks)


def _empathy_fallback() -> str:
    return "Cuídate mucho, ojalá se te pase pronto. Aquí estoy si me necesitas."


def quitar_pitch(texto: str) -> str:
    """Saca frases de venta de un texto (para temas sensibles o oferta repetida)."""
    queda = []
    for o in _oraciones(texto):
        if _RE_VENTA.search(o) or "[[" in o:
            continue
        if re.search(r"https?://", o) and "aro-finance" in o.lower():
            continue
        queda.append(o)
    return " ".join(queda).strip()


def filtrar_marcadores(texto: str, permitido_ofrecer: bool, sensible: bool) -> str:
    cuerpo, marks = _separar_marcadores(texto)
    if sensible or not permitido_ofrecer:
        # Quita marcadores de venta / pago / panel.
        marks = "\n".join(
            m for m in marks.splitlines()
            if "ENVIAR_PANEL" not in m and "ENVIAR_PAGO" not in m and "ENVIAR_LINK_MP" not in m
        )
        if sensible:
            cuerpo = quitar_pitch(cuerpo)
            if not cuerpo or len(cuerpo) < 20:
                cuerpo = _empathy_fallback()
        elif not permitido_ofrecer:
            recortado = quitar_pitch(cuerpo)
            cuerpo = recortado or cuerpo
    if marks:
        return f"{cuerpo}\n{marks}".strip()
    return cuerpo


def postprocesar(telefono: str, texto: str, texto_in: str = "", ahora: datetime | None = None) -> str:
    """Aplica saludo del día, recorte, una pregunta, dedupe y el filtro de oferta."""
    ahora = ahora or _ahora()
    sensible = es_tema_sensible(texto_in)
    permitido = (not sensible) and puede_ofrecer(telefono, texto_in, ahora=ahora)
    cuerpo, marks = _separar_marcadores(texto or "")
    cuerpo = quitar_frases_repetidas(telefono, cuerpo)
    cuerpo = aplicar_saludo(telefono, cuerpo, ahora=ahora)
    cuerpo = acortar(cuerpo)
    armado = f"{cuerpo}\n{marks}".strip() if marks else cuerpo
    armado = filtrar_marcadores(armado, permitido_ofrecer=permitido, sensible=sensible)
    return armado.strip()


# ---------------------------------------------------------------------------
# Bloque para el prompt de Gemini
# ---------------------------------------------------------------------------
def bloque_reglas(telefono: str = "", texto_in: str = "", ahora: datetime | None = None) -> str:
    ahora = ahora or _ahora()
    lineas = [
        "Cómo debes hablar:",
        "- Responde SIEMPRE primero lo último que la persona acaba de decir.",
        "- Español peruano, natural, como un humano por WhatsApp. 2 o 3 líneas, una sola pregunta al final.",
        "- No uses frases hechas tipo «te hablo ahorita en 2 minutos».",
        "- Un solo saludo al empezar el día (buenos días / buenas tardes / buenas noches, según la hora de Lima). No saludes otra vez ni repitas «hola».",
        "- Un keyword de venta (panel, precio, etc.) permite conversar, pero NO obliga a vender en cada mensaje.",
        "- Ofrece el Panel SOLO si hablan de plata, gastos, fiados, deudas, negocio o precio (o si piden el Panel).",
        "  La primera oferta es el Panel GRATIS (https://aro-finance-pro.web.app) con 15 días de Pro. Aún no hables de precio.",
        "- Si hablan de salud o un problema personal (cita, médico, medicinas, hospital, tristeza, etc.): solo empatía, CERO venta, CERO Panel, CERO precios.",
        "- No prometas que un asesor va a enviar el Panel, el video ni nada que no puedas cumplir tú.",
        "- No lo presentes como una hoja de cálculo. Es la app web ARO Finance Pro.",
        "- El video tutorial solo existe si el sistema lo envía; tú no lo inventes ni prometas.",
        "- Nunca inventes precios ni funciones. No digas que todo es gratis: hay plan Gratis con límites y plan Pro de pago.",
    ]
    if telefono:
        e = cargar(telefono)
        if _ya_saludo_hoy(telefono, ahora) or _dijo_hola(telefono):
            lineas.append("- Ya saludaste en este chat (o hoy). NO vuelvas a saludar ni digas hola.")
        else:
            lineas.append(f"- Si es el primer mensaje del día, empieza con «{saludo_del_dia(ahora)}» y una pregunta.")
        if es_tema_sensible(texto_in):
            lineas.append("- IMPORTANTE: este mensaje es de salud o un tema personal. Responde solo con empatía. NO ofrezcas el Panel ni pongas marcadores de venta.")
        elif not puede_ofrecer(telefono, texto_in, ahora=ahora):
            if oferta_reciente(telefono, ahora=ahora):
                lineas.append("- Ya ofreciste el Panel en las últimas 24 h. No lo vuelvas a ofrecer ni pongas [[ENVIAR_PANEL]]. Sigue el hilo de lo que preguntan.")
            elif texto_in and not es_tema_dinero(texto_in) and not pide_panel(texto_in):
                lineas.append("- Este mensaje no habla de plata ni del Panel. Responde lo que preguntan, sin ofrecer el Panel.")
        else:
            lineas.append("- Puedes ofrecer el Panel gratis (15 días de Pro) si encaja, con el marcador [[ENVIAR_PANEL]]. Todavía no des el precio, salvo que lo pidan o ya esté en prueba y vaya a vencer.")
        if e.get("stage") in ("prueba", "cierre"):
            lineas.append("- Si preguntan por el precio o se acaba la prueba, es S/ 19 al mes o S/ 149 al año, y cierra con «¿mensual o anual?».")
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Seguimientos (apagados por defecto)
# ---------------------------------------------------------------------------
def texto_seguimiento_dia2() -> str:
    return "¿Pudiste entrar al Panel? Si te trabas en algo, dime y te ayudo."


def texto_fin_prueba() -> str:
    return ("Tu prueba Pro está por terminar. Puedes seguir con Pro a S/ 19 al mes "
            "o S/ 149 al año. ¿Mensual o anual?")


def _parse_iso(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=TZ)
        return ts
    except ValueError:
        return None


def due_seguimiento_dia2(estado: dict, ahora: datetime | None = None) -> bool:
    if estado.get("seguimiento_dia2_enviado"):
        return False
    ts = _parse_iso(estado.get("last_offer_at") or "")
    if not ts:
        return False
    ahora = ahora or _ahora()
    return ahora.astimezone(TZ).date() > ts.astimezone(TZ).date()


def due_fin_prueba(telefono: str, estado: dict, ahora: datetime | None = None) -> bool:
    if estado.get("fin_prueba_avisado"):
        return False
    try:
        import venta_panel as vp
        e = vp.estado_plan(telefono, hoy=(ahora or _ahora()).date())
    except Exception:
        return False
    if e.get("plan") != "prueba_pro" or not e.get("pro"):
        return False
    try:
        venc = date.fromisoformat(e["vencimiento"])
    except Exception:
        return False
    hoy = (ahora or _ahora()).date()
    return hoy <= venc and (venc - hoy).days <= 1


def _enviar(telefono: str, texto: str = "", plantilla: str = "", lang: str = "es",
            ahora: datetime | None = None) -> str:
    """Envía texto libre si hay ventana de 24 h; si no, la plantilla de Meta. Devuelve el modo usado."""
    if dentro_ventana_wa(telefono, ahora=ahora):
        if _enviar_texto:
            _enviar_texto(telefono, texto)
        return "texto"
    nombre = (plantilla or "").strip()
    if not nombre:
        print(f"Seguimiento omitido tel={_tel(telefono)}: fuera de 24 h y no hay plantilla de Meta.", flush=True)
        return "omitido_sin_plantilla"
    if _enviar_plantilla:
        _enviar_plantilla(telefono, nombre, lang)
        return "plantilla"
    print(f"Seguimiento omitido tel={_tel(telefono)}: no hay envío de plantilla configurado.", flush=True)
    return "omitido_sin_envio"


def _cargar_todos() -> list[tuple[str, dict]]:
    conocidos = {}
    with _lock:
        for tel, e in _convos.items():
            conocidos[tel] = e
    try:
        import venta_firestore as vf
        c = vf.get_client() if vf.configurado() else None
        if c and hasattr(c, "estados_todos"):
            for tel, doc in c.estados_todos():
                tel = _tel(tel)
                if tel and tel not in conocidos:
                    convo = doc.get("convo") if isinstance(doc.get("convo"), dict) else {}
                    if convo:
                        base = vacio()
                        base.update({k: convo[k] for k in base if k in convo})
                        conocidos[tel] = base
                        with _lock:
                            _convos.setdefault(tel, base)
    except Exception as e:
        print(f"Error listando convos: {e}", flush=True)
    return list(conocidos.items())


def procesar_seguimientos(ahora: datetime | None = None, en_modo_prueba=None, chat_en_silencio=None) -> list[dict]:
    """Recorre los chats y envía seguimientos pendientes. No hace nada si SEGUIMIENTO_ACTIVO=0."""
    if not seguimiento_activo():
        return []
    ahora = ahora or _ahora()
    lang = (os.environ.get("WA_TEMPLATE_LANG") or "es").strip() or "es"
    tpl_seg = (os.environ.get("WA_TEMPLATE_SEGUIMIENTO") or "").strip()
    tpl_fin = (os.environ.get("WA_TEMPLATE_FIN_PRUEBA") or "").strip()
    hechos = []
    for tel, e in _cargar_todos():
        if en_modo_prueba and not en_modo_prueba(tel):
            continue
        if chat_en_silencio and chat_en_silencio(tel):
            continue
        if due_seguimiento_dia2(e, ahora=ahora):
            modo = _enviar(tel, texto_seguimiento_dia2(), plantilla=tpl_seg, lang=lang, ahora=ahora)
            if modo != "omitido_sin_plantilla" and modo != "omitido_sin_envio":
                e["seguimiento_dia2_enviado"] = True
                e["stage"] = e.get("stage") or "seguimiento"
                with _lock:
                    _convos[tel] = e
                guardar(tel)
            hechos.append({"telefono": tel, "tipo": "dia2", "modo": modo})
        if due_fin_prueba(tel, e, ahora=ahora):
            modo = _enviar(tel, texto_fin_prueba(), plantilla=tpl_fin, lang=lang, ahora=ahora)
            if modo != "omitido_sin_plantilla" and modo != "omitido_sin_envio":
                e["fin_prueba_avisado"] = True
                e["stage"] = "cierre"
                with _lock:
                    _convos[tel] = e
                guardar(tel)
            hechos.append({"telefono": tel, "tipo": "fin_prueba", "modo": modo})
    return hechos
