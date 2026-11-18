# bot-ventas-cables

Bot de ventas por WhatsApp (FastAPI + WhatsApp Cloud API + Gemini) desplegado en Render.
Todas las claves van en variables de entorno de Render. **Nunca** las escribas en el código.

## Venta automática del Panel Financiero (S/ 40)

1. El asesor responde dudas con `panel_financiero.md` (copia para clientes de `VERDAD_UNICA_ARO.md`).
   Si defines `PANEL_INFO_URL` (texto público), se usa esa fuente en lugar del archivo.
2. Si el cliente quiere comprar, el bot manda el QR (`PAGO_QR_URL` o `PAGO_QR_MEDIA_ID`) y las
   instrucciones de pago (`PAGO_METODO`, por defecto Plin Interbank a nombre de `PAGO_TITULAR`).
3. Si el cliente manda una imagen, se descarga de WhatsApp y Gemini Vision lee el comprobante.
   El código exige: monto = `PANEL_PRECIO`, fecha de hoy (America/Lima), hora dentro de
   `PAGO_VENTANA_MIN` minutos y destinatario con todas las palabras de `PAGO_DESTINATARIO_CLAVES`.
   Ante cualquier duda no entrega: responde que un asesor revisará y avisa a `NOTIFICAR_A`.
4. Si es válido: envía `PANEL_COPY_URL` (enlace /copy de Google Sheets) y `PANEL_VIDEO_URL`,
   registra la venta en la hoja `SHEET_INGRESOS_ID` (rango `SHEET_INGRESOS_RANGO`) con la cuenta de
   servicio de `GOOGLE_SERVICE_ACCOUNT_JSON` (JSON o base64; comparte la hoja con el correo de la
   cuenta de servicio como Editor). Evita entregas dobles por número de operación o hash de la imagen
   (en memoria y en la hoja).

Todos los envíos son respuestas al mensaje del cliente, dentro de la ventana de 24 h de WhatsApp.
Los avisos a `NOTIFICAR_A` solo llegan si ese número escribió al bot en las últimas 24 h.

Modelo: `GEMINI_MODEL` (por defecto `gemini-3.5-flash-lite`, acepta imágenes), respaldo en `GEMINI_MODELS_FALLBACK`.
Para pasar al número real basta con cambiar `PHONE_NUMBER_ID` (y el token) en Render.
