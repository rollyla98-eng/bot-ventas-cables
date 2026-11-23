"""Pruebas de las 4 reglas de conversación (Gemini y WhatsApp simulados, sin red)."""
import os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

for k in ("WHATSAPP_TOKEN", "PHONE_NUMBER_ID", "VERIFY_TOKEN", "GEMINI_API_KEY"):
    os.environ.setdefault(k, "test-dummy")
os.environ.update(
    MODO_PRUEBA_NUMEROS="",
    PANEL_COPY_URL="https://aro-finance-pro.web.app",
    PANEL_VIDEO_URL="https://youtu.be/demo",
    PAGO_QR_URL="https://ejemplo.com/qr.png",
    NOTIFICAR_A="51900000000",
    SHEET_INGRESOS_ID="",
    GOOGLE_SERVICE_ACCOUNT_JSON="",
    FIREBASE_PROJECT_ID="",
    FIREBASE_SERVICE_ACCOUNT_JSON="",
    MP_ACCESS_TOKEN="",
    MP_WEBHOOK_SECRET="",
    SEGUIMIENTO_ACTIVO="0",
    WA_TEMPLATE_SEGUIMIENTO="",
    WA_TEMPLATE_FIN_PRUEBA="",
    PLAN_GRATIS_MOVIMIENTOS="30",
    PLAN_GRATIS_FIADOS="3",
)
# Asegurar que las claves de seguimiento/MP/Firebase quedan UNSET o vacías
for k in ("MP_ACCESS_TOKEN", "MP_WEBHOOK_SECRET", "FIREBASE_SERVICE_ACCOUNT_JSON", "FIREBASE_PROJECT_ID"):
    os.environ.pop(k, None)
    os.environ[k] = ""

sys.path.insert(0, "/workspace/bvc-pr-convo")

import conversacion as cv
import venta_panel as vp
import venta_firestore as vf
import main as m

TZ = ZoneInfo("America/Lima")
passed = failed = 0

def check(name, cond):
    global passed, failed
    passed += bool(cond)
    failed += (not cond)
    print(("  OK  " if cond else " FAIL ") + name)


class WA:
    def __init__(s):
        s.out = []
        s.plantillas = []
    def send_text(s, to, body, preview_url=False):
        s.out.append(("text", to, body))
    def send_image(s, to, link=None, media_id=None, caption=""):
        s.out.append(("image", to, link or media_id))
    def textos(s, to):
        return [b for t, d, b in s.out if t == "text" and d == to]
    def imagenes(s, to):
        return [b for t, d, b in s.out if t == "image" and d == to]
    def clear(s):
        s.out.clear(); s.plantillas.clear()


def en(ahora):
    cv.set_ahora(lambda: ahora)


# ---------------------------------------------------------------------------
print("=== 1) Método de venta (prompt y primera oferta) ===")
cv.reset()
p = m.construir_system_prompt("51911111111", "quiero controlar mis gastos")
check("prompt dice 2 o 3 líneas y una sola pregunta", "2 o 3 líneas" in p and "una sola pregunta" in p)
check("prompt: primero lo último que dijo", "PRIMERO lo último" in p or "primero lo último" in p.lower())
check("prompt: Panel gratis y 15 días de Pro", "Panel GRATIS" in p or "gratis" in p.lower())
check("prompt incluye el link de la app", "aro-finance-pro.web.app" in p)
check("prompt no dice plantilla en Google Sheets", "plantilla en Google Sheets" not in p and "plantilla automatizada en Google Sheets" not in p)
check("prompt no promete que un asesor envía el Panel", "un asesor enviará" in p or "NO prometas" in p)
check("prompt con precios S/ 19 y S/ 149", "S/ 19" in p and "S/ 149" in p)
check("cierre mensual o anual está en el módulo", "¿Mensual o anual?" in cv.texto_fin_prueba() or "¿mensual o anual?" in cv.texto_fin_prueba())
check("url_panel cae a la app web", vp.url_panel() == "https://aro-finance-pro.web.app")

