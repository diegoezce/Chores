import hashlib
import hmac
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from contextlib import asynccontextmanager

from fastapi import Body, Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, points
from .backup import hacer_backup, iniciar_backups_automaticos
from .config import DIAS_LARGOS, cargar

STATIC = Path(__file__).parent / "static"
TZ = ZoneInfo(os.environ.get("TZ", "America/Argentina/Buenos_Aires"))
DATA = os.environ.get("CHORES_DATA", "data")
DB_PATH = f"{DATA}/chores.sqlite3"
BACKUPS = f"{DATA}/backups"
PIN_PADRES = os.environ.get("PARENT_PIN", "")
DIAS_ATRAS_CHICAS = int(os.environ.get("KIDS_DAYS_BACK", "14"))

cfg = cargar()


def hoy() -> date:
    return datetime.now(TZ).date()


def conn_dep():
    conn = db.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def _secreto() -> bytes:
    conn = db.connect(DB_PATH)
    try:
        s = db.get_meta(conn, "secreto")
        if s is None:
            s = secrets.token_hex(32)
            db.set_meta(conn, "secreto", s)
        return s.encode()
    finally:
        conn.close()


def _token_padres() -> str:
    return hmac.new(_secreto(), f"padres:{PIN_PADRES}".encode(), hashlib.sha256).hexdigest()


def es_padre(padres: str | None) -> bool:
    return bool(PIN_PADRES) and padres is not None and hmac.compare_digest(padres, _token_padres())


def requiere_padre(padres: str | None = Cookie(default=None)):
    if not es_padre(padres):
        raise HTTPException(401, "Necesitás el PIN de padres")


@asynccontextmanager
async def lifespan(_app):
    Path(DATA).mkdir(parents=True, exist_ok=True)
    conn = db.connect(DB_PATH)
    if db.get_meta(conn, "inicio") is None:
        db.set_meta(conn, "inicio", db.iso(hoy()))
    conn.close()
    if os.environ.get("CHORES_NO_BACKUP") != "1":
        iniciar_backups_automaticos(DB_PATH, BACKUPS, float(os.environ.get("BACKUP_EVERY_HOURS", "24")),
                                    int(os.environ.get("BACKUP_KEEP", "30")))
    yield


