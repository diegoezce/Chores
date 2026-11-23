import hashlib
import hmac
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from contextlib import asynccontextmanager

from fastapi import Body, Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, points
from .backup import hacer_backup, iniciar_backups_automaticos
from .config import DIAS_LARGOS, cargar, slug
from .router_v2 import router as router_v2

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
    cfg.cargar_extras(conn)
    conn.close()
    if os.environ.get("CHORES_NO_BACKUP") != "1":
        iniciar_backups_automaticos(DB_PATH, BACKUPS, float(os.environ.get("BACKUP_EVERY_HOURS", "24")),
                                    int(os.environ.get("BACKUP_KEEP", "30")))
    yield


app = FastAPI(title="Tareas de la casa", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.include_router(router_v2)


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


EMOJIS_VALIDOS = {"🦄","🦊","🦋","🐱","🐶","🐼","🦁","🐸","🌸","⭐","🌈","🦩"}

def _resumen_chica(conn, cid: str) -> dict:
    h = hoy()
    c = cfg.chicas[cid]
    lunes = h - timedelta(days=h.weekday())
    emoji = db.get_meta(conn, f"emoji:{cid}") or c.emoji
    return {
        "id": cid, "nombre": c.nombre, "emoji": emoji, "color": c.color, "fondo": c.fondo,
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


@app.put("/api/chicas/{cid}/emoji")
def cambiar_emoji(cid: str, body: dict = Body(...), conn=Depends(conn_dep)):
    _chica_o_404(cid)
    emoji = str(body.get("emoji", ""))
    if emoji not in EMOJIS_VALIDOS:
        raise HTTPException(400, "Emoji no válido")
    db.set_meta(conn, f"emoji:{cid}", emoji)
    return {"ok": True, "emoji": emoji}


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
        mes = points.resumen_mes(conn, cfg, cid, h)
        con_premio = mes["pct"] >= cfg.minimo_premio
        out.append({**_resumen_chica(conn, cid), "dias": dias,
                    "mes": {**mes, "premio": con_premio, "premio_monto": points.monto_premio(premio_mensual(conn), mes, cfg.minimo_premio)}})
    return {"hoy": db.iso(h), "minimo_premio": cfg.minimo_premio, "premio_mensual": premio_mensual(conn), "chicas": out}


def premio_mensual(conn) -> int:
    return int(db.get_meta(conn, "premio_mensual", "0") or 0)


@app.put("/api/padres/premio", dependencies=[Depends(requiere_padre)])
def fijar_premio(body: dict = Body(...), conn=Depends(conn_dep)):
    monto = body.get("monto")
    if not isinstance(monto, int) or isinstance(monto, bool) or not 0 <= monto <= 100_000_000:
        raise HTTPException(400, "El premio tiene que ser un número entero (0 o más)")
    db.set_meta(conn, "premio_mensual", str(monto))
    return {"premio_mensual": monto}


@app.get("/api/padres/dia", dependencies=[Depends(requiere_padre)])
def dia_padres(cid: str, fecha: str, conn=Depends(conn_dep)):
    _chica_o_404(cid)
    return _estado_dia(conn, cid, _parse_dia(fecha))


@app.put("/api/padres/chicas/{cid}/tareas/{tarea}", dependencies=[Depends(requiere_padre)])
def marcar_padres(cid: str, tarea: str, body: dict = Body(...), conn=Depends(conn_dep)):
    if "hecha" not in body or not isinstance(body["hecha"], bool):
        raise HTTPException(400, "Falta 'hecha' (true/false)")
    return _marcar(conn, cid, tarea, body.get("fecha"), body["hecha"], "padres", None)


def _extra_json(e) -> dict:
    return {"id": e.id, "chica": e.chica, "texto": e.tarea.texto, "dias": list(e.dias), "desde": db.iso(e.desde)}


@app.get("/api/padres/tareas", dependencies=[Depends(requiere_padre)])
def tareas_extra():
    return [_extra_json(e) for e in cfg.extras if e.hasta is None]


@app.post("/api/padres/tareas", dependencies=[Depends(requiere_padre)])
def agregar_tarea(body: dict = Body(...), conn=Depends(conn_dep)):
    cid = str(body.get("chica", ""))
    _chica_o_404(cid)
    texto = " ".join(str(body.get("texto", "")).split())
    dias = body.get("dias")
    if not texto or len(texto) > 80 or not slug(texto):
        raise HTTPException(400, "Escribí el nombre de la tarea (hasta 80 letras)")
    if (not isinstance(dias, list) or not dias or
            not all(isinstance(i, int) and not isinstance(i, bool) and 0 <= i <= 6 for i in dias)):
        raise HTTPException(400, "Elegí al menos un día")
    dias = sorted(set(dias))
    if cfg.hay_conflicto(cid, slug(texto), dias):
        raise HTTPException(409, "Esa tarea ya existe en alguno de esos días")
    conn.execute("INSERT INTO tareas_extra(chica, texto, dias, desde) VALUES(?,?,?,?)",
                 (cid, texto, ",".join(map(str, dias)), db.iso(hoy())))
    cfg.cargar_extras(conn)
    return _extra_json(cfg.extras[-1])


@app.delete("/api/padres/tareas/{tid}", dependencies=[Depends(requiere_padre)])
def quitar_tarea(tid: int, conn=Depends(conn_dep)):
    """Deja de aplicar desde hoy; lo ya hecho y los puntos ganados quedan intactos."""
    cur = conn.execute("UPDATE tareas_extra SET hasta=? WHERE id=? AND hasta IS NULL",
                       (db.iso(hoy() - timedelta(days=1)), tid))
    if cur.rowcount == 0:
        raise HTTPException(404, "Esa tarea no existe")
    cfg.cargar_extras(conn)
    return {"ok": True}


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


@app.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
@app.get("/soporte", response_class=HTMLResponse, include_in_schema=False)
def privacy():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Política de Privacidad — Chores</title>
  <style>
    body { font-family: -apple-system, sans-serif; max-width: 680px; margin: 48px auto; padding: 0 24px; color: #1c1c1e; line-height: 1.6; }
    h1 { font-size: 1.6rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
    p, ul { font-size: 0.95rem; color: #3a3a3c; } ul { padding-left: 1.4rem; }
    footer { margin-top: 3rem; font-size: 0.8rem; color: #8e8e93; }
  </style>
</head>
<body>
  <h1>Política de Privacidad — Chores</h1>
  <p><em>Última actualización: noviembre 2026</em></p>
  <p>Chores es una aplicación familiar diseñada para uso privado dentro del hogar.</p>

  <h2>Datos que recolectamos</h2>
  <ul>
    <li>Dirección de correo electrónico del padre o madre administrador/a, utilizada únicamente para autenticación.</li>
    <li>Nombre y emoji de cada hijo, ingresados por el administrador para identificar perfiles dentro de la app.</li>
    <li>Registro de tareas completadas y puntos acumulados por cada hijo.</li>
  </ul>

  <h2>Cómo usamos los datos</h2>
  <p>Todos los datos se almacenan exclusivamente en el servidor propio de la familia (self-hosted). No se transmiten a servidores de terceros, no se comparten con ninguna empresa externa y no se utilizan con fines comerciales o publicitarios.</p>

  <h2>Terceros</h2>
  <p>Chores no integra servicios de analítica, publicidad, redes sociales ni ningún SDK de terceros que recolecte datos de los usuarios.</p>

  <h2>Seguridad</h2>
  <p>La comunicación entre la app y el servidor se realiza mediante HTTPS. Los tokens de sesión se almacenan de forma segura en el Keychain del dispositivo.</p>

  <h2>Menores de edad</h2>
  <p>La app está diseñada para ser utilizada por menores de edad bajo la supervisión directa de sus padres o tutores, quienes son los únicos administradores de los datos.</p>

  <h2>Contacto</h2>
  <p>Para consultas sobre privacidad: <a href="mailto:diegoezce@gmail.com">diegoezce@gmail.com</a></p>

  <footer>Chores · Uso familiar y privado</footer>
</body>
</html>""", status_code=200)


app.mount("/", StaticFiles(directory=STATIC), name="static")