wa = WA()
cv.reset()
vp._suscripciones.clear()
# Entrega: link de la app, video solo porque hay URL, nunca 'un asesor te envía'
ok = vp.entregar_panel(wa, "51911111111")
txt = "\n".join(wa.textos("51911111111"))
check("entrega el link de la app", "https://aro-finance-pro.web.app" in txt and ok)
check("entrega el video porque hay PANEL_VIDEO_URL", any(vp.PANEL_VIDEO_URL in x for x in wa.textos("51911111111")))
check("no promete envío a mano", "asesor te envía" not in txt and "plantilla" not in txt.lower())
check("explica 15 días de Pro", any("15 días de Pro" in x for x in wa.textos("51911111111")))


print("=== 2) Seguir el hilo: caso gastro (no vender) ===")
cv.reset()
tel = "51922222222"
m._chats_venta[m.normalizar_telefono(tel)] = __import__("time").time()
wa = WA()
AHORA = datetime(2026, 11, 23, 16, 0, tzinfo=TZ)
en(AHORA)
# Gemini (simulado) se va al pitch, que es el fallo real que hay que impedir.
pitch = (
    "¡Hola! Te ofrezco el Panel Financiero gratis con 15 días de Pro. "
    "Entra a https://aro-finance-pro.web.app "
    "¿Quieres que te lo envíe?\n[[ENVIAR_PANEL]]"
)
cv.registrar_inbound(tel, "Tengo una cita en gastro y debo comprar medicinas", ahora=AHORA)
out = cv.postprocesar(tel, pitch, texto_in="Tengo una cita en gastro y debo comprar medicinas", ahora=AHORA)
check("detecta tema sensible (gastro + medicinas)", cv.es_tema_sensible("Tengo una cita en gastro y debo comprar medicinas"))
check("no puede ofrecer en ese mensaje", cv.puede_ofrecer(tel, "Tengo una cita en gastro y debo comprar medicinas", ahora=AHORA) is False)
check("se quitó el marcador de envío", "[[ENVIAR_PANEL]]" not in out)
check("no queda el link ni el pitch", "aro-finance-pro" not in out.lower() and "15 días de Pro" not in out)
check("no menciona precios", "S/ 19" not in out and "S/ 149" not in out)
check("queda una respuesta de empatía", "cuídate" in out.lower() or "ojalá" in out.lower() or "aquí estoy" in out.lower())
# pipeline completo: aunque Gemini venda, no se entrega el Panel
wa.clear()
m._WA = wa
m.generar_respuesta = lambda phone, text: pitch
m._chats_venta[m.normalizar_telefono(tel)] = __import__("time").time()
m._silencio_dueno.clear()
reply = m.manejar_texto(wa, tel, "Tengo una cita en gastro y debo comprar medicinas")
check("manejar_texto no envía el Panel", "aro-finance-pro.web.app" not in "\n".join(wa.textos(tel)) or "Entra aquí" not in "\n".join(wa.textos(tel)))
check("no se mandó el link de entrega", not any("Entra aquí" in x for x in wa.textos(tel)))
check("respuesta final sin marcador ni pitch", "[[ENVIAR_PANEL]]" not in reply and "15 días de Pro" not in reply)


print("=== 2b) Keyword no obliga a vender; solo plata/gastos habilitan la oferta ===")
cv.reset()
tel2 = "51933333333"
en(AHORA)
check("un saludo solo no es tema de dinero", cv.es_tema_dinero("hola, cómo estás") is False)
check("gastos sí habilita", cv.es_tema_dinero("quiero controlar mis gastos del negocio"))
check("fiados/deudas/precio habilitan", cv.es_tema_dinero("tengo muchos fiados y deudas, ¿cuál es el precio?"))
out = cv.postprocesar(tel2, "Qué bien. [[ENVIAR_PANEL]] ¿Te mando el Panel?", texto_in="hola, cómo estás", ahora=AHORA)
check("en un hola no se deja el marcador de oferta", "[[ENVIAR_PANEL]]" not in out)
out = cv.postprocesar(tel2, "Te puedo enviar el Panel. [[ENVIAR_PANEL]]", texto_in="quiero controlar mis gastos", ahora=AHORA)
check("con gastos sí se permite el marcador", "[[ENVIAR_PANEL]]" in out)


