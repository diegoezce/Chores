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

-- ── v2: multi-familia ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS familias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'America/Argentina/Buenos_Aires',
    idioma TEXT NOT NULL DEFAULT 'es',
    puntos_tarea INTEGER NOT NULL DEFAULT 10,
    puntos_dia INTEGER NOT NULL DEFAULT 20,
    minimo_premio_pct REAL NOT NULL DEFAULT 50,
    creado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    familia_id INTEGER NOT NULL REFERENCES familias(id),
    nombre TEXT NOT NULL,
    email TEXT UNIQUE,
    password_hash TEXT,
    apple_sub TEXT UNIQUE,
    creado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    expira TEXT NOT NULL,
    creado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hijos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    familia_id INTEGER NOT NULL REFERENCES familias(id),
    nombre TEXT NOT NULL,
    emoji TEXT NOT NULL DEFAULT '⭐',
    color TEXT NOT NULL DEFAULT '#333',
    fondo TEXT NOT NULL DEFAULT '#eee',
    creado TEXT NOT NULL
);
-- Token de sesión para el dispositivo propio del hijo (vinculado con QR).
CREATE TABLE IF NOT EXISTS hijo_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hijo_id INTEGER NOT NULL REFERENCES hijos(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    expira TEXT NOT NULL,
    creado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tareas_def (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    familia_id INTEGER NOT NULL REFERENCES familias(id),
    texto TEXT NOT NULL,
    slug TEXT NOT NULL,
    UNIQUE(familia_id, slug)
);
-- Qué tarea le toca a qué hijo qué días. `hasta` NULL = vigente.
CREATE TABLE IF NOT EXISTS asignaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hijo_id INTEGER NOT NULL REFERENCES hijos(id),
    tarea_id INTEGER NOT NULL REFERENCES tareas_def(id),
    dias TEXT NOT NULL,
    desde TEXT NOT NULL,
    hasta TEXT
);
CREATE TABLE IF NOT EXISTS completadas2 (
    hijo_id INTEGER NOT NULL REFERENCES hijos(id),
    dia TEXT NOT NULL,
    tarea TEXT NOT NULL,
    puntos INTEGER NOT NULL,
    creado TEXT NOT NULL,
    PRIMARY KEY (hijo_id, dia, tarea)
);
CREATE TABLE IF NOT EXISTS bonus2 (
    hijo_id INTEGER NOT NULL REFERENCES hijos(id),
    dia TEXT NOT NULL,
    puntos INTEGER NOT NULL,
    PRIMARY KEY (hijo_id, dia)
);
CREATE TABLE IF NOT EXISTS eventos2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cuando TEXT NOT NULL,
    hijo_id INTEGER NOT NULL REFERENCES hijos(id),
    dia TEXT NOT NULL,
    tarea TEXT NOT NULL,
    accion TEXT NOT NULL,
    quien TEXT NOT NULL
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
