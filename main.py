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

# Configuración de Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction=(
        "Eres un asesor de ventas experto en cables de carga rápida y accesorios tecnológicos LDNIO. "
        "Responde de forma concisa, amable y orientada a cerrar la venta por WhatsApp."
    )
)

@app.get("/")
def home():
    return {"status": "Servidor activo"}

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token")
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(content="Token inválido", status_code=403)

@app.post("/webhook")
async def receive_message(request: Request):
    data = await request.json()
    try:
        entries = data.get("entry", [])
        if entries:
            changes = entries[0].get("changes", [])
            if changes:
                value = changes[0].get("value", {})
                if "messages" in value and len(value["messages"]) > 0:
                    message_obj = value["messages"][0]
                    user_phone = message_obj.get("from")
                    if message_obj.get("type") == "text":
                        user_text = message_obj["text"]["body"]
                        ai_response = model.generate_content(user_text)
                        send_whatsapp_message(user_phone, ai_response.text.strip())
    except Exception as e:
        print(f"Error: {e}")

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
    requests.post(url, json=payload, headers=headers)
