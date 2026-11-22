# Plan: app nativa iOS y producto multi-familia

Objetivo: publicar la app en el App Store para cualquier familia (registro, cuenta de
padres, administración de hijos y tareas). Hoy la app es de una sola familia: PIN global,
tareas en `tareas.yaml`, tres chicas fijas y zona horaria fija (Buenos Aires).

Se mantienen las reglas de `CLAUDE.md`: la lógica de puntos y rachas vive en
`app/points.py`, los puntos se guardan al ganarse (nunca se recalcula el pasado), y toda
fecha pasa por un único punto de acceso (`hoy()`), que pasará a depender de la familia.

## 1. Backend: de una familia a multi-familia

**Modelo de datos nuevo**
- `familias`: zona horaria, idioma, premio mensual, puntos por tarea y por día completo
  (hoy globales en el yaml).
- `usuarios`: adultos con login, vinculados a una familia (puede haber más de uno).
- `hijos`: nombre, emoji, color, vinculados a una familia (ya no hay `pili`/`numi`/`vicky`).
- `tareas` y `asignaciones`: qué tarea le toca a qué hijo y qué días. Los "paquetes A/B/C"
  rotativos pasan a ser una plantilla opcional, no la estructura base.
- `completadas`, `bonus`, `eventos`: igual que hoy, con `familia_id`.

**Sale de la config global:** `tareas.yaml`, `PARENT_PIN`, `hoy()` con zona fija.

**Migración:** la familia actual pasa a ser la familia nº 1 con sus datos intactos;
`tareas.yaml` se importa una sola vez.

**Infraestructura:** la mini PC con túnel no sirve para un producto público. Hosting
administrado, Postgres en vez de SQLite, backups automáticos, rate limiting y logs.

## 2. Cuentas y autenticación

- **Padres:** Sign in with Apple como método principal (obligatorio si hay otros logins
  sociales). Opcional: email + contraseña para web/Android.
- **Sesiones:** access token + refresh token en Keychain (reemplaza la cookie con PIN).
- **Hijos sin cuenta propia** (no se registran ni dan email). Dos modos:
  - En el dispositivo del padre, con selector de perfil (como hoy).
  - En su propio dispositivo, vinculado con código o QR generado por el padre; el token
    solo permite marcar tareas del hijo vinculado.
- **PIN de modo padres:** protección local dentro de la app para que los chicos no cambien
  tareas ni puntos.

## 3. Requisitos de App Store que condicionan el diseño

- **Borrado de cuenta dentro de la app** (guideline 5.1.1(v)): elimina la familia y sus datos.
- **Datos de menores:** solo nombre o apodo y emoji; sin fotos, sin analítica de terceros,
  sin publicidad. Política de privacidad pública, URL de soporte, etiquetas de privacidad.
  GDPR (exportar y borrar datos) si hay usuarios en la UE.
- **Kids Category:** no apuntar a ella; la app está dirigida a padres.
- **Cuenta demo** y notas para el revisor de Apple.
- **Monetización (si aplica):** StoreKit 2, suscripción o compra única; validación de
  recibos en el backend.
- **Localización:** español e inglés desde el inicio.

## 4. App iOS (SwiftUI, iOS 17+, URLSession + async/await, sin dependencias)

- **Onboarding:** Sign in with Apple → crear familia (nombre y zona horaria) → agregar
  hijos → elegir plantillas de tareas o crearlas.
- **Padres:** administrar hijos y tareas, calendario por hijo, resumen y premio, ajustes,
  borrado de cuenta.
- **Hijos:** día con tareas, puntos, racha, tira semanal, hasta 14 días atrás, fuegos
  artificiales y haptics, selector de emoji.
- **Después del MVP:** notificaciones locales, widget (racha y progreso de hoy), modo
  offline con cola de marcas pendientes (cuidando idempotencia y límites de días).

## 5. Fases

| Fase | Contenido |
|---|---|
| 0 | Decisiones abiertas y contrato de API (OpenAPI) |
| 1 | Backend multi-familia con migración, auth con tokens, CRUD de hijos y tareas, tests (la lógica de `points.py` se mantiene casi igual) |
| 2 | Hosting y Postgres; migrar la familia actual al servicio nuevo; la web actual sigue funcionando |
| 3 | App iOS MVP en TestFlight, primero para la propia familia |
| 4 | Pulido para App Store: borrado de cuenta, privacidad, localización, monetización |
| 5 | Notificaciones, widget, modo offline |

## 6. Decisiones

1. **Uso por los hijos:** ambos — dispositivo del padre (selector de perfil) y dispositivo
   propio vinculado con QR/código generado por el padre.
2. **Monetización:** pago único.
3. **Hosting:** mini PC con túnel para fases 1–3 (igual que hoy). En fase 4 (App Store
   público) se migra a Fly.io: 1 máquina shared (1 core, 256 MB RAM), SQLite sobre volumen
   persistente, backups continuos con Litestream a Backblaze B2, ~$3.70/mes. El setup de
   Fly.io se documenta y prepara antes de la migración para que sea un corte limpio.
4. **Mercado e idiomas:** Argentina / español en la v1. Inglés y mercado global en versión
   posterior.

## Notas

- El código Swift se puede escribir desde el entorno Linux, pero compilar, probar en
  simulador y subir a TestFlight requiere Xcode en un Mac.
- Cambios chicos sugeridos en la API actual: formato de error estable, `/api/version` con
  versión mínima del cliente, y cliente Swift generado desde `/openapi.json`.
