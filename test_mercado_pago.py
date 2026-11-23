"""Pruebas de Mercado Pago (HTTP simulado, sin token real ni red)."""
import hashlib
import hmac
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

for k in ("WHATSAPP_TOKEN", "PHONE_NUMBER_ID", "VERIFY_TOKEN", "GEMINI_API_KEY"):
    os.environ.setdefault(k, "test-dummy")
os.environ.update(
    MODO_PRUEBA_NUMEROS="",
    PANEL_COPY_URL="https://docs.google.com/spreadsheets/d/X/copy",
    PANEL_VIDEO_URL="https://youtu.be/demo",
    PAGO_QR_URL="https://ejemplo.com/qr.png",
    NOTIFICAR_A="51900000000",
    SHEET_INGRESOS_ID="hoja-test",
    GOOGLE_SERVICE_ACCOUNT_JSON="",
    FIREBASE_PROJECT_ID="",
    FIREBASE_SERVICE_ACCOUNT_JSON="",
    MP_ACCESS_TOKEN="",
    MP_WEBHOOK_SECRET="",
    PUBLIC_BASE_URL="https://bot-ventas-cables.onrender.com",
    PLAN_GRATIS_MOVIMIENTOS="30",
    PLAN_GRATIS_FIADOS="3",
)
sys.path.insert(0, "/workspace/bvc-pr-convo")

import mercado_pago as mp
import venta_firestore as vf
import venta_panel as vp
import main as m

TZ = ZoneInfo("America/Lima")
AHORA = datetime(2026, 11, 23, 15, 30, tzinfo=TZ)
vp._ahora = lambda: AHORA

passed = failed = 0

def check(name, cond):
    global passed, failed
    passed += bool(cond)
    failed += (not cond)
    print(("  OK  " if cond else " FAIL ") + name)


class WA:
    def __init__(s):
        s.out = []
    def send_text(s, to, body, preview_url=False):
        s.out.append(("text", to, body))
    def send_image(s, to, link=None, media_id=None, caption=""):
        s.out.append(("image", to, link or media_id))
    def textos(s, to):
        return [b for t, d, b in s.out if t == "text" and d == to]
    def imagenes(s, to):
        return [b for t, d, b in s.out if t == "image" and d == to]
    def clear(s):
        s.out.clear()


HOJA = []

class FakeResp:
    def __init__(s, data=None):
        s.data = data or {}
    def raise_for_status(s):
        pass
    def json(s):
        return s.data

class FakeSession:
    def get(s, url, timeout=0):
        return FakeResp({"values": [list(f) for f in HOJA]})
    def post(s, url, params=None, json=None, timeout=0):
        HOJA.extend(json["values"])
        return FakeResp()

vp._sesion_sheets = lambda: FakeSession()

CLI = "51999111222"
ROLLY = "51900000000"
TOKEN = "TEST-dummy-access-token"
SECRET = "test-webhook-secret"
wa = WA()


def set_token(val=""):
    if val:
        os.environ["MP_ACCESS_TOKEN"] = val
    else:
        os.environ.pop("MP_ACCESS_TOKEN", None)


def set_secret(val=""):
    if val:
        os.environ["MP_WEBHOOK_SECRET"] = val
    else:
        os.environ.pop("MP_WEBHOOK_SECRET", None)


def firmar(pid, request_id="req-abc", ts="1704908010", secret=SECRET):
    manifest = mp.manifest_firma(str(pid), request_id, ts)
    v1 = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return {"x-signature": f"ts={ts},v1={v1}", "x-request-id": request_id}


CAPTURA = []

def fake_post(payload):
    CAPTURA.append(payload)
    pref_id = f"pref-{len(CAPTURA)}"
    return {
        "id": pref_id,
        "init_point": f"https://www.mercadopago.com.pe/checkout/v1/redirect?pref_id={pref_id}",
        "sandbox_init_point": f"https://sandbox.mercadopago.com.pe/checkout/v1/redirect?pref_id={pref_id}",
    }

mp._post_preferencia = fake_post
PAGOS_API = {}

def fake_get(payment_id):
    if payment_id not in PAGOS_API:
        raise RuntimeError(f"pago {payment_id} no existe")
    return dict(PAGOS_API[payment_id])

mp._get_pago = fake_get


def pago_api(pid, status, amount, plan="pro_mensual", tel=CLI, currency="PEN", nonce="abc123"):
    return {
        "id": pid,
        "status": status,
        "transaction_amount": amount,
        "currency_id": currency,
        "external_reference": mp.armar_referencia(tel, plan, nonce),
    }