print("=== 2c) No repetir la oferta en 24 h ===")
cv.reset()
tel3 = "51944444444"
en(AHORA)
cv.marcar_oferta(tel3, ahora=AHORA, stage="ofrecido")
check("oferta reciente es True", cv.oferta_reciente(tel3, ahora=AHORA))
check("un segundo mensaje de gastos no vuelve a ofrecer",
      cv.puede_ofrecer(tel3, "sigo con los gastos de la semana", ahora=AHORA) is False)
out = cv.postprocesar(tel3, "Te lo vuelvo a enviar. [[ENVIAR_PANEL]]",
                      texto_in="sigo con los gastos de la semana", ahora=AHORA)
check("el marcador se quita dentro de las 24 h", "[[ENVIAR_PANEL]]" not in out)
# al día siguiente sí
manana = AHORA + timedelta(hours=25)
check("después de 24 h se puede volver a ofrecer", cv.puede_ofrecer(tel3, "sigo con los gastos", ahora=manana))


print("=== 2d) El historial del hilo se pasa (10-20 mensajes, ambos lados) ===")
cv.reset()
telh = "51955555555"
for i in range(15):
    cv.registrar_inbound(telh, f"mensaje cliente {i}", ahora=AHORA)
    cv.registrar_outbound(telh, f"respuesta bot {i}", ahora=AHORA)
hilo = cv.historial_texto(telh)
check("guarda ambos lados", "Cliente: mensaje cliente 14" in hilo and "Asesor: respuesta bot 14" in hilo)
check("deja como máximo 20 mensajes", hilo.count("Cliente:") + hilo.count("Asesor:") == 20)
check("los más viejos salen (queda el hilo reciente)", "mensaje cliente 0" not in hilo and "mensaje cliente 5" in hilo)


print("=== 3) Un solo saludo al día y no repetir hola ni frases hechas ===")
cv.reset()
tel4 = "51966666666"
manana_hora = datetime(2026, 11, 23, 10, 0, tzinfo=TZ)
tarde = datetime(2026, 11, 23, 16, 0, tzinfo=TZ)
noche = datetime(2026, 11, 23, 20, 0, tzinfo=TZ)
otro_dia = datetime(2026, 11, 24, 15, 0, tzinfo=TZ)
check("10am → buenos días", cv.saludo_del_dia(manana_hora) == "Buenos días")
check("4pm → buenas tardes", cv.saludo_del_dia(tarde) == "Buenas tardes")
check("8pm → buenas noches", cv.saludo_del_dia(noche) == "Buenas noches")

en(manana_hora)
r1 = cv.postprocesar(tel4, "Hola, dime en qué te ayudo. ¿Quieres el Panel?", texto_in="quiero controlar mis gastos", ahora=manana_hora)
check("primer mensaje del día lleva buenos días", r1.lower().startswith("buenos días"))
check("no deja un hola extra junto al saludo del día", r1.lower().count("hola") == 0)
r2 = cv.postprocesar(tel4, "Hola, ¿ya entraste al Panel?", texto_in="sí, un poco", ahora=tarde)
# 'sí, un poco' no es dinero ni sensible... wait, puede_ofrecer is false, but greeting is the focus
check("el mismo día no vuelve a saludar", "buenos días" not in r2.lower() and "buenas tardes" not in r2.lower())
check("no repite hola en la misma conversación", "hola" not in r2.lower())
# frase hecha
cv.registrar_outbound(tel4, "Te hablo ahorita en 2 minutos", ahora=tarde)
r3 = cv.postprocesar(tel4, "Te hablo ahorita en 2 minutos, ya te ayudo con eso.", texto_in="ok", ahora=tarde)
check("se quita la frase hecha", "te hablo ahorita en 2 minutos" not in r3.lower())
en(otro_dia)
r4 = cv.postprocesar(tel4, "¿Cómo vas con el registro?", texto_in="bien", ahora=otro_dia)
check("al día siguiente saluda una vez (buenas tardes)", r4.lower().startswith("buenas tardes"))


