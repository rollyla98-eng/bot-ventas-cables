import os
import requests
from fastapi import FastAPI, Request, Response, Query
import google.generativeai as genai

app = FastAPI()

# Credenciales oficiales
WHATSAPP_TOKEN = "EAAOqcj76RogBSeVSaZAjmFGIgZB6mab6VezmNdFNkFV4rfEbmiJmRZBG78gRddIbLkGWYt48OZCVvtlW4ZCzZBiimYZC9O6Ht8HrZAratZAZBz2pdWxhJaXQjttwURMM9lUtKNFA1pNeQTVOiP0hgEY5k5HbQOB4zRsabaoyuikhh3e1WgSHoRbWYx3R99tmZCaPawPcIQQja8XPFr0FNALLfXLY7ZAi4bKR7USJ6MZB0FBfZBTyo2rQsazuE80nQJKyrR7K5AowBkDxLqkA7ASe2vvHj0Jad8"
PHONE_NUMBER_ID = "1305556822640751"
VERIFY_TOKEN = "aro_secreto_2026"
GEMINI_API_KEY = "AIzaSyAu33CgDU5FRmbHMbX_QAiToFu5kCUI5xg"

# Configuración del modelo Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction=(
        "Eres un asesor de ventas experto en cables de carga rápida, accesorios tecnológicos y cargadores LDNIO. "
        "Responde de forma concisa, amable, directa y orientada a cerrar la venta por WhatsApp. "
        "Resuelve dudas sobre compatibilidad (Tipo C, Lightning, USB normal), potencia en watts y disponibilidad."
    )
)

@app.get("/")
def home():
    return {"status": "Servidor activo y bot funcionando 24/7"}

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token")
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        print("Webhook verificado exitosamente por Meta.")
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(content="Token inválido", status_code=403)

@app.post("/webhook")
async def receive_message(request: Request):
    data = await request.json()
    try:
        entries = data.get("entry", [])
        if not entries:
            return {"status": "no entry"}

        changes = entries[0].get("changes", [])
        if not changes:
            return {"status": "no changes"}

        value = changes[0].get("value", {})
        
        # Procesa únicamente si es un mensaje de usuario (ignora estados de lectura/entrega)
        if "messages" in value and len(value["messages"]) > 0:
            message_obj = value["messages"][0]
            user_phone = message_obj.get("from")
            
            if message_obj.get("type") == "text":
                user_text = message_obj["text"]["body"]
                print(f"Mensaje recibido de {user_phone}: {user_text}")
                
                # Generar respuesta con Gemini
                ai_response = model.generate_content(user_text)
                reply_text = ai_response.text.strip()
                
                # Enviar respuesta al cliente por WhatsApp
                send_whatsapp_message(user_phone, reply_text)
                print(f"Respuesta enviada a {user_phone}: {reply_text}")
                
    except Exception as e:
        print(f"Error procesando mensaje: {e}")

    return {"status": "ok"}

def send_whatsapp_message(to: str, message: str):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    response = requests.post(url, json=payload, headers=headers)
    print(f"Meta Graph API status: {response.status_code}")
