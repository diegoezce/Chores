# CLAUDE.md

App de tareas del hogar para tres chicas (FastAPI + SQLite + HTML/JS plano, sin build).

- Lógica de puntos y rachas: `app/points.py` (sin HTTP, testeada a fondo). Cambios a las
  reglas van con tests en `tests/test_points.py`.
- Tareas: `tareas.yaml`. Los puntos se guardan al ganarse; nunca recalcular el pasado
  desde la config.
- Fechas: siempre `app.main.hoy()` (America/Argentina/Buenos_Aires), nunca `date.today()`.
- Tests: `pytest`. Commit y push directo a `main` tras cada cambio.
