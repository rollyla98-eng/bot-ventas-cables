"""Arranque sin claves de Firebase ni Mercado Pago (como un deploy fresco en Render).

FIREBASE_SERVICE_ACCOUNT_JSON, FIREBASE_PROJECT_ID, MP_ACCESS_TOKEN y
MP_WEBHOOK_SECRET quedan UNSET. El bot debe importar, responder el healthcheck,
verificar el webhook de WhatsApp y no caerse si llega un mensaje de venta o
una notificación de Mercado Pago. También se comprueba que un fallo al
importar firebase-admin se maneja (el bot sigue en memoria).
"""
import os, sys, builtins

# Solo las variables mínimas que el proceso de WhatsApp/Gemini exige para importar.
# Las de Firebase y Mercado Pago quedan explícitamente UNSET.
for k in ("WHATSAPP_TOKEN", "PHONE_NUMBER_ID", "VERIFY_TOKEN", "GEMINI_API_KEY"):
    os.environ[k] = "test-dummy"
os.environ["PAGO_QR_URL"] = "https://ejemplo.com/qr.png"
os.environ["PAGO_QR_MEDIA_ID"] = ""
os.environ["PANEL_COPY_URL"] = "https://aro-finance-pro.web.app"
os.environ["PANEL_VIDEO_URL"] = ""
os.environ["MODO_PRUEBA_NUMEROS"] = ""
os.environ["NOTIFICAR_A"] = ""
os.environ["SHEET_INGRESOS_ID"] = ""
os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = ""
os.environ["SEGUIMIENTO_ACTIVO"] = "0"

UNSET = (
    "FIREBASE_SERVICE_ACCOUNT_JSON",
    "FIREBASE_PROJECT_ID",
    "MP_ACCESS_TOKEN",
    "MP_WEBHOOK_SECRET",
)
for k in UNSET:
    os.environ.pop(k, None)

sys.path.insert(0, "/workspace/bvc-pr-convo")

passed = failed = 0

def check(name, cond):
    global passed, failed
    passed += bool(cond)
    failed += (not cond)
    print(("  OK  " if cond else " FAIL ") + name)


print("=== Importar la app con Firebase y Mercado Pago UNSET ===")
import venta_firestore as vf
import mercado_pago as mp
import venta_panel as vp
import conversacion as cv
import main as m

check("main, venta_panel, mercado_pago, venta_firestore y conversacion importan", True)
check("las 4 variables siguen UNSET", all(not os.environ.get(k) for k in UNSET))
check("mp_disponible es False", mp.mp_disponible() is False)
check("Firestore no configurado (no-op)", vf.configurado() is False)
check("get_client() es None, sin crash", vf.get_client() is None)
check("seguimientos apagados por defecto", cv.seguimiento_activo() is False)


print("=== FastAPI TestClient: health y webhook de WhatsApp ===")
from fastapi.testclient import TestClient

class WA:
    def __init__(s):
        s.out = []
    def send_text(s, to, body, preview_url=False):
        s.out.append(("text", to, body))
    def send_image(s, to, link=None, media_id=None, caption=""):
        s.out.append(("image", to, link or media_id))
    def textos(s, to):
        return [x[2] for x in s.out if x[0] == "text" and x[1] == to]
    def imagenes(s, to):
        return [x[2] for x in s.out if x[0] == "image" and x[1] == to]

client = TestClient(m.app)
home = client.get("/")
check("GET / responde 200", home.status_code == 200 and home.json().get("status") == "Servidor activo")

# Verificación del webhook (el token de prueba, no un secreto real)
ver = client.get("/webhook", params={
    "hub.mode": "subscribe",
    "hub.verify_token": "test-dummy",
    "hub.challenge": "desafio-123",
})
check("GET /webhook verify funciona (200 + challenge)", ver.status_code == 200 and ver.text == "desafio-123")
mal = client.get("/webhook", params={
    "hub.mode": "subscribe",
    "hub.verify_token": "token-falso",
    "hub.challenge": "x",
})
check("GET /webhook con token falso da 403", mal.status_code == 403)

# Mensaje de venta: mock de Gemini para no salir a la red. El flujo debe ofrecer solo el QR.
wa = WA()
m._WA = wa
cv.configurar(enviar_texto=wa.send_text)
m._chats_venta.clear()
m._silencio_dueno.clear()
m.generar_respuesta = lambda phone, text: (
    f"Claro, te paso cómo pagar el Pro.\n{vp.MARCADOR_PAGO}"
)
payload = {
    "object": "whatsapp_business_account",
    "entry": [{"changes": [{"value": {"messages": [{
        "id": "w-arranque-1",
        "from": "51912312312",
        "type": "text",
        "text": {"body": "Hola, quiero el Panel, ¿cuál es el precio?"},
    }]}}]}],
}
resp = client.post("/webhook", json=payload)
check("POST /webhook con mensaje de venta responde 200", resp.status_code == 200)
# TestClient corre las background tasks antes de devolver
txt = "\n".join(wa.textos("51912312312"))
imgs = wa.imagenes("51912312312")
check("el camino de respuesta no se cae y ofrece el QR Plin/Yape", "https://ejemplo.com/qr.png" in imgs or any("Plin" in x or "Yape" in x for x in wa.textos("51912312312")))
check("envía la imagen del QR", "https://ejemplo.com/qr.png" in imgs)
check("no envía link de Mercado Pago (token UNSET)", "mercadopago.com" not in txt.lower() and "init_point" not in txt.lower())
check("sí quedan las instrucciones de Plin o Yape", any("Plin" in x or "Yape" in x for x in wa.textos("51912312312")))


print("=== POST /mp/webhook sin token: 200 y no hace nada ===")
antes_plan = dict(vp.estado_plan("51912312312"))
mp_resp = client.post("/mp/webhook", json={"type": "payment", "data": {"id": "999999"}})
check("POST /mp/webhook responde 200", mp_resp.status_code == 200)
# La tarea de fondo no debe activar Pro ni caerse
despues = vp.estado_plan("51912312312")
check("no activa Pro ni cambia el plan", despues.get("plan") == antes_plan.get("plan") and not despues.get("pro"))
r = mp.procesar_notificacion({"type": "payment", "data": {"id": "999999"}})
check("procesar_notificacion queda en sin_token", r.get("motivo") == "sin_token" and r.get("ok") is False)


print("=== Error al importar firebase-admin se maneja ===")
# No se usan claves reales: se inyecta un loader falso y se simula que el import falla.
vf.reset_client()
vf._cargar_credenciales = lambda: ({"type": "service_account", "project_id": "demo"}, "demo")
real_import = builtins.__import__

def fake_import(name, *args, **kwargs):
    if name == "firebase_admin" or name.startswith("firebase_admin."):
        raise ImportError("simulated firebase-admin missing")
    return real_import(name, *args, **kwargs)

builtins.__import__ = fake_import
try:
    cliente = vf.get_client()
    check("get_client() no explota si firebase-admin falla al importar", cliente is None)
    check("configurado() queda en False", vf.configurado() is False)
finally:
    builtins.__import__ = real_import
    vf.reset_client()
    for k in UNSET:
        os.environ.pop(k, None)


print(f"\nResultado: {passed} OK, {failed} FAIL")
sys.exit(0 if failed == 0 else 1)
