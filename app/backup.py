"""Copias de seguridad de la base. Usa la API de backup de SQLite, que es
segura aunque alguien esté marcando una tarea en ese mismo momento."""
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path


def hacer_backup(db_path: str, dir_backups: str, conservar: int = 30) -> Path:
    destino_dir = Path(dir_backups)
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"chores-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    origen = sqlite3.connect(db_path)
    copia = sqlite3.connect(destino)
    try:
        origen.backup(copia)
    finally:
        copia.close()
        origen.close()
    verificar = sqlite3.connect(destino)
    try:
        if verificar.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            destino.unlink(missing_ok=True)
            raise RuntimeError("El backup salió corrupto")
    finally:
        verificar.close()
    viejos = sorted(destino_dir.glob("chores-*.sqlite3"))[:-conservar]
    for v in viejos:
        v.unlink(missing_ok=True)
    return destino


def iniciar_backups_automaticos(db_path: str, dir_backups: str, cada_horas: float = 24, conservar: int = 30):
    """Un backup al arrancar y otro cada `cada_horas`."""
    def loop():
        while True:
            try:
                hacer_backup(db_path, dir_backups, conservar)
            except Exception as e:  # nunca tumbar la app por un backup
                print(f"[backup] falló: {e}", flush=True)
            time.sleep(cada_horas * 3600)

    t = threading.Thread(target=loop, name="backups", daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    import os
    d = os.environ.get("CHORES_DATA", "data")
    print(hacer_backup(f"{d}/chores.sqlite3", f"{d}/backups"))