print("=== Sin token: cae al QR y no crea preferencias ===")
set_token("")
set_secret("")
CAPTURA.clear()
wa.clear()
check("mp_disponible es False", mp.mp_disponible() is False)
check("crear_links vacío", mp.crear_links(CLI) == [])
check("crear_preferencia es None", mp.crear_preferencia(CLI, "pro_mensual") is None)
vp.procesar_respuesta(wa, CLI, f"Te paso los datos de pago.\n{vp.MARCADOR_PAGO}")
check("sin token sigue enviando el QR", wa.imagenes(CLI) == [vp.PAGO_QR_URL])
check("instrucciones de Plin/Yape", any("Plin o Yape" in x for x in wa.textos(CLI)))
check("no mandó link de Mercado Pago", not any("mercadopago.com" in x.lower() for x in wa.textos(CLI)))
check("no se llamó a la API de preferencias", CAPTURA == [])
wa.clear()
vp.procesar_respuesta(wa, CLI, f"{vp.MARCADOR_LINK_MP}")
check("pedir solo tarjeta sin token cae al QR", wa.imagenes(CLI) == [vp.PAGO_QR_URL])
check("texto de ofrecer Pro no menciona tarjeta si no hay token", "tarjeta" not in vp.mensaje_ofrecer_pro())


print("=== Preferencia: payload de Checkout Pro ===")
set_token(TOKEN)
CAPTURA.clear()
pref = mp.crear_preferencia(CLI, "pro_mensual", nonce="n1")
check("se creó preferencia mensual", pref is not None and pref["link"])
body = CAPTURA[-1]
item = body["items"][0]
check("POST unit_price 19 y currency PEN", item["unit_price"] == 19 and item["currency_id"] == "PEN")
check("título mensual exacto", item["title"] == "Panel Financiero Pro mensual")
check("quantity 1", item["quantity"] == 1)
check("external_reference con teléfono y plan", body["external_reference"] == f"bvc|{CLI}|pro_mensual|n1")
check("notification_url del bot", body["notification_url"] == "https://bot-ventas-cables.onrender.com/mp/webhook")
check("back_urls opcionales presentes", "success" in body.get("back_urls", {}))
check("con TEST- usa sandbox_init_point", "sandbox.mercadopago.com" in pref["link"])
CAPTURA.clear()
pref_a = mp.crear_preferencia(CLI, "pro_anual", nonce="n2")
item_a = CAPTURA[-1]["items"][0]
check("anual unit_price 149 y título exacto", item_a["unit_price"] == 149 and item_a["title"] == "Panel Financiero Pro anual")
parse = mp.parsear_referencia(CAPTURA[-1]["external_reference"])
check("referencia anual se parsea", parse == {"telefono": CLI, "plan": "pro_anual", "nonce": "n2"})


print("=== Oferta al cliente: QR + ambos links (o el plan elegido) ===")
CAPTURA.clear()
wa.clear()
vp.procesar_respuesta(wa, CLI, f"Claro, te paso el pago.\n{vp.MARCADOR_PAGO}")
check("con token sigue enviando el QR", wa.imagenes(CLI) == [vp.PAGO_QR_URL])
txt = "\n".join(wa.textos(CLI))
check("menciona Mercado Pago y los dos links", "Mercado Pago" in txt and "sandbox.mercadopago.com" in txt)
check("creó mensual y anual", len(CAPTURA) == 2)
check("texto de ofrecer Pro menciona tarjeta", "tarjeta" in vp.mensaje_ofrecer_pro())
wa.clear()
CAPTURA.clear()
vp.procesar_respuesta(wa, CLI, f"Te mando el link anual.\n{vp.MARCADOR_LINK_MP}:pro_anual")
check("marcador de anual no deja basura", not any("pro_anual" in x and "[[" in x for x in wa.textos(CLI)))
check("solo creó preferencia anual", len(CAPTURA) == 1 and CAPTURA[0]["items"][0]["unit_price"] == 149)
check("no reenvió QR si pidió solo tarjeta", wa.imagenes(CLI) == [])
check("prompt ofrece Plin/Yape o tarjeta", "Plin/Yape con QR o tarjeta con Mercado Pago" in vp.bloque_prompt_panel(CLI))


