"""Pagos con tarjeta vía Mercado Pago Checkout Pro.

El flujo de QR Plin/Yape no se toca. Si falta MP_ACCESS_TOKEN, este módulo
no ofrece links ni procesa cobros: el bot sigue solo con el QR.

Hooks de almacenamiento (venta_panel.activar_pro / registrar_pago): la capa
Firestore puede sustituirlos más adelante sin cambiar este archivo.
Aquí no hay claves: el Access Token y el secreto del webhook salen de env.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import traceback
from typing import Any
from urllib.parse import urlparse

import requests

import venta_panel as vp

MP_PREFERENCES_URL = "https://api.mercadopago.com/checkout/preferences"
MP_PAYMENT_URL = "https://api.mercadopago.com/v1/payments/{id}"
PUBLIC_BASE_URL_DEFAULT = "https://bot-ventas-cables.onrender.com"
PLANES = ("pro_mensual", "pro_anual")
MARCADOR_LINK_MP = "[[ENVIAR_LINK_MP]]"
_RE_MARCADOR_MP = re.compile(r"\[\[ENVIAR_LINK_MP(?::([a-z_]+))?\]\]")

# Lo asigna main.py al arrancar. En pruebas se mockea o se pasa `wa` explícito.
_wa = None


def configurar_wa(wa) -> None:
    """Registra el adaptador de WhatsApp (solo se usa en runtime real)."""
    global _wa
    _wa = wa


def access_token() -> str:
    return (os.environ.get("MP_ACCESS_TOKEN") or "").strip()


def webhook_secret() -> str:
    return (os.environ.get("MP_WEBHOOK_SECRET") or "").strip()


def public_base_url() -> str:
    raw = (os.environ.get("PUBLIC_BASE_URL") or PUBLIC_BASE_URL_DEFAULT).strip().rstrip("/")
    return raw or PUBLIC_BASE_URL_DEFAULT


def mp_disponible() -> bool:
    return bool(access_token())


def _precio(plan: str):
    x = vp.PRO_PRECIO_ANUAL if plan == "pro_anual" else vp.PRO_PRECIO_MENSUAL
    return int(x) if float(x).is_integer() else float(x)


def _titulo(plan: str) -> str:
    return "Panel Financiero Pro anual" if plan == "pro_anual" else "Panel Financiero Pro mensual"


def armar_referencia(telefono: str, plan: str, nonce: str | None = None) -> str:
    tel = vp._tel(telefono)
    if plan not in PLANES:
        raise ValueError(f"plan inválido: {plan}")
    return f"bvc|{tel}|{plan}|{nonce or secrets.token_hex(8)}"


def parsear_referencia(ref: str) -> dict | None:
    partes = [p.strip() for p in str(ref or "").split("|")]
    if len(partes) != 4 or partes[0] != "bvc":
        return None
    _, tel, plan, nonce = partes
    tel = vp._tel(tel)
    if not tel or plan not in PLANES:
        return None
    return {"telefono": tel, "plan": plan, "nonce": nonce}


def plan_desde_texto(texto: str) -> str | None:
    """Plan pedido en el texto o en el marcador. None = ofrecer ambos."""
    m = _RE_MARCADOR_MP.search(texto or "")
    if m and m.group(1):
        tag = m.group(1).strip()
        if tag in PLANES:
            return tag
        if tag == "anual":
            return "pro_anual"
        if tag == "mensual":
            return "pro_mensual"
    t = vp._norm(texto)
    anual = "anual" in t or "149" in t
    mensual = "mensual" in t or "al mes" in t or bool(re.search(r"\b19\b", t))
    if anual and not mensual:
        return "pro_anual"
    if mensual and not anual:
        return "pro_mensual"
    return None


def _headers() -> dict:
    return {"Authorization": f"Bearer {access_token()}", "Content-Type": "application/json"}


def payload_preferencia(telefono: str, plan: str, nonce: str | None = None) -> dict:
    """Cuerpo de POST /checkout/preferences (útil para pruebas)."""
    if plan not in PLANES:
        raise ValueError(f"plan inválido: {plan}")
    base = public_base_url()
    return {
        "items": [
            {
                "title": _titulo(plan),
                "quantity": 1,
                "unit_price": _precio(plan),
                "currency_id": "PEN",
            }
        ],
        "external_reference": armar_referencia(telefono, plan, nonce),
        "notification_url": f"{base}/mp/webhook",
        "back_urls": {
            "success": base,
            "failure": base,
            "pending": base,
        },
        "auto_return": "approved",
        "statement_descriptor": "ARO CONTROL",
    }


def url_pago(pref: dict) -> str:
    if not pref:
        return ""
    if access_token().startswith("TEST-"):
        return (pref.get("sandbox_init_point") or pref.get("init_point") or "").strip()
    return (pref.get("init_point") or pref.get("sandbox_init_point") or "").strip()


def _post_preferencia(payload: dict) -> dict:
    r = requests.post(MP_PREFERENCES_URL, json=payload, headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json() or {}


def _get_pago(payment_id: str) -> dict:
    url = MP_PAYMENT_URL.format(id=payment_id)
    r = requests.get(url, headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json() or {}


def crear_preferencia(telefono: str, plan: str, nonce: str | None = None) -> dict | None:
    """Crea una preferencia Checkout Pro. None si no hay token o si falla la API."""
    if not mp_disponible():
        return None
    payload = payload_preferencia(telefono, plan, nonce)
    try:
        pref = _post_preferencia(payload)
    except Exception as e:
        print(f"MP error creando preferencia plan={plan}: {e}", flush=True)
        return None
    link = url_pago(pref)
    if not link:
        print(f"MP preferencia sin init_point plan={plan}", flush=True)
        return None
    return {
        "id": pref.get("id"),
        "init_point": pref.get("init_point") or "",
        "sandbox_init_point": pref.get("sandbox_init_point") or "",
        "external_reference": payload["external_reference"],
        "link": link,
        "plan": plan,
    }


def crear_links(telefono: str, plan: str | None = None) -> list[dict]:
    """Una preferencia por plan (el elegido, o mensual y anual). Vacío sin token."""
    if not mp_disponible():
        return []
    planes = (plan,) if plan in PLANES else PLANES
    links = []
    for p in planes:
        pref = crear_preferencia(telefono, p)
        if pref:
            links.append(pref)
    return links


def texto_links(links: list[dict]) -> str:
    if not links:
        return ""
    lineas = []
    if len(links) == 1:
        p = links[0]
        nombre = "anual" if p["plan"] == "pro_anual" else "mensual"
        dias = vp.PRO_DIAS_ANUAL if p["plan"] == "pro_anual" else vp.PRO_DIAS_MENSUAL
        lineas.append(
            f"Paga el plan {vp.PRO_NOMBRE} {nombre} ({vp._soles(_precio(p['plan']))}, {dias} días) "
            f"con tarjeta por Mercado Pago:\n{p['link']}"
        )
    else:
        lineas.append("También puedes pagar con tarjeta (débito o crédito) por Mercado Pago:")
        for p in links:
            nombre = "anual" if p["plan"] == "pro_anual" else "mensual"
            dias = vp.PRO_DIAS_ANUAL if p["plan"] == "pro_anual" else vp.PRO_DIAS_MENSUAL
            lineas.append(f"• {nombre.capitalize()} ({vp._soles(_precio(p['plan']))}, {dias} días):\n{p['link']}")
    lineas.append(f"Al confirmarse el pago, tu {vp.PRO_NOMBRE} se activa solo. No hace falta captura. 🙌")
    return "\n".join(lineas)


def enviar_links_mp(wa, telefono: str, plan: str | None = None) -> bool:
    """Envía el o los links de Checkout Pro. False si no hay token o falló la API (silencioso)."""
    if not mp_disponible():
        return False
    links = crear_links(telefono, plan)
    if not links:
        return False
    wa.send_text(telefono, texto_links(links), preview_url=True)
    return True


def extraer_payment_id(body: dict | None, query: dict | None = None) -> str | None:
    """Id del pago (data.id o IPN topic=payment&id=). No usa el id de la notificación."""
    body = body or {}
    query = {str(k): v for k, v in (query or {}).items()}
    typ = str(
        body.get("type") or body.get("topic") or query.get("type") or query.get("topic") or ""
    ).lower()
    if typ and typ not in ("payment",) and not typ.startswith("payment"):
        return None

    pid = None
    data = body.get("data")
    if isinstance(data, dict):
        pid = data.get("id")
    if not pid:
        resource = str(body.get("resource") or query.get("resource") or "")
        if resource:
            if resource.isdigit():
                pid = resource
            else:
                path = urlparse(resource).path.rstrip("/").split("/")
                if path:
                    pid = path[-1]
    if not pid:
        pid = query.get("data.id") or query.get("data_id")
    if not pid and typ.startswith("payment"):
        # IPN clásico: ?topic=payment&id=123. No usar body["id"] (es la notificación).
        pid = query.get("id")
    pid = str(pid or "").strip()
    return pid or None


def manifest_firma(payment_id: str, request_id: str, ts: str) -> str:
    partes = []
    if payment_id:
        partes.append(f"id:{payment_id}")
    if request_id:
        partes.append(f"request-id:{request_id}")
    partes.append(f"ts:{ts}")
    return ";".join(partes) + ";"


def firma_valida(body: dict | None, headers: dict | None, query: dict | None = None,
                 secret: str | None = None) -> bool:
    """HMAC-SHA256 de 'id:{data.id};request-id:{x-request-id};ts:{ts};'."""
    secret = webhook_secret() if secret is None else secret
    if not secret:
        return True
    headers = {str(k).lower(): v for k, v in (headers or {}).items()}
    sig = headers.get("x-signature") or ""
    request_id = headers.get("x-request-id") or ""
    ts = v1 = ""
    for part in sig.split(","):
        part = part.strip()
        if part.startswith("ts="):
            ts = part[3:]
        elif part.startswith("v1="):
            v1 = part[3:]
    if not ts or not v1:
        return False
    payment_id = extraer_payment_id(body, query) or ""
    esperado = hmac.new(
        secret.encode("utf-8"),
        manifest_firma(payment_id, request_id, ts).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(esperado, v1)


def obtener_pago(payment_id: str) -> dict | None:
    if not mp_disponible() or not payment_id:
        return None
    try:
        return _get_pago(str(payment_id))
    except Exception as e:
        print(f"MP error leyendo pago id={payment_id}: {e}", flush=True)
        return None


def validar_pago(pago: dict | None) -> tuple[bool, str, dict]:
    """Exige status approved, moneda PEN y monto exacto coincidente con el plan de la referencia."""
    if not pago:
        return False, "sin_pago", {}
    if str(pago.get("status") or "").lower() != "approved":
        return False, f"estado_{pago.get('status')}", {}
    if str(pago.get("currency_id") or "").upper() != "PEN":
        return False, "moneda", {}
    try:
        monto = float(pago.get("transaction_amount"))
    except (TypeError, ValueError):
        return False, "monto", {}
    plan_monto = vp.plan_por_monto(monto)
    if not plan_monto:
        return False, "monto", {}
    ref = parsear_referencia(pago.get("external_reference") or "")
    if not ref:
        return False, "referencia", {}
    if ref["plan"] != plan_monto:
        return False, "monto_plan", {}
    if not ref["telefono"]:
        return False, "telefono", {}
    return True, "ok", {
        "telefono": ref["telefono"],
        "plan": ref["plan"],
        "monto": monto,
        "nonce": ref["nonce"],
    }


def _avisar_cliente(telefono: str, plan: str, venc: str, wa=None) -> None:
    sender = wa if wa is not None else _wa
    if sender is None:
        return
    nombre = "anual" if plan == "pro_anual" else "mensual"
    try:
        sender.send_text(
            telefono,
            f"¡Pago confirmado! ✅ Tu plan {vp.PRO_NOMBRE} {nombre} está activo hasta el "
            f"{vp._fecha_txt(venc)}: movimientos y fiados ilimitados, historial, reportes, exportación "
            f"y Asesor IA. ¡Gracias! 🙌",
        )
    except Exception:
        traceback.print_exc()


def aplicar_pago_mp(pago: dict, wa=None) -> dict:
    """Activa Pro si el pago es válido. Idempotente por id de pago."""
    ok, motivo, info = validar_pago(pago)
    pid = str((pago or {}).get("id") or "")
    if not ok:
        print(f"MP pago no aplicado id={pid} motivo={motivo}", flush=True)
        return {"ok": False, "motivo": motivo, "payment_id": pid}
    tel = info["telefono"]
    plan = info["plan"]
    monto = info["monto"]
    with vp._lock:
        if vp.pago_id_ya_registrado(pid):
            return {"ok": True, "motivo": "duplicado", "payment_id": pid, "telefono": tel, "plan": plan}
        if pid:
            vp._entregados.add(f"mp:{pid}")
    inicio = vp.estado_plan(tel)["inicio_prueba"]
    venc = vp.activar_pro(tel, plan)
    res = vp.registrar_pago(
        tel,
        plan,
        monto,
        metodo="mercadopago",
        payment_id=pid,
        vencimiento=venc,
        inicio_prueba=inicio,
        datos={"numero_operacion": pid, "app": "mercadopago", "metodo": "mercadopago", "payment_id": pid},
        receipt_ok=True,
    )
    sender = wa if wa is not None else _wa
    _avisar_cliente(tel, plan, venc, wa=sender)
    aviso_fs = vp.aviso_falta_cuenta_app(sender, tel, res) if sender is not None else (
        "" if res.get("fs_on") else " (OJO: Firestore no configurado; el Pro en la app se activa cuando esté la clave)"
    )
    if sender is not None:
        vp.notificar(
            sender,
            f"✅ Pago Mercado Pago {vp.PRO_NOMBRE} "
            f"{'anual' if plan == 'pro_anual' else 'mensual'} {vp._soles(monto)} de +{tel}; "
            f"vence {vp._fecha_txt(venc)}. id={pid}"
            + ("" if res.get("hoja") else " (OJO: no se pudo registrar en la hoja; solo en memoria)")
            + aviso_fs,
        )
    return {
        "ok": True,
        "motivo": "activado",
        "payment_id": pid,
        "telefono": tel,
        "plan": plan,
        "vencimiento": venc,
        "cuenta_app": bool(res.get("cuenta_app")),
    }


def procesar_notificacion(body: dict | None, query: dict | None = None, wa=None) -> dict:
    """Relee el pago en la API y, si está aprobado y es válido, activa Pro."""
    if not mp_disponible():
        return {"ok": False, "motivo": "sin_token"}
    pid = extraer_payment_id(body, query)
    if not pid:
        return {"ok": False, "motivo": "sin_id"}
    pago = obtener_pago(pid)
    if not pago:
        return {"ok": False, "motivo": "no_se_pudo_leer", "payment_id": pid}
    return aplicar_pago_mp(pago, wa=wa)
