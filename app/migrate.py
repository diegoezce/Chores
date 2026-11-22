"""
Migración única: familia actual (tareas.yaml) → tablas v2.

Uso:
    python -m app.migrate [--db data/chores.sqlite3] [--config tareas.yaml] [--dry-run]

Idempotente: si ya existe familia_id=1 no hace nada.
"""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import db
from .config import DIAS, cargar, slug

TZ = ZoneInfo(os.environ.get("TZ", "America/Argentina/Buenos_Aires"))


def ahora() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def migrar(conn, cfg, dry_run: bool) -> None:
    ya_migrado = conn.execute("SELECT id FROM familias WHERE id=1").fetchone()
    if ya_migrado:
        print("Familia 1 ya existe — nada que hacer.")
        return

    if dry_run:
        print("[dry-run] Se crearían:")

    # 1. Familia
    familia = {
        "nombre": "Mi familia",
        "timezone": os.environ.get("TZ", "America/Argentina/Buenos_Aires"),
        "idioma": "es",
        "puntos_tarea": cfg.puntos_tarea,
        "puntos_dia": cfg.puntos_dia,
        "minimo_premio_pct": cfg.minimo_premio,
        "creado": ahora(),
    }
    if dry_run:
        print(f"  familia: {familia}")
    else:
        conn.execute(
            "INSERT INTO familias(id,nombre,timezone,idioma,puntos_tarea,puntos_dia,minimo_premio_pct,creado) "
            "VALUES(1,?,?,?,?,?,?,?)",
            (familia["nombre"], familia["timezone"], familia["idioma"],
             familia["puntos_tarea"], familia["puntos_dia"], familia["minimo_premio_pct"], familia["creado"]),
        )

    # 2. Hijos (en orden para IDs reproducibles)
    chica_a_hijo: dict[str, int] = {}
    for i, (cid, c) in enumerate(cfg.chicas.items(), start=1):
        emoji = db.get_meta(conn, f"emoji:{cid}") or c.emoji
        if dry_run:
            print(f"  hijo {i}: {c.nombre} ({cid})")
        else:
            conn.execute(
                "INSERT INTO hijos(familia_id,nombre,emoji,color,fondo,creado) VALUES(1,?,?,?,?,?)",
                (c.nombre, emoji, c.color, c.fondo, ahora()),
            )
            hijo_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            chica_a_hijo[cid] = hijo_id

    if dry_run:
        return

    # 3. tareas_def: todas las tareas únicas de la semana + tareas_extra vigentes
    slug_a_id: dict[str, int] = {}

    def _upsert_tarea(texto: str) -> int:
        s = slug(texto)
        if s in slug_a_id:
            return slug_a_id[s]
        conn.execute(
            "INSERT OR IGNORE INTO tareas_def(familia_id,texto,slug) VALUES(1,?,?)",
            (texto, s),
        )
        tid = conn.execute("SELECT id FROM tareas_def WHERE familia_id=1 AND slug=?", (s,)).fetchone()[0]
        slug_a_id[s] = tid
        return tid

    for dia_nombre in DIAS:
        for cid, tareas in cfg._semana[dia_nombre].items():
            for t in tareas:
                _upsert_tarea(t.texto)

    for extra in cfg.extras:
        _upsert_tarea(extra.tarea.texto)

    # 4. Asignaciones desde la semana (agrupadas por hijo+tarea para compactar días)
    # Estructura: (hijo_id, tarea_id) → set de días (0=lunes)
    asig: dict[tuple[int, int], set[int]] = {}
    for dia_idx, dia_nombre in enumerate(DIAS):
        for cid, tareas in cfg._semana[dia_nombre].items():
            hijo_id = chica_a_hijo[cid]
            for t in tareas:
                tarea_id = slug_a_id[slug(t.texto)]
                asig.setdefault((hijo_id, tarea_id), set()).add(dia_idx)

    hoy_iso = db.iso(datetime.now(TZ).date())
    for (hijo_id, tarea_id), dias in asig.items():
        conn.execute(
            "INSERT INTO asignaciones(hijo_id,tarea_id,dias,desde) VALUES(?,?,?,?)",
            (hijo_id, tarea_id, ",".join(map(str, sorted(dias))), hoy_iso),
        )

    # Tareas extra con sus fechas originales
    for extra in cfg.extras:
        hijo_id = chica_a_hijo[extra.chica]
        tarea_id = slug_a_id[slug(extra.tarea.texto)]
        conn.execute(
            "INSERT INTO asignaciones(hijo_id,tarea_id,dias,desde,hasta) VALUES(?,?,?,?,?)",
            (hijo_id, tarea_id, ",".join(map(str, sorted(extra.dias))),
             db.iso(extra.desde), db.iso(extra.hasta) if extra.hasta else None),
        )

    # 5. Copiar completadas → completadas2
    rows = conn.execute("SELECT * FROM completadas").fetchall()
    for r in rows:
        hijo_id = chica_a_hijo.get(r["chica"])
        if hijo_id:
            conn.execute(
                "INSERT OR IGNORE INTO completadas2(hijo_id,dia,tarea,puntos,creado) VALUES(?,?,?,?,?)",
                (hijo_id, r["dia"], r["tarea"], r["puntos"], r["creado"]),
            )

    # 6. Copiar bonus → bonus2
    rows = conn.execute("SELECT * FROM bonus").fetchall()
    for r in rows:
        hijo_id = chica_a_hijo.get(r["chica"])
        if hijo_id:
            conn.execute(
                "INSERT OR IGNORE INTO bonus2(hijo_id,dia,puntos) VALUES(?,?,?)",
                (hijo_id, r["dia"], r["puntos"]),
            )

    # 7. Copiar eventos → eventos2
    rows = conn.execute("SELECT * FROM eventos").fetchall()
    for r in rows:
        hijo_id = chica_a_hijo.get(r["chica"])
        if hijo_id:
            conn.execute(
                "INSERT INTO eventos2(cuando,hijo_id,dia,tarea,accion,quien) VALUES(?,?,?,?,?,?)",
                (r["cuando"], hijo_id, r["dia"], r["tarea"], r["accion"], r["quien"]),
            )

    print(f"Migración completa: {len(cfg.chicas)} hijos, "
          f"{len(slug_a_id)} tareas, "
          f"{len(rows)} eventos copiados.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get("CHORES_DATA", "data") + "/chores.sqlite3")
    parser.add_argument("--config", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"Error: no se encuentra la base de datos '{args.db}'", file=sys.stderr)
        sys.exit(1)

    cfg = cargar(args.config)
    conn = db.connect(args.db)
    cfg.cargar_extras(conn)
    try:
        migrar(conn, cfg, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