print("=== Webhook: aprobado activa Pro por el mismo hook ===")
vp._suscripciones.clear()
vp._entregados.clear()
HOJA.clear()
wa.clear()
mp.configurar_wa(wa)
PAGOS_API.clear()
PAGOS_API["111"] = pago_api("111", "approved", 19, "pro_mensual", nonce="x1")
res = mp.procesar_notificacion({"type": "payment", "data": {"id": "111"}}, wa=wa)
e = vp.estado_plan(CLI)
check("aprobado 19 → pro_mensual activo", res["ok"] and res["motivo"] == "activado" and e["plan"] == "pro_mensual" and e["pro"])
check("vence +30d desde hoy", e["vencimiento"] == (AHORA.date() + timedelta(days=30)).isoformat())
check("avisa al cliente (Pago confirmado)", any("Pago confirmado" in x and "Pro mensual" in x for x in wa.textos(CLI)))
check("hoja método mercadopago e id", HOJA[-1][3] == "mercadopago" and HOJA[-1][8] == "mp:111" and HOJA[-1][2] == "19.00" and HOJA[-1][10] == "pro_mensual")
check("aviso a Rolly", any("Mercado Pago" in x and "111" in x for x in wa.textos(ROLLY)))
venc_m = e["vencimiento"]


print("=== Rechazado / monto incorrecto / moneda / plan no calza ===")
n_hoja = len(HOJA)
wa.clear()
PAGOS_API["222"] = pago_api("222", "rejected", 19, "pro_mensual")
r = mp.procesar_notificacion({"type": "payment", "data": {"id": "222"}}, wa=wa)
check("rejected no activa", r["ok"] is False and "estado_rejected" in r["motivo"])
PAGOS_API["223"] = pago_api("223", "pending", 19, "pro_mensual")
r = mp.procesar_notificacion({"type": "payment", "data": {"id": "223"}}, wa=wa)
check("pending no activa", r["ok"] is False)
PAGOS_API["224"] = pago_api("224", "approved", 20, "pro_mensual")
r = mp.procesar_notificacion({"type": "payment", "data": {"id": "224"}}, wa=wa)
check("monto 20 rechazado", r["ok"] is False and r["motivo"] == "monto")
PAGOS_API["225"] = pago_api("225", "approved", 19, "pro_anual")  # 19 vs plan anual
r = mp.procesar_notificacion({"type": "payment", "data": {"id": "225"}}, wa=wa)
check("19 con referencia anual rechazado", r["ok"] is False and r["motivo"] == "monto_plan")
PAGOS_API["226"] = pago_api("226", "approved", 19, "pro_mensual", currency="USD")
r = mp.procesar_notificacion({"type": "payment", "data": {"id": "226"}}, wa=wa)
check("USD rechazado", r["ok"] is False and r["motivo"] == "moneda")
check("plan del cliente no cambió", vp.estado_plan(CLI)["vencimiento"] == venc_m)
check("rechazos no escriben hoja", len(HOJA) == n_hoja)
check("no se avisó al cliente en rechazos", wa.textos(CLI) == [])


print("=== Duplicado: el mismo payment id no activa dos veces ===")
wa.clear()
antes = vp.estado_plan(CLI)["vencimiento"]
r2 = mp.procesar_notificacion({"type": "payment", "data": {"id": "111"}}, wa=wa)
check("segundo aviso es duplicado", r2["ok"] is True and r2["motivo"] == "duplicado")
check("vencimiento no se extendió", vp.estado_plan(CLI)["vencimiento"] == antes)
check("no reenvía confirmación al cliente", wa.textos(CLI) == [])
# también desde la hoja, como tras un reinicio
vp._entregados.clear()
r3 = mp.procesar_notificacion({"type": "payment", "data": {"id": "111"}}, wa=wa)
check("duplicado se ve en la hoja tras vaciar memoria", r3["motivo"] == "duplicado")
check("sigue sin extender", vp.estado_plan(CLI)["vencimiento"] == antes)


print("=== Anual 149 extiende desde el vencimiento vigente ===")
wa.clear()
PAGOS_API["1490"] = pago_api("1490", "approved", 149, "pro_anual", nonce="anio")
r = mp.procesar_notificacion({"topic": "payment", "data": {"id": "1490"}}, wa=wa)
check("149 → pro_anual", r["ok"] and vp.estado_plan(CLI)["plan"] == "pro_anual")
check("+365 desde el vencimiento mensual", vp.estado_plan(CLI)["vencimiento"] == (datetime.fromisoformat(venc_m).date() + timedelta(days=365)).isoformat())


