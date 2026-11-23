"""Capa Firestore de ARO Finance Pro para el bot de ventas (copia de trabajo).

La app (https://aro-finance-pro.web.app, projectId aro-finance-pro) guarda cada
cuenta en users/{uid}. El plan que ve el panel sale de profile.subscription:

    {
      "plan": "trial" | "monthly" | "annual",
      "status": "trial" | "active" | "expired" | "blocked",
      "startedAt": "<ISO>",
      "expiresAt": "<ISO>",
      "expiresAtMs": <int>,
      "blocked": false
    }

Un plan Pro pagado es plan=monthly|annual y status=active, con vencimiento
futuro. Si ya venció o status=expired, la app lo trata como Gratis. La prueba
de 15 días es plan=trial, status=trial. Esta forma es la que usa el propio
panel al activar Pro (función de administración) y al crear la cuenta.

Colecciones que usa este módulo:
- users/{uid}: se actualiza SOLO subscription (y updatedAt); no se pisan
  movimientos, fiados ni el resto del perfil.
- pagos: registro del bot (teléfono, monto, plan, fecha, resultado del
  comprobante, wamid, uid si se pudo vincular, metodo, payment_id).
- botEstados/{telefono}: estado del bot por número de WhatsApp, para
  reconstruir memoria tras un reinicio aunque aún no haya cuenta en la app.

Si faltan FIREBASE_SERVICE_ACCOUNT_JSON / FIREBASE_PROJECT_ID (o no hay
cliente inyectado), todas las funciones son no-op y devuelven vacío: el bot
sigue en memoria, con la hoja de ingresos como respaldo opcional.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("ZONA_HORARIA", "America/Lima"))

COL_USERS = "users"
COL_PAGOS = "pagos"
COL_BOT_ESTADOS = "botEstados"
AI_USAGE = "aiUsage"
AI_LIMIT_PRO = 30

# Plan del bot → (plan, status) que espera la app en users/{uid}.subscription
BOT_A_APP = {
    "prueba_pro": ("trial", "trial"),
    "pro_mensual": ("monthly", "active"),
    "pro_anual": ("annual", "active"),
}

_lock = threading.RLock()
_client = None
_client_ready = False


def _tel(t: str) -> str:
    return re.sub(r"\D", "", t or "")


def variantes_telefono(telefono: str) -> list[str]:
    """Formatos con los que la app puede haber guardado users/{uid}.phone."""
    d = _tel(telefono)
    if not d:
        return []
    out = [d, f"+{d}"]
    if d.startswith("51") and len(d) >= 11:
        local = d[2:]
        out.extend([
            local,
            f"+51{local}",
            f"+51 {local}",
            f"+51 {local[:3]} {local[3:6]} {local[6:]}".strip(),
            f"51 {local}",
        ])
    elif len(d) == 9:
        out.extend([
            f"51{d}",
            f"+51{d}",
            f"+51 {d}",
            f"+51 {d[:3]} {d[3:6]} {d[6:]}",
        ])
    # únicos, orden estable, sin vacíos
    vistos, uniq = set(), []
    for x in out:
        if x and x not in vistos:
            vistos.add(x)
            uniq.append(x)
    return uniq


def _iso_ms(dia: date | str, hora: int = 0, minuto: int = 0) -> tuple[str, int]:
    if isinstance(dia, str):
        dia = date.fromisoformat(dia[:10])
    dt = datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=TZ)
    return dt.isoformat(), int(dt.timestamp() * 1000)


def subscription_app(plan_bot: str, inicio: str, vencimiento: str, prev: dict | None = None) -> dict:
    """Arma el objeto subscription que el panel sabe leer, sin campos de más."""
    prev = prev or {}
    if plan_bot == "gratis":
        plan_app = prev.get("plan") if prev.get("plan") in ("trial", "monthly", "annual") else "trial"
        started = prev.get("startedAt") or (_iso_ms(inicio)[0] if inicio else datetime.now(TZ).isoformat())
        exp_iso, exp_ms = _iso_ms(vencimiento) if vencimiento else (started, 0)
        return {
            "plan": plan_app,
            "status": "expired",
            "startedAt": started,
            "expiresAt": exp_iso,
            "expiresAtMs": exp_ms,
            "blocked": False,
        }
    par = BOT_A_APP.get(plan_bot)
    if not par:
        raise ValueError(f"plan de bot desconocido: {plan_bot}")
    plan_app, status = par
    started = prev.get("startedAt") or (_iso_ms(inicio)[0] if inicio else datetime.now(TZ).isoformat())
    exp_iso, exp_ms = _iso_ms(vencimiento)
    return {
        "plan": plan_app,
        "status": status,
        "startedAt": started,
        "expiresAt": exp_iso,
        "expiresAtMs": exp_ms,
        "blocked": False,
    }


def estado_desde_subscription(sub: dict | None, hoy: date | None = None) -> dict:
    """Traduce users/{uid}.subscription al estado que usa el bot."""
    vacio = {"plan": None, "inicio_prueba": "", "vencimiento": "", "uid": ""}
    if not sub:
        return vacio
    hoy = hoy or datetime.now(TZ).date()
    try:
        if sub.get("expiresAtMs"):
            venc_d = datetime.fromtimestamp(int(sub["expiresAtMs"]) / 1000, TZ).date()
        else:
            venc_d = date.fromisoformat(str(sub.get("expiresAt") or "")[:10])
        venc = venc_d.isoformat()
        vigente = hoy < venc_d
    except (ValueError, TypeError, OSError):
        venc, vigente = str(sub.get("expiresAt") or "")[:10], False
    inicio = ""
    try:
        if sub.get("startedAt"):
            inicio = str(sub["startedAt"])[:10]
    except Exception:
        inicio = ""
    if sub.get("blocked") or sub.get("status") == "blocked":
        plan = "gratis"
    elif not vigente or sub.get("status") == "expired":
        plan = "gratis" if (sub.get("plan") or inicio) else None
    elif sub.get("plan") == "monthly":
        plan = "pro_mensual"
    elif sub.get("plan") == "annual":
        plan = "pro_anual"
    elif sub.get("plan") == "trial" or vigente:
        plan = "prueba_pro"
    else:
        plan = None
    if not plan and not inicio:
        return vacio
    return {"plan": plan, "inicio_prueba": inicio, "vencimiento": venc, "uid": ""}


def mes_clave(hoy: date | None = None) -> str:
    """En la app, el id de aiUsage es Cn() = YYYY-MM-DD (cuota diaria)."""
    return (hoy or datetime.now(TZ).date()).isoformat()


# ---------------------------------------------------------------------------
# Cliente (real o inyectado). Nunca imprime el JSON de la cuenta de servicio.
# ---------------------------------------------------------------------------
class FakeFirestore:
    """Almacén en memoria con la misma interfaz que usa este módulo (para tests)."""

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.pagos: list[dict] = []
        self.estados: dict[str, dict] = {}
        self.ai_usage: dict[tuple[str, str], dict] = {}

    def user_get(self, uid: str) -> dict | None:
        d = self.users.get(uid)
        return dict(d) if d is not None else None

    def user_merge(self, uid: str, fields: dict) -> None:
        cur = dict(self.users.get(uid) or {"uid": uid})
        # merge superficial; subscription se reemplaza entera (como la app)
        cur.update(fields)
        cur["uid"] = uid
        self.users[uid] = cur

    def users_where(self, field: str, value: str) -> list[tuple[str, dict]]:
        val = (value or "").strip().lower() if field == "email" else (value or "").strip()
        out = []
        for uid, d in self.users.items():
            got = d.get(field)
            if field == "email":
                if str(got or "").strip().lower() == val and val:
                    out.append((uid, dict(d)))
            else:
                if str(got or "").strip() == val and val:
                    out.append((uid, dict(d)))
        return out

    def estado_get(self, telefono: str) -> dict | None:
        d = self.estados.get(_tel(telefono))
        return dict(d) if d is not None else None

    def estado_set(self, telefono: str, fields: dict) -> None:
        k = _tel(telefono)
        cur = dict(self.estados.get(k) or {})
        cur.update(fields)
        cur["phone"] = k
        self.estados[k] = cur

    def estados_todos(self) -> list[tuple[str, dict]]:
        return [(k, dict(v)) for k, v in self.estados.items()]

    def pago_add(self, data: dict) -> str:
        doc = dict(data)
        doc_id = doc.get("id") or f"pago-{len(self.pagos) + 1}"
        doc["id"] = doc_id
        self.pagos.append(doc)
        return doc_id

    def pagos_con_clave(self, clave: str) -> list[dict]:
        if not clave:
            return []
        return [dict(p) for p in self.pagos if p.get("clave") == clave]

    def ai_usage_reset(self, uid: str, month_key: str, limit: int = AI_LIMIT_PRO) -> None:
        now = datetime.now(TZ).isoformat()
        self.ai_usage[(uid, month_key)] = {
            "monthKey": month_key,
            "count": 0,
            "limit": limit,
            "breakdown": {"tax": 0, "cashflow": 0, "collection": 0},
            "updatedAt": now,
        }


class _AdminFirestore:
    """Adaptador sobre firebase_admin.firestore.Client (producción, cuando haya clave)."""

    def __init__(self, db):
        self.db = db

    def user_get(self, uid: str) -> dict | None:
        snap = self.db.collection(COL_USERS).document(uid).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        data["uid"] = uid
        return data

    def user_merge(self, uid: str, fields: dict) -> None:
        self.db.collection(COL_USERS).document(uid).set(fields, merge=True)

    def users_where(self, field: str, value: str) -> list[tuple[str, dict]]:
        if not value:
            return []
        q = self.db.collection(COL_USERS).where(field, "==", value).limit(5)
        out = []
        for snap in q.stream():
            data = snap.to_dict() or {}
            data["uid"] = snap.id
            out.append((snap.id, data))
        return out

    def estado_get(self, telefono: str) -> dict | None:
        snap = self.db.collection(COL_BOT_ESTADOS).document(_tel(telefono)).get()
        if not snap.exists:
            return None
        return snap.to_dict() or {}

    def estado_set(self, telefono: str, fields: dict) -> None:
        self.db.collection(COL_BOT_ESTADOS).document(_tel(telefono)).set(fields, merge=True)

    def estados_todos(self) -> list[tuple[str, dict]]:
        out = []
        for snap in self.db.collection(COL_BOT_ESTADOS).stream():
            out.append((snap.id, snap.to_dict() or {}))
        return out

    def pago_add(self, data: dict) -> str:
        _, ref = self.db.collection(COL_PAGOS).add(data)
        return ref.id

    def pagos_con_clave(self, clave: str) -> list[dict]:
        if not clave:
            return []
        return [s.to_dict() or {} for s in self.db.collection(COL_PAGOS).where("clave", "==", clave).limit(5).stream()]

    def ai_usage_reset(self, uid: str, month_key: str, limit: int = AI_LIMIT_PRO) -> None:
        now = datetime.now(TZ).isoformat()
        self.db.collection(COL_USERS).document(uid).collection(AI_USAGE).document(month_key).set(
            {
                "monthKey": month_key,
                "count": 0,
                "limit": limit,
                "breakdown": {"tax": 0, "cashflow": 0, "collection": 0},
                "updatedAt": now,
            },
            merge=True,
        )


def set_client(client) -> None:
    """Inyecta un cliente (FakeFirestore en tests). None = volver a decidir por env."""
    global _client, _client_ready
    with _lock:
        _client = client
        _client_ready = client is not None


def reset_client() -> None:
    set_client(None)
    global _client_ready
    _client_ready = False


def _cargar_credenciales() -> tuple[dict, str] | None:
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    if not raw.startswith("{"):
        raw = base64.b64decode(raw).decode("utf-8")
    info = json.loads(raw)
    if not isinstance(info, dict) or info.get("type") != "service_account":
        print("FIREBASE_SERVICE_ACCOUNT_JSON no es una cuenta de servicio.", flush=True)
        return None
    project = (os.environ.get("FIREBASE_PROJECT_ID") or info.get("project_id") or "").strip()
    if not project:
        print("Falta FIREBASE_PROJECT_ID (y el JSON no trae project_id).", flush=True)
        return None
    return info, project


def get_client():
    """Cliente configurado, o None si no hay env / falló el arranque. No lanza."""
    global _client, _client_ready
    with _lock:
        if _client is not None:
            return _client
        if _client_ready:
            return None
        _client_ready = True
        loaded = None
        try:
            loaded = _cargar_credenciales()
        except Exception as e:
            print(f"No se pudo leer FIREBASE_SERVICE_ACCOUNT_JSON: {e}", flush=True)
            return None
        if not loaded:
            return None
        info, project = loaded
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore

            if not firebase_admin._apps:
                cred = credentials.Certificate(info)
                firebase_admin.initialize_app(cred, {"projectId": project})
            _client = _AdminFirestore(firestore.client())
            print(f"Firestore listo (proyecto {project}).", flush=True)
            return _client
        except Exception as e:
            print(f"No se pudo iniciar Firebase Admin: {e}", flush=True)
            _client = None
            return None


def configurado() -> bool:
    return get_client() is not None


# ---------------------------------------------------------------------------
# Operaciones que usa venta_panel
# ---------------------------------------------------------------------------
def buscar_usuario(telefono: str = "", email: str = "") -> tuple[str, dict] | None:
    c = get_client()
    if not c:
        return None
    try:
        if email and "@" in email:
            hits = c.users_where("email", email.strip().lower())
            if not hits:
                hits = c.users_where("email", email.strip())
            if hits:
                return hits[0]
        if telefono:
            vistos = set()
            for v in variantes_telefono(telefono):
                if v in vistos:
                    continue
                vistos.add(v)
                hits = c.users_where("phone", v)
                if hits:
                    return hits[0]
    except Exception as e:
        print(f"Error buscando usuario en Firestore: {e}", flush=True)
    return None


def cargar_estado(telefono: str, hoy: date | None = None) -> dict:
    """Estado del plan para un WhatsApp: botEstados, o la cuenta de la app si hay match."""
    vacio = {"plan": None, "inicio_prueba": "", "vencimiento": ""}
    c = get_client()
    if not c:
        return dict(vacio)
    tel = _tel(telefono)
    try:
        bot = c.estado_get(tel) or {}
        estado = {
            "plan": bot.get("plan"),
            "inicio_prueba": bot.get("inicio_prueba") or "",
            "vencimiento": bot.get("vencimiento") or "",
            "uid": bot.get("uid") or "",
            "email": bot.get("email") or "",
        }
        uid = estado["uid"]
        encontrado = None
        if uid:
            doc = c.user_get(uid)
            if doc:
                encontrado = (uid, doc)
        if not encontrado:
            encontrado = buscar_usuario(telefono=tel, email=estado.get("email") or "")
        if encontrado:
            uid, doc = encontrado
            estado["uid"] = uid
            if doc.get("email") and not estado.get("email"):
                estado["email"] = doc.get("email")
            app = estado_desde_subscription(doc.get("subscription"), hoy=hoy)
            # Si la app ya tiene Pro pagado o una prueba, eso manda (es lo que ve el cliente).
            if app.get("plan"):
                estado["plan"] = app["plan"]
                estado["inicio_prueba"] = app["inicio_prueba"] or estado["inicio_prueba"]
                estado["vencimiento"] = app["vencimiento"] or estado["vencimiento"]
        if not estado.get("plan") and not estado.get("inicio_prueba"):
            return dict(vacio)
        return estado
    except Exception as e:
        print(f"Error leyendo estado de Firestore: {e}", flush=True)
        return dict(vacio)


def _aplicar_en_app(c, uid: str, plan_bot: str, inicio: str, vencimiento: str, telefono: str = "", email: str = "") -> bool:
    doc = c.user_get(uid) or {"uid": uid}
    prev = doc.get("subscription") if isinstance(doc.get("subscription"), dict) else {}
    sub = subscription_app(plan_bot, inicio, vencimiento, prev=prev)
    fields = {"subscription": sub, "updatedAt": datetime.now(TZ).isoformat(), "uid": uid}
    # Si el perfil no tiene teléfono y nosotros sí, lo anotamos para el próximo match.
    if telefono and not (doc.get("phone") or "").strip():
        d = _tel(telefono)
        fields["phone"] = f"+{d}" if d else telefono
    if email and not (doc.get("email") or "").strip():
        fields["email"] = email.strip().lower()
    c.user_merge(uid, fields)
    if plan_bot in ("pro_mensual", "pro_anual") and (not prev or prev.get("plan") == "trial"):
        try:
            c.ai_usage_reset(uid, mes_clave(), AI_LIMIT_PRO)
        except Exception as e:
            print(f"No se pudo reiniciar aiUsage: {e}", flush=True)
    return True


def guardar_estado(telefono: str, plan: str, inicio_prueba: str, vencimiento: str,
                   email: str = "", uid: str = "", aplicar_en_app: bool = True) -> dict:
    """Escribe botEstados y, si hay cuenta vinculada, actualiza users/{uid}.subscription."""
    result = {"ok": False, "uid": uid or "", "cuenta_app": False}
    c = get_client()
    if not c:
        return result
    tel = _tel(telefono)
    try:
        if not uid or aplicar_en_app:
            hit = buscar_usuario(telefono=tel, email=email)
            if hit:
                uid = hit[0]
        now = datetime.now(TZ).isoformat()
        payload = {
            "phone": tel,
            "plan": plan,
            "inicio_prueba": inicio_prueba or "",
            "vencimiento": vencimiento or "",
            "updatedAt": now,
        }
        if email:
            payload["email"] = email.strip().lower()
        if uid:
            payload["uid"] = uid
        c.estado_set(tel, payload)
        result["ok"] = True
        result["uid"] = uid or ""
        if aplicar_en_app and uid:
            result["cuenta_app"] = _aplicar_en_app(c, uid, plan, inicio_prueba, vencimiento, telefono=tel, email=email)
        return result
    except Exception as e:
        print(f"Error guardando estado en Firestore: {e}", flush=True)
        return result


def registrar_prueba(telefono: str, inicio: str, vencimiento: str, email: str = "", uid: str = "") -> dict:
    res = guardar_estado(telefono, "prueba_pro", inicio, vencimiento, email=email, uid=uid)
    c = get_client()
    if not c:
        return res
    try:
        c.pago_add({
            "tipo": "prueba_pro",
            "phone": _tel(telefono),
            "amount": 0,
            "plan": "prueba_pro",
            "date": datetime.now(TZ).isoformat(),
            "receiptCheck": "sin cobro: activación de prueba Pro",
            "wamid": "",
            "clave": "",
            "uid": res.get("uid") or "",
            "email": email or "",
            "cuentaApp": bool(res.get("cuenta_app")),
            "inicio_prueba": inicio,
            "vencimiento": vencimiento,
        })
    except Exception as e:
        print(f"Error registrando activación de prueba: {e}", flush=True)
    return res


def registrar_pago(telefono: str, plan: str, monto: float, inicio: str, vencimiento: str,
                   clave: str = "", wamid: str = "", datos: dict | None = None,
                   receipt_ok: bool = True, email: str = "", uid: str = "",
                   metodo: str = "", payment_id: str = "") -> dict:
    datos = datos or {}
    res = guardar_estado(telefono, plan, inicio, vencimiento, email=email, uid=uid)
    c = get_client()
    if not c:
        return res
    try:
        motivos = datos.get("motivos") or ""
        receipt = "validado" if receipt_ok else f"rechazado: {motivos}".strip()
        pid = str(payment_id or datos.get("payment_id") or "")
        c.pago_add({
            "tipo": "pago",
            "phone": _tel(telefono),
            "amount": float(monto),
            "plan": plan,
            "date": datetime.now(TZ).isoformat(),
            "receiptCheck": receipt,
            "wamid": wamid or "",
            "clave": clave or "",
            "uid": res.get("uid") or "",
            "email": email or datos.get("email") or "",
            "cuentaApp": bool(res.get("cuenta_app")),
            "inicio_prueba": inicio,
            "vencimiento": vencimiento,
            "destinatario": datos.get("destinatario") or "",
            "numero_operacion": datos.get("numero_operacion") or pid,
            "metodo": metodo or datos.get("metodo") or "",
            "payment_id": pid,
        })
        res["ok"] = True
    except Exception as e:
        print(f"Error registrando pago en Firestore: {e}", flush=True)
    return res


def pago_ya_registrado(clave: str) -> bool:
    if not clave:
        return False
    c = get_client()
    if not c:
        return False
    try:
        return bool(c.pagos_con_clave(clave))
    except Exception as e:
        print(f"Error buscando pago duplicado: {e}", flush=True)
        return False


def vincular_correo(telefono: str, email: str) -> dict:
    """Guarda el correo del cliente y, si existe la cuenta, aplica el plan pendiente del bot."""
    vacio = {"ok": False, "uid": "", "cuenta_app": False, "plan": None}
    c = get_client()
    if not c or not email or "@" not in email:
        return vacio
    tel = _tel(telefono)
    try:
        bot = c.estado_get(tel) or {}
        hit = buscar_usuario(telefono=tel, email=email)
        fields = {"email": email.strip().lower(), "updatedAt": datetime.now(TZ).isoformat()}
        if hit:
            fields["uid"] = hit[0]
        c.estado_set(tel, fields)
        plan = bot.get("plan")
        inicio = bot.get("inicio_prueba") or ""
        venc = bot.get("vencimiento") or ""
        if hit and plan:
            ok_app = _aplicar_en_app(c, hit[0], plan, inicio, venc, telefono=tel, email=email)
            return {"ok": True, "uid": hit[0], "cuenta_app": ok_app, "plan": plan, "vencimiento": venc}
        return {"ok": True, "uid": (hit[0] if hit else ""), "cuenta_app": bool(hit), "plan": plan, "vencimiento": venc}
    except Exception as e:
        print(f"Error vinculando correo: {e}", flush=True)
        return vacio


_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def extraer_correo(texto: str) -> str:
    m = _EMAIL_RE.search(texto or "")
    return (m.group(0).strip().lower() if m else "")


def es_solo_correo(texto: str) -> bool:
    t = (texto or "").strip()
    m = _EMAIL_RE.fullmatch(t)
    return bool(m)
