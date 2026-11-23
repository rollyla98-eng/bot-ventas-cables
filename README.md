# bot-ventas-cables

Bot de ventas por WhatsApp (FastAPI + WhatsApp Cloud API + Gemini) desplegado en Render.
Todas las claves van en variables de entorno de Render. **Nunca** las escribas en el código.

## Panel Financiero: plan Gratis con límites y plan Pro sin límites

1. El asesor (Gemini) responde dudas con `panel_financiero.md` (copia para clientes de `VERDAD_UNICA_ARO.md`).
   Si defines `PANEL_INFO_URL` (texto público), se usa esa fuente en lugar del archivo.
2. **Planes** (el bot los explica con los valores de env):
   - **Gratis, para siempre:** hasta `PLAN_GRATIS_MOVIMIENTOS` (30) movimientos al mes, hasta `PLAN_GRATIS_FIADOS` (3)
     fiados activos, solo el resumen del mes actual, sin exportar a CSV/Excel y sin Asesor IA.
   - **Pro:** `PRO_PRECIO_MENSUAL` (S/ 19) al mes o `PRO_PRECIO_ANUAL` (S/ 149) al año: movimientos y fiados ilimitados,
     historial completo, reportes, exportación y Asesor IA.
   - Los límites viven en la plantilla de Google Sheets; el bot **solo los comunica**, no los aplica.
3. **Entrega del Panel (sin cobrar):** el bot envía `PANEL_COPY_URL` (enlace /copy) y `PANEL_VIDEO_URL`. En ese momento
   se crea la cuenta y empiezan `PRO_PRUEBA_DIAS` (15) días de Pro gratis (una vez por número); después pasa a Gratis
   sin perder datos. Si faltan las URLs, le dice que un asesor se lo envía y avisa a `NOTIFICAR_A`.
4. **Asesor IA:** solo con Pro vigente. En Gratis el bot sigue respondiendo dudas del Panel y de venta, pero si pide
   Asesor IA o funciones Pro le ofrece Pro y le manda el QR. El corte lo hace el código (marcador `[[ASESOR_IA]]`).
5. **Pago:** QR `PAGO_QR_URL` o `PAGO_QR_MEDIA_ID` (Plin/Yape a nombre de `PAGO_TITULAR`). Gemini Vision lee el
   comprobante y el código exige: monto exacto `PRO_PRECIO_MENSUAL` (→ `pro_mensual`, +`PRO_DIAS_MENSUAL`=30 días) o
   `PRO_PRECIO_ANUAL` (→ `pro_anual`, +`PRO_DIAS_ANUAL`=365 días), fecha de hoy (America/Lima), hora dentro de
   `PAGO_VENTANA_MIN`, destinatario con `PAGO_DESTINATARIO_CLAVES` y confianza ≥ 0.8. Los días se cuentan desde hoy o
   desde el vencimiento si Pro sigue vigente. Evita activaciones dobles por n° de operación o hash de la imagen.
6. **Hoja** `SHEET_INGRESOS_ID`, rango `SHEET_INGRESOS_RANGO` (por defecto `Ingresos!A:M`), con la cuenta de servicio de
   `GOOGLE_SERVICE_ACCOUNT_JSON` (JSON o base64; comparte la hoja con su correo como Editor). Columnas:
   A fecha · B producto · C monto · D método · E teléfono · F destinatario · G n° operación · H fecha-hora del comprobante ·
   I clave · J estado · **K plan (`prueba_pro`/`gratis`/`pro_mensual`/`pro_anual`) · L inicio de prueba · M vencimiento**.
   El plan de cada cliente se relee de la hoja tras un reinicio de Render; sin hoja queda solo en memoria.

Marcadores que emite Gemini (el código los quita del texto): `[[ENVIAR_PANEL]]`, `[[ENVIAR_PAGO_PRO]]` y `[[ASESOR_IA]]`.

Todos los envíos son respuestas al mensaje del cliente, dentro de la ventana de 24 h de WhatsApp.
Los avisos a `NOTIFICAR_A` solo llegan si ese número escribió al bot en las últimas 24 h.

Modelo: `GEMINI_MODEL` (por defecto `gemini-3.5-flash-lite`, acepta imágenes), respaldo en `GEMINI_MODELS_FALLBACK`.
Para pasar al número real basta con cambiar `PHONE_NUMBER_ID` (y el token) en Render.