app = FastAPI(title="Tareas de la casa", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def _parse_dia(texto: str | None) -> date:
    if not texto:
        return hoy()
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise HTTPException(400, "Fecha inválida")


def _chica_o_404(cid: str):
    if cid not in cfg.chicas:
        raise HTTPException(404, "Esa chica no existe")
    return cfg.chicas[cid]


def _estado_dia(conn, cid: str, dia: date) -> dict:
    hechas = points._hechas(conn, cid, dia)
    tareas = cfg.tareas(cid, dia)
    return {
        "fecha": db.iso(dia),
        "dia_nombre": DIAS_LARGOS[dia.weekday()],
        "es_hoy": dia == hoy(),
        "tareas": [{"id": t.id, "texto": t.texto, "hecha": t.id in hechas} for t in tareas],
        "hechas": sum(1 for t in tareas if t.id in hechas),
        "total_tareas": len(tareas),
        "completo": points.dia_completo(cfg, conn, cid, dia),
        "puntos_dia": points.puntos_del_dia(conn, cid, dia),
    }


def _resumen_chica(conn, cid: str) -> dict:
    h = hoy()
    c = cfg.chicas[cid]
    lunes = h - timedelta(days=h.weekday())
    return {
        "id": cid, "nombre": c.nombre, "emoji": c.emoji, "color": c.color, "fondo": c.fondo,
        "puntos": points.total(conn, cid),
        "puntos_semana": points.puntos_desde(conn, cid, lunes),
        "racha": points.racha(conn, cfg, cid, h),
    }


@app.get("/api/chicas")
def chicas(conn=Depends(conn_dep)):
    return {"hoy": db.iso(hoy()), "chicas": [_resumen_chica(conn, cid) for cid in cfg.chicas]}


@app.get("/api/chicas/{cid}")
def chica(cid: str, fecha: str | None = None, conn=Depends(conn_dep)):
    _chica_o_404(cid)
    dia = _parse_dia(fecha)
    h = hoy()
    if dia > h:
        raise HTTPException(400, "Ese día todavía no llegó")
    semana = []
    lunes = dia - timedelta(days=dia.weekday())
    for i in range(7):
        d = lunes + timedelta(days=i)
        st = _estado_dia(conn, cid, d) if d <= h else None
        semana.append({
            "fecha": db.iso(d), "dia_corto": DIAS_LARGOS[i][:3],
            "hay_tareas": bool(cfg.tareas(cid, d)),
            "completo": bool(st and st["completo"]),
            "avance": (st["hechas"] / st["total_tareas"]) if st and st["total_tareas"] else 0,
            "futuro": d > h,
        })
    return {
        **_resumen_chica(conn, cid),
        "hoy": db.iso(h),
        "puntos_tarea": cfg.puntos_tarea, "puntos_bonus": cfg.puntos_dia,
        "dia": _estado_dia(conn, cid, dia),
        "semana": semana,
        "max_dias_atras": DIAS_ATRAS_CHICAS,
    }


def _marcar(conn, cid, tarea, fecha, hecho, quien, limite):
    _chica_o_404(cid)
    try:
        r = points.marcar(conn, cfg, cid, _parse_dia(fecha), tarea, bool(hecho), quien, hoy(), limite)
    except points.ReglaError as e:
        raise HTTPException(400, e.mensaje)
    dia = _parse_dia(fecha)
    return {**r, "dia": _estado_dia(conn, cid, dia), "resumen": _resumen_chica(conn, cid)}


@app.put("/api/chicas/{cid}/tareas/{tarea}")
def marcar(cid: str, tarea: str, body: dict = Body(...), conn=Depends(conn_dep)):
    if "hecha" not in body or not isinstance(body["hecha"], bool):
        raise HTTPException(400, "Falta 'hecha' (true/false)")
    return _marcar(conn, cid, tarea, body.get("fecha"), body["hecha"], cid, DIAS_ATRAS_CHICAS)


# ---------- Padres ----------

@app.post("/api/padres/login")
def login(response: Response, body: dict = Body(...)):
    pin = str(body.get("pin", ""))
    if not PIN_PADRES or not hmac.compare_digest(pin, PIN_PADRES):
        raise HTTPException(401, "PIN incorrecto")
    response.set_cookie("padres", _token_padres(), max_age=60 * 60 * 24 * 90, httponly=True, samesite="strict")
    return {"ok": True}


@app.post("/api/padres/logout")
def logout(response: Response):
    response.delete_cookie("padres")
    return {"ok": True}


@app.get("/api/padres/sesion")
def sesion(padres: str | None = Cookie(default=None)):
    return {"ok": es_padre(padres), "configurado": bool(PIN_PADRES)}


@app.get("/api/padres/resumen", dependencies=[Depends(requiere_padre)])
def resumen(conn=Depends(conn_dep)):
    h = hoy()
    lunes = h - timedelta(days=h.weekday())
    out = []
    for cid in cfg.chicas:
        dias = []
        for i in range(7):
            d = lunes + timedelta(days=i)
            st = _estado_dia(conn, cid, d)
            dias.append({**st, "futuro": d > h})
        out.append({**_resumen_chica(conn, cid), "dias": dias})
    return {"hoy": db.iso(h), "chicas": out}


@app.get("/api/padres/dia", dependencies=[Depends(requiere_padre)])
def dia_padres(cid: str, fecha: str, conn=Depends(conn_dep)):
    _chica_o_404(cid)
    return _estado_dia(conn, cid, _parse_dia(fecha))


@app.put("/api/padres/chicas/{cid}/tareas/{tarea}", dependencies=[Depends(requiere_padre)])
def marcar_padres(cid: str, tarea: str, body: dict = Body(...), conn=Depends(conn_dep)):
    if "hecha" not in body or not isinstance(body["hecha"], bool):
        raise HTTPException(400, "Falta 'hecha' (true/false)")
    return _marcar(conn, cid, tarea, body.get("fecha"), body["hecha"], "padres", None)


@app.get("/api/padres/historial", dependencies=[Depends(requiere_padre)])
def historial(limite: int = 60, conn=Depends(conn_dep)):
    rows = conn.execute("SELECT * FROM eventos ORDER BY id DESC LIMIT ?", (min(max(limite, 1), 500),)).fetchall()
    return [{**dict(r), "tarea_texto": cfg.texto_de(r["tarea"])} for r in rows]


@app.post("/api/padres/backup", dependencies=[Depends(requiere_padre)])
def backup_ahora():
    return {"archivo": hacer_backup(DB_PATH, BACKUPS, int(os.environ.get("BACKUP_KEEP", "30"))).name}


@app.get("/api/padres/exportar", dependencies=[Depends(requiere_padre)])
def exportar(conn=Depends(conn_dep)):
    data = {
        "exportado": datetime.now(TZ).isoformat(timespec="seconds"),
        "completadas": [dict(r) for r in conn.execute("SELECT * FROM completadas ORDER BY dia, chica")],
        "bonus": [dict(r) for r in conn.execute("SELECT * FROM bonus ORDER BY dia, chica")],
        "eventos": [dict(r) for r in conn.execute("SELECT * FROM eventos ORDER BY id")],
    }
    return JSONResponse(data, headers={"Content-Disposition": "attachment; filename=chores-export.json"})


@app.get("/salud")
def salud(conn=Depends(conn_dep)):
    conn.execute("SELECT 1")
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC / "icon-192.png")


@app.get("/padres")
def padres_page():
    return FileResponse(STATIC / "padres.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC), name="static")