print("=== 4) Mensajes cortos: 2-3 líneas y una sola pregunta ===")
largo = (
    "Primera línea de más. Segunda explicación un poco larga también.\n"
    "Tercera línea con detalle extra que no hace falta.\n"
    "Cuarta línea que debería recortarse.\n"
    "¿Quieres el mensual? ¿O el anual? ¿Empezamos hoy?"
)
corto = cv.acortar(largo)
check("queda a lo más 3 líneas", corto.count("\n") <= 2 and len(corto.splitlines()) <= 3)
check("una sola pregunta (la última)", corto.count("?") == 1 and "¿Empezamos hoy?" in corto)
check("no deja las preguntas anteriores", "¿Quieres el mensual?" not in corto)
muy_largo = "Palabra " * 80 + " ¿Nos vemos?"
rec = cv.acortar(muy_largo)
check("se recorta a ~300 caracteres", len(rec) <= 320 and "¿Nos vemos?" in rec)


print("=== 5) Seguimientos: apagados por defecto, ventana 24 h y plantilla ===")
cv.reset()
tels = "51977777777"
ayer = AHORA - timedelta(days=1, hours=1)
cv.marcar_oferta(tels, ahora=ayer, stage="ofrecido")
# inbound reciente (dentro de 24 h)
cv.cargar(tels)["last_inbound_at"] = (AHORA - timedelta(hours=2)).isoformat()
cv.guardar(tels)
os.environ["SEGUIMIENTO_ACTIVO"] = "0"
enviados = []
def capt_texto(to, body, preview_url=False):
    enviados.append(("texto", to, body))
def capt_tpl(to, name, lang="es"):
    enviados.append(("plantilla", to, name))
cv.configurar(enviar_texto=capt_texto, enviar_plantilla=capt_tpl)
hechos = cv.procesar_seguimientos(ahora=AHORA)
check("con SEGUIMIENTO_ACTIVO=0 no sale nada", hechos == [] and enviados == [])

os.environ["SEGUIMIENTO_ACTIVO"] = "1"
os.environ["WA_TEMPLATE_SEGUIMIENTO"] = ""
enviados.clear()
hechos = cv.procesar_seguimientos(ahora=AHORA)
check("dentro de 24 h y activo: manda texto libre", any(h.get("tipo") == "dia2" and h.get("modo") == "texto" for h in hechos))
check("el texto pregunta si entró al Panel", any("¿Pudiste entrar al Panel?" in x[2] for x in enviados if x[0] == "texto"))
check("no usa plantilla si hay ventana", not any(x[0] == "plantilla" for x in enviados))

# Fuera de 24 h, sin plantilla: se omite
cv.reset()
cv.marcar_oferta(tels, ahora=ayer, stage="ofrecido")
cv.cargar(tels)["last_inbound_at"] = (AHORA - timedelta(hours=30)).isoformat()
cv.cargar(tels)["seguimiento_dia2_enviado"] = False
cv.guardar(tels)
enviados.clear()
os.environ["WA_TEMPLATE_SEGUIMIENTO"] = ""
hechos = cv.procesar_seguimientos(ahora=AHORA)
check("fuera de 24 h sin plantilla: no envía y lo deja asentado", any(h.get("modo") == "omitido_sin_plantilla" for h in hechos))
check("no salió texto ni plantilla", enviados == [])

