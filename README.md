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
   - Los límites los aplica el Panel (la app); el bot **solo los comunica**, no los aplica.
3. **Entrega del Panel (sin cobrar):** el bot envía el enlace de la app (`PANEL_APP_URL`, o `PANEL_COPY_URL`, o
   por defecto `https://aro-finance-pro.web.app`). El video (`PANEL_VIDEO_URL`) solo se envía si está configurado;
   nunca se promete un envío a mano. Al entregarlo empiezan `PRO_PRUEBA_DIAS` (15) días de Pro gratis (una vez por
   número); después pasa a Gratis sin perder datos.
4. **Asesor IA:** solo con Pro vigente. En Gratis el bot sigue respondiendo dudas del Panel y de venta, pero si pide
   Asesor IA o funciones Pro le ofrece Pro y le manda el QR. El corte lo hace el código (marcador `[[ASESOR_IA]]`).
5. **Pago (dos métodos):**
   - **Plin/Yape con QR:** `PAGO_QR_URL` o `PAGO_QR_MEDIA_ID` (a nombre de `PAGO_TITULAR`). Gemini Vision lee el
     comprobante y el código exige: monto exacto `PRO_PRECIO_MENSUAL` (→ `pro_mensual`, +`PRO_DIAS_MENSUAL`=30 días) o
     `PRO_PRECIO_ANUAL` (→ `pro_anual`, +`PRO_DIAS_ANUAL`=365 días), fecha de hoy (America/Lima), hora dentro de
     `PAGO_VENTANA_MIN`, destinatario con `PAGO_DESTINATARIO_CLAVES` y confianza ≥ 0.8. Evita activaciones dobles por
     n° de operación o hash de la imagen.
   - **Tarjeta con Mercado Pago:** el bot crea una preferencia Checkout Pro (S/ 19 o S/ 149) y envía el link
     (`init_point`). El webhook `POST /mp/webhook` relee el pago en la API y, solo si está `approved`, moneda PEN y
     el monto calza con el plan, activa Pro (mismos días, desde el vencimiento si sigue vigente) y lo guarda en
     Firestore (`pagos.metodo=mercadopago` y el id del pago). No hace falta captura ni revisión de IA.
     Si falta `MP_ACCESS_TOKEN`, el bot ofrece solo el QR. Variables: `MP_ACCESS_TOKEN`, `MP_WEBHOOK_SECRET`
     (opcional) y `PUBLIC_BASE_URL` (por defecto `https://bot-ventas-cables.onrender.com`).
6. **Hoja** `SHEET_INGRESOS_ID`, rango `SHEET_INGRESOS_RANGO` (por defecto `Ingresos!A:M`), con la cuenta de servicio de
   `GOOGLE_SERVICE_ACCOUNT_JSON` (JSON o base64; comparte la hoja con su correo como Editor). Columnas:
   A fecha · B producto · C monto · D método · E teléfono · F destinatario · G n° operación · H fecha-hora del comprobante ·
   I clave · J estado · **K plan (`prueba_pro`/`gratis`/`pro_mensual`/`pro_anual`) · L inicio de prueba · M vencimiento**.
   El plan de cada cliente se relee de la hoja tras un reinicio de Render; sin hoja queda solo en memoria.

Marcadores que emite Gemini (el código los quita del texto): `[[ENVIAR_PANEL]]`, `[[ENVIAR_PAGO_PRO]]`, `[[ENVIAR_LINK_MP]]` y `[[ASESOR_IA]]`.

Todos los envíos son respuestas al mensaje del cliente, dentro de la ventana de 24 h de WhatsApp.
Los avisos a `NOTIFICAR_A` solo llegan si ese número escribió al bot en las últimas 24 h.

Modelo: `GEMINI_MODEL` (por defecto `gemini-3.5-flash-lite`, acepta imágenes), respaldo en `GEMINI_MODELS_FALLBACK`.
Para pasar al número real basta con cambiar `PHONE_NUMBER_ID` (y el token) en Render.

## Persistencia (ARO Finance Pro / Firestore)

Los pagos y las activaciones de prueba Pro se guardan en Firestore del proyecto `aro-finance-pro`
(colección `users/{uid}` con `subscription`, más `pagos` y `botEstados`).
Configura `FIREBASE_PROJECT_ID` y `FIREBASE_SERVICE_ACCOUNT_JSON` (JSON de cuenta de servicio,
sin subirlo al repo). Si faltan, el bot sigue en memoria; la hoja (`SHEET_INGRESOS_ID`) queda
como respaldo opcional.

## Mercado Pago (tarjeta)

1. En [Tus integraciones](https://www.mercadopago.com.pe/developers/panel/app) crea una aplicación (o usa una ya creada).
2. Copia el Access Token de **prueba** (empieza con `TEST-`) y el de **producción** (`APP_USR-`). En Render, pon
   `MP_ACCESS_TOKEN` con Save only. Empieza con el de prueba.
3. En la app, configura el webhook: URL `https://bot-ventas-cables.onrender.com/mp/webhook`, evento **Pagos**.
   Copia la firma secreta a `MP_WEBHOOK_SECRET` (Save only).
4. `PUBLIC_BASE_URL=https://bot-ventas-cables.onrender.com` (o déjala así; es el valor por defecto).

## Conversación y seguimientos

El bot saluda una vez al día (hora de Lima), responde primero lo último que dijo la persona, no ofrece el Panel
en temas de salud o personales, no repite la oferta en 24 h y recorta las respuestas a 2-3 líneas con una sola pregunta.

Los seguimientos (al día siguiente de ofrecer el Panel, y antes de que termine la prueba, cerrando con «¿mensual o anual?»)
están **apagados** por defecto (`SEGUIMIENTO_ACTIVO=0`). Cuando Rolly los active:

1. Pon `SEGUIMIENTO_ACTIVO=1` en Render.
2. Para escribir **fuera** de la ventana de 24 h de WhatsApp hace falta una plantilla aprobada en Meta:
   `WA_TEMPLATE_SEGUIMIENTO` (día siguiente) y `WA_TEMPLATE_FIN_PRUEBA` (precio + «¿mensual o anual?»).
   Si no hay plantilla, ese envío se omite y queda en los logs.
3. Llama a `POST /cron/seguimiento` (por ejemplo un cron de Render) una vez al día. Si defines `CRON_SECRETO`,
   mándalo en el header `x-cron-secret`.

