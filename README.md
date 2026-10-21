# Chores · Tareas de la casa

App sencilla para que las chicas vean las tareas del día, las marquen como hechas,
sumen puntos y mantengan su racha. Hay una vista de padres con resumen semanal.

- **Chicas:** entran, tocan su nombre y listo (sin usuarios ni contraseñas). Pueden
  volver hasta 14 días atrás. Cada tarea suma 10 puntos; completar todas las del día
  suma 20 más. Fuegos artificiales y sonido al completar.
- **Padres:** `/padres` con PIN. Resumen semanal, corregir cualquier día, historial,
  backup manual y exportación.
- **Domingo:** libre, no hay tareas.

## Reglas de puntos
- El total es la suma de lo guardado, no un contador: no puede desfasarse.
- Marcar/desmarcar es idempotente: un doble toque o un reintento no suma ni resta de más.
- Desmarcar una tarea resta sus puntos; si se había ganado el bonus del día, se retira
  (y vuelve si la marca de nuevo).
- Editar `tareas.yaml` **no** cambia puntos ya ganados.
- Racha: días seguidos con todas las tareas hechas. Los días sin tareas no la cortan y
  el día de hoy no la corta mientras siga en curso.
- Todo movimiento queda en una bitácora que no se borra.

## Correr en Docker (mini PC)
```bash
cp .env.example .env     # editá PARENT_PIN
docker compose up -d --build
```
Queda en `http://<ip-de-la-mini-pc>:8000`. Para verlo desde afuera, sumá un túnel de
Cloudflare apuntando a `http://localhost:8000`. Cambiar tareas: editá `tareas.yaml` y
`docker compose restart`.

## Backups
- Automáticos: uno al arrancar y uno cada 24 h en `data/backups/` (últimos 30, cada uno
  verificado con `PRAGMA integrity_check`).
- Manual: botón en `/padres`, o `docker compose exec chores python -m app.backup`.
- **Recomendado:** sincronizá `data/backups/` a otro lugar (nube/otro disco): un backup en
  el mismo disco no te salva si el disco falla.
- Restaurar: parar el contenedor y copiar un `chores-*.sqlite3` sobre `data/chores.sqlite3`
  (borrando antes `chores.sqlite3-wal` y `-shm` si existen).

## Desarrollo
```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
CHORES_DATA=./data PARENT_PIN=1234 uvicorn app.main:app --reload
```