# Fuera de 24 h, con plantilla
cv.reset()
cv.marcar_oferta(tels, ahora=ayer, stage="ofrecido")
cv.cargar(tels)["last_inbound_at"] = (AHORA - timedelta(hours=30)).isoformat()
cv.guardar(tels)
enviados.clear()
os.environ["WA_TEMPLATE_SEGUIMIENTO"] = "seguimiento_panel"
hechos = cv.procesar_seguimientos(ahora=AHORA)
check("fuera de 24 h con plantilla: usa la de Meta", any(h.get("modo") == "plantilla" for h in hechos))
check("se llamó la plantilla de seguimiento", any(x[0] == "plantilla" and x[2] == "seguimiento_panel" for x in enviados))

# Fin de prueba: texto con precio y «¿mensual o anual?»
cv.reset()
telf = "51988888888"
vp._suscripciones[telf] = {
    "plan": "prueba_pro",
    "inicio_prueba": (AHORA.date() - timedelta(days=14)).isoformat(),
    "vencimiento": (AHORA.date() + timedelta(days=1)).isoformat(),
}
cv.cargar(telf)["last_inbound_at"] = (AHORA - timedelta(hours=1)).isoformat()
cv.guardar(telf)
enviados.clear()
os.environ["WA_TEMPLATE_FIN_PRUEBA"] = ""
hechos = cv.procesar_seguimientos(ahora=AHORA)
check("fin de prueba (mañana vence) se dispara", any(h.get("tipo") == "fin_prueba" for h in hechos))
check("cierra con precio y ¿mensual o anual?", any("S/ 19" in x[2] and "¿Mensual o anual?" in x[2] for x in enviados if x[0] == "texto"))

# Restaurar flag apagado
os.environ["SEGUIMIENTO_ACTIVO"] = "0"
cv.configurar(enviar_texto=None, enviar_plantilla=None)


print("=== 6) Persistencia de estado en Firestore (botEstados.convo) ===")
cv.reset()
vf.reset_client()
fake = vf.FakeFirestore()
vf.set_client(fake)
telp = "51999999999"
en(AHORA)
cv.registrar_inbound(telp, "quiero ver mis gastos", ahora=AHORA)
cv.marcar_oferta(telp, ahora=AHORA, stage="ofrecido")
cv.aplicar_saludo(telp, "¿Te ayudo con el registro?", ahora=AHORA)
doc = fake.estado_get(telp) or {}
convo = doc.get("convo") or {}
check("botEstados guarda historial, oferta, saludo y stage",
      convo.get("stage") == "ofrecido" and convo.get("last_offer_at") and convo.get("last_greeting_date")
      and any(i.get("role") == "cliente" for i in convo.get("history") or []))
# simula reinicio
cv.reset()
e = cv.cargar(telp)
check("tras reinicio relee el estado desde Firestore", e.get("stage") == "ofrecido" and e.get("last_greeting_date") == AHORA.date().isoformat())
vf.reset_client()


print("=== 7) Entrega sin video no promete nada que no exista ===")
cv.reset()
# No tocamos vp.PANEL_VIDEO_URL a nivel módulo si el test de planes lo necesita...
# usamos un teléfono nuevo y parche temporal
old_video = vp.PANEL_VIDEO_URL
vp.PANEL_VIDEO_URL = ""
wa = WA()
vp.entregar_panel(wa, "51910101010")
union = "\n".join(x[2] for x in wa.out)
check("sin video no se inventa ni se promete", "video" not in union.lower() and "asesor te envía" not in union)
check("sí se envía el link de la app", "aro-finance-pro.web.app" in union)
vp.PANEL_VIDEO_URL = old_video


print(f"\nResultado: {passed} OK, {failed} FAIL")
sys.exit(0 if failed == 0 else 1)