print("=== Firma x-signature ===")
set_secret(SECRET)
body = {"type": "payment", "data": {"id": "111"}}
ok_headers = firmar("111")
check("firma válida", mp.firma_valida(body, ok_headers))
bad = dict(ok_headers)
bad["x-signature"] = ok_headers["x-signature"][:-2] + "00"
check("firma alterada es inválida", mp.firma_valida(body, bad) is False)
check("sin header es inválida si hay secreto", mp.firma_valida(body, {}) is False)
set_secret("")
check("sin secreto se omite la verificación", mp.firma_valida(body, {}) is True)


print("=== Endpoint POST /mp/webhook (200 rápido, 401 si firma mala) ===")
from fastapi.testclient import TestClient
set_token(TOKEN)
set_secret(SECRET)
client = TestClient(m.app)
PAGOS_API["333"] = pago_api("333", "approved", 19, "pro_mensual", tel="51933333333", nonce="wh")
vp._suscripciones.pop("51933333333", None)
headers = firmar("333")
resp = client.post("/mp/webhook", json={"type": "payment", "data": {"id": "333"}}, headers=headers)
check("webhook 200 con firma válida", resp.status_code == 200 and resp.json().get("status") == "ok")
check("background activó Pro del 51933333333", vp.estado_plan("51933333333")["plan"] == "pro_mensual")
resp_bad = client.post("/mp/webhook", json={"type": "payment", "data": {"id": "333"}},
                       headers={"x-signature": "ts=1,v1=deadbeef", "x-request-id": "x"})
check("firma mala → 401", resp_bad.status_code == 401)
# IPN por query
set_secret("")  # sin secreto, se acepta
PAGOS_API["444"] = pago_api("444", "approved", 19, "pro_mensual", tel="51944444444", nonce="ipn")
resp_ipn = client.post("/mp/webhook?topic=payment&id=444")
check("IPN topic=payment&id= 200", resp_ipn.status_code == 200)
check("IPN activó Pro", vp.estado_plan("51944444444")["plan"] == "pro_mensual")
ping = client.get("/mp/webhook")
check("GET /mp/webhook responde ok", ping.status_code == 200)


print("=== Firestore: metodo=mercadopago y payment_id en pagos ===")
set_token(TOKEN)
fake = vf.FakeFirestore()
vf.set_client(fake)
# cuenta de la app ya existente
fake.user_merge("uid-ana", {
    "uid": "uid-ana",
    "email": "ana@tienda.pe",
    "ownerName": "Ana",
    "phone": "+51 999 111 222",
    "subscription": vf.subscription_app("prueba_pro", AHORA.date().isoformat(), (AHORA.date() + timedelta(days=15)).isoformat()),
})
wa.clear()
PAGOS_API["555"] = pago_api("555", "approved", 19, "pro_mensual", tel="51999111222", nonce="fs1")
# usar un teléfono limpio vinculado
TEL_FS = "51999111222"
# already used CLI; that's ok, just record payment
r = mp.procesar_notificacion({"type": "payment", "data": {"id": "555"}}, wa=wa)
pago_fs = next((p for p in fake.pagos if p.get("payment_id") == "555" or p.get("clave") == "mp:555"), None)
check("pagos tiene metodo mercadopago", pago_fs is not None and pago_fs.get("metodo") == "mercadopago")
check("pagos guarda el payment_id de MP", pago_fs is not None and pago_fs.get("payment_id") == "555")
check("subscription de la app quedó monthly/active", fake.users["uid-ana"]["subscription"]["plan"] == "monthly" and fake.users["uid-ana"]["subscription"]["status"] == "active")
check("no se pisó ownerName", fake.users["uid-ana"]["ownerName"] == "Ana")
vf.reset_client()


print("=== Siempre se relee el pago (no se confía en el body) ===")
# body dice approved, la API dice rejected
PAGOS_API["666"] = pago_api("666", "rejected", 19, "pro_mensual", tel="51966666666")
r = mp.procesar_notificacion({"type": "payment", "data": {"id": "666"}, "action": "payment.updated"}, wa=wa)
check("se ignora el body y se usa el GET", r["ok"] is False and not vp.estado_plan("51966666666")["pro"])


print(f"\nResultado: {passed} OK, {failed} FAIL")
sys.exit(0 if failed == 0 else 1)
