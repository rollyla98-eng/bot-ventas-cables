# ARO Tech API — sitio público

Sitio estático en español para la aplicación de TikTok for Developers **ARO Tech API**.

ARO (Rolly Arnulfo López Aro) es un pequeño negocio peruano. Esta aplicación solo envía videos propios de ARO como borradores al buzón de TikTok de sus cuentas (@aro.control, @aro_.oficial y @arotech_oficial). El dueño publica desde su celular.

## Páginas

- `/` — qué hace la aplicación
- `/terms` — Términos de servicio
- `/privacy` — Política de privacidad
- `/callback` — Redirect URI de OAuth (muestra `code`, `state`, `error` y `error_description`)

No hay scripts de seguimiento ni código de servidor.

## Despliegue

Publicar como Static Site en Render:

- Build command: ninguno (o `true`)
- Publish directory: `public`

La Redirect URI de TikTok debe ser exactamente:

`https://<servicio>.onrender.com/callback`
