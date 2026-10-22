import sqlite3
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
-- Un registro por tarea hecha. Los puntos se guardan acá, no se recalculan
-- desde la config: editar tareas.yaml jamás cambia puntos ya ganados.
CREATE TABLE IF NOT EXISTS completadas (
    chica TEXT NOT NULL, dia TEXT NOT NULL, tarea TEXT NOT NULL,
    puntos INTEGER NOT NULL, creado TEXT NOT NULL,
    PRIMARY KEY (chica, dia, tarea)
);
-- Bonus por día completo (como mucho uno por chica y día).
CREATE TABLE IF NOT EXISTS bonus (
    chica TEXT NOT NULL, dia TEXT NOT NULL, puntos INTEGER NOT NULL,
    PRIMARY KEY (chica, dia)
);
-- Tareas agregadas desde la vista de padres. `desde`/`hasta` acotan los días en que
-- aplican, así agregar o quitar una tarea nunca cambia el pasado.
CREATE TABLE IF NOT EXISTS tareas_extra (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chica TEXT NOT NULL, texto TEXT NOT NULL,
    dias TEXT NOT NULL, desde TEXT NOT NULL, hasta TEXT
);
-- Bitácora de todo lo que se marcó o desmarcó (solo se agrega, nunca se borra).
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, cuando TEXT NOT NULL,
    chica TEXT NOT NULL, dia TEXT NOT NULL, tarea TEXT NOT NULL,
    accion TEXT NOT NULL, quien TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript(SCHEMA)
    return conn


def get_meta(conn, clave: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT valor FROM meta WHERE clave=?", (clave,)).fetchone()
    return row["valor"] if row else default


def set_meta(conn, clave: str, valor: str) -> None:
    conn.execute(
        "INSERT INTO meta(clave, valor) VALUES(?, ?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
        (clave, valor),
    )


def iso(d: date) -> str:
    return d.isoformat()
