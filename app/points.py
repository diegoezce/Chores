"""Toda la lógica de puntos y rachas. Sin HTTP, para poder testearla a fondo.

Reglas:
- Cada tarea hecha suma `puntos_tarea`. Desmarcarla los resta (el registro se borra).
- Completar TODAS las tareas de un día suma además `puntos_dia` (una vez por día).
  Si después desmarca una, el bonus se retira; si la vuelve a marcar, vuelve.
- El total es siempre la suma de lo guardado: no hay contador que pueda desfasarse.
- Marcar/desmarcar es idempotente (se manda el estado deseado, no un "toggle"),
  así un doble toque o un reintento por mala señal no cambia nada.
"""
from datetime import date, datetime, timedelta, timezone

from . import db
from .config import Config


class ReglaError(Exception):
    def __init__(self, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


def _hechas(conn, chica: str, dia: date) -> set[str]:
    rows = conn.execute("SELECT tarea FROM completadas WHERE chica=? AND dia=?", (chica, db.iso(dia)))
    return {r["tarea"] for r in rows}


def dia_completo(cfg: Config, conn, chica: str, dia: date) -> bool:
    tareas = cfg.tareas(chica, dia)
    return bool(tareas) and {t.id for t in tareas} <= _hechas(conn, chica, dia)


def marcar(conn, cfg: Config, chica: str, dia: date, tarea_id: str, hecho: bool,
           quien: str, hoy: date, max_dias_atras: int | None) -> dict:
    """Fija el estado de una tarea. Devuelve qué cambió realmente."""
    if chica not in cfg.chicas:
        raise ReglaError("chica_desconocida", "Esa chica no existe")
    if dia > hoy:
        raise ReglaError("futuro", "Todavía no podés marcar días que no llegaron")
    if max_dias_atras is not None and (hoy - dia).days > max_dias_atras:
        raise ReglaError("muy_viejo", "Ese día ya quedó muy atrás. Pedile ayuda a mamá o papá")
    ids_del_dia = {t.id for t in cfg.tareas(chica, dia)}
    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    d = db.iso(dia)

    conn.execute("BEGIN IMMEDIATE")
    try:
        existe = conn.execute(
            "SELECT 1 FROM completadas WHERE chica=? AND dia=? AND tarea=?", (chica, d, tarea_id)
        ).fetchone() is not None
        if hecho and tarea_id not in ids_del_dia:
            raise ReglaError("tarea_invalida", "Esa tarea no está en la lista de ese día")
        if not hecho and not existe and tarea_id not in ids_del_dia:
            raise ReglaError("tarea_invalida", "Esa tarea no está en la lista de ese día")

        cambio = False
        if hecho and not existe:
            conn.execute(
                "INSERT INTO completadas(chica, dia, tarea, puntos, creado) VALUES(?,?,?,?,?)",
                (chica, d, tarea_id, cfg.puntos_tarea, ahora),
            )
            cambio = True
        elif not hecho and existe:
            conn.execute("DELETE FROM completadas WHERE chica=? AND dia=? AND tarea=?", (chica, d, tarea_id))
            cambio = True

        bonus_antes = conn.execute("SELECT 1 FROM bonus WHERE chica=? AND dia=?", (chica, d)).fetchone() is not None
        if cambio:
            completo = dia_completo(cfg, conn, chica, dia)
            if completo and not bonus_antes:
                conn.execute("INSERT INTO bonus(chica, dia, puntos) VALUES(?,?,?)", (chica, d, cfg.puntos_dia))
            elif not completo and bonus_antes:
                conn.execute("DELETE FROM bonus WHERE chica=? AND dia=?", (chica, d))
            conn.execute(
                "INSERT INTO eventos(cuando, chica, dia, tarea, accion, quien) VALUES(?,?,?,?,?,?)",
                (ahora, chica, d, tarea_id, "marcar" if hecho else "desmarcar", quien),
            )
            inicio = db.get_meta(conn, "inicio")
            if inicio is None or d < inicio:
                db.set_meta(conn, "inicio", d)
        bonus_despues = conn.execute("SELECT 1 FROM bonus WHERE chica=? AND dia=?", (chica, d)).fetchone() is not None
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {
        "cambio": cambio,
        "bonus_ganado": bonus_despues and not bonus_antes,
        "bonus_perdido": bonus_antes and not bonus_despues,
    }


def total(conn, chica: str) -> int:
    a = conn.execute("SELECT COALESCE(SUM(puntos),0) s FROM completadas WHERE chica=?", (chica,)).fetchone()["s"]
    b = conn.execute("SELECT COALESCE(SUM(puntos),0) s FROM bonus WHERE chica=?", (chica,)).fetchone()["s"]
    return a + b


def puntos_del_dia(conn, chica: str, dia: date) -> int:
    d = db.iso(dia)
    a = conn.execute("SELECT COALESCE(SUM(puntos),0) s FROM completadas WHERE chica=? AND dia=?", (chica, d)).fetchone()["s"]
    b = conn.execute("SELECT COALESCE(SUM(puntos),0) s FROM bonus WHERE chica=? AND dia=?", (chica, d)).fetchone()["s"]
    return a + b


def puntos_desde(conn, chica: str, desde: date) -> int:
    d = db.iso(desde)
    a = conn.execute("SELECT COALESCE(SUM(puntos),0) s FROM completadas WHERE chica=? AND dia>=?", (chica, d)).fetchone()["s"]
    b = conn.execute("SELECT COALESCE(SUM(puntos),0) s FROM bonus WHERE chica=? AND dia>=?", (chica, d)).fetchone()["s"]
    return a + b


def _dias_completos(conn, chica: str) -> set[str]:
    return {r["dia"] for r in conn.execute("SELECT dia FROM bonus WHERE chica=?", (chica,))}


def racha(conn, cfg: Config, chica: str, hoy: date) -> dict:
    """Días seguidos con todas las tareas hechas. Los días sin tareas (domingo) no
    cortan la racha, y el día de hoy no la corta mientras siga en curso."""
    completos = _dias_completos(conn, chica)
    inicio = db.get_meta(conn, "inicio")
    inicio_d = date.fromisoformat(inicio) if inicio else hoy

    actual, d = 0, hoy
    while d >= inicio_d:
        if db.iso(d) in completos:
            actual += 1
        elif d != hoy and cfg.tareas(chica, d):
            break
        d -= timedelta(days=1)

    mejor = corrida = 0
    d = inicio_d
    while d <= hoy:
        if db.iso(d) in completos:
            corrida += 1
            mejor = max(mejor, corrida)
        elif cfg.tareas(chica, d) and d != hoy:
            corrida = 0
        d += timedelta(days=1)
    return {"actual": actual, "mejor": max(mejor, actual)}
