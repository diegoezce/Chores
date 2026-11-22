"""API v2: multi-familia con JWT (sin dependencias extra)."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from . import db, points
from .config import slug as make_slug
from .db import iso

router = APIRouter(prefix="/api/v2")

DATA = os.environ.get("CHORES_DATA", "data")
DB_PATH = f"{DATA}/chores.sqlite3"

EMOJIS_VALIDOS = {"🦄", "🦊", "🦋", "🐱", "🐶", "🐼", "🦁", "🐸", "🌸", "⭐", "🌈", "🦩"}

# ── JWT ───────────────────────────────────────────────────────────────────────

def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _secreto(conn) -> bytes:
    s = db.get_meta(conn, "secreto")
    if s is None:
        s = secrets.token_hex(32)
        db.set_meta(conn, "secreto", s)
    return s.encode()


def _make_jwt(payload: dict, secret: bytes) -> str:
    h = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    b = _b64u(json.dumps(payload).encode())
    sig = _b64u(hmac.new(secret, f"{h}.{b}".encode(), hashlib.sha256).digest())
    return f"{h}.{b}.{sig}"


def _verify_jwt(token: str, secret: bytes) -> dict | None:
    try:
        h, b, s = token.split(".")
        expected = _b64u(hmac.new(secret, f"{h}.{b}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(s, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(b + "=="))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def _emit_tokens(conn, usuario_id: int, familia_id: int) -> dict:
    secret = _secreto(conn)
    exp = int(time.time()) + 900  # 15 min
    access = _make_jwt({"sub": usuario_id, "fid": familia_id, "type": "access", "exp": exp}, secret)
    refresh = secrets.token_urlsafe(32)
    expira = iso(date.today() + timedelta(days=90))
    conn.execute(
        "INSERT INTO refresh_tokens(usuario_id,token,expira,creado) VALUES(?,?,?,?)",
        (usuario_id, refresh, expira, iso(date.today())),
    )
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}


# ── Password ──────────────────────────────────────────────────────────────────

def _hash_pw(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + ":" + dk.hex()


def _verify_pw(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ── Dependencias ──────────────────────────────────────────────────────────────

def conn_dep():
    conn = db.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def _check_access_token(authorization: str | None, conn) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token requerido")
    payload = _verify_jwt(authorization[7:], _secreto(conn))
    if not payload or payload.get("type") != "access":
        raise HTTPException(401, "Token inválido o expirado")
    return payload


def padre_dep(authorization: str | None = Header(default=None), conn=Depends(conn_dep)):
    payload = _check_access_token(authorization, conn)
    return {"conn": conn, "payload": payload}


def hijo_o_padre_dep(authorization: str | None = Header(default=None), conn=Depends(conn_dep)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token requerido")
    token = authorization[7:]
    # Intentar como access token (padre)
    payload = _verify_jwt(token, _secreto(conn))
    if payload and payload.get("type") == "access":
        return {"conn": conn, "payload": payload, "is_hijo": False}
    # Intentar como token de hijo
    row = conn.execute(
        "SELECT ht.hijo_id, h.familia_id FROM hijo_tokens ht JOIN hijos h ON h.id=ht.hijo_id "
        "WHERE ht.token=? AND ht.expira >= ?",
        (token, iso(date.today())),
    ).fetchone()
    if row:
        return {"conn": conn, "is_hijo": True, "hijo_id": row["hijo_id"], "familia_id": row["familia_id"]}
    raise HTTPException(401, "Token inválido o expirado")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _familia_o_404(conn, familia_id: int) -> dict:
    row = conn.execute("SELECT * FROM familias WHERE id=?", (familia_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Familia no encontrada")
    return dict(row)


def _hijo_o_404(conn, hijo_id: int, familia_id: int) -> dict:
    row = conn.execute("SELECT * FROM hijos WHERE id=? AND familia_id=?", (hijo_id, familia_id)).fetchone()
    if not row:
        raise HTTPException(404, "Hijo no encontrado")
    return dict(row)


def _tareas_hijo_db(conn, hijo_id: int, dia: date) -> list[dict]:
    rows = conn.execute("""
        SELECT td.id, td.texto, td.slug
        FROM asignaciones a JOIN tareas_def td ON a.tarea_id=td.id
        WHERE a.hijo_id=?
          AND a.desde <= ?
          AND (a.hasta IS NULL OR a.hasta >= ?)
          AND (',' || a.dias || ',') LIKE ('%,' || ? || ',%')
        ORDER BY td.texto
    """, (hijo_id, iso(dia), iso(dia), str(dia.weekday()))).fetchall()
    return [dict(r) for r in rows]


def _get_slugs_fn(conn, hijo_id: int):
    cache: dict[date, set[str]] = {}
    def fn(dia: date) -> set[str]:
        if dia not in cache:
            cache[dia] = {r["slug"] for r in _tareas_hijo_db(conn, hijo_id, dia)}
        return cache[dia]
    return fn


def _hoy_familia(conn, familia_id: int) -> date:
    row = conn.execute("SELECT timezone FROM familias WHERE id=?", (familia_id,)).fetchone()
    tz = row["timezone"] if row else "America/Argentina/Buenos_Aires"
    return datetime.now(ZoneInfo(tz)).date()


def _estado_dia_v2(conn, hijo_id: int, dia: date, familia: dict) -> dict:
    tareas = _tareas_hijo_db(conn, hijo_id, dia)
    slugs = {t["slug"] for t in tareas}
    hechas = points._hechas_v2(conn, hijo_id, dia)
    return {
        "fecha": iso(dia),
        "tareas": [{"id": t["slug"], "texto": t["texto"], "hecha": t["slug"] in hechas} for t in tareas],
        "hechas": sum(1 for t in tareas if t["slug"] in hechas),
        "total_tareas": len(tareas),
        "completo": bool(slugs) and slugs <= hechas,
        "puntos_dia": points.puntos_del_dia_v2(conn, hijo_id, dia),
    }


def _resumen_hijo_v2(conn, hijo: dict, hoy: date) -> dict:
    hijo_id = hijo["id"]
    lunes = hoy - timedelta(days=hoy.weekday())
    get_slugs = _get_slugs_fn(conn, hijo_id)
    return {
        **hijo,
        "puntos": points.total_v2(conn, hijo_id),
        "puntos_semana": points.puntos_desde_v2(conn, hijo_id, lunes),
        "racha": points.racha_v2(conn, hijo_id, hoy, get_slugs),
    }


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.post("/auth/registro", status_code=201)
def registro(body: dict = Body(...), conn=Depends(conn_dep)):
    nombre_familia = str(body.get("nombre_familia", "")).strip()
    nombre = str(body.get("nombre", "")).strip()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    if not nombre_familia or not nombre or not email or len(password) < 6:
        raise HTTPException(400, "nombre_familia, nombre, email y contraseña (mín. 6 chars) requeridos")
    if conn.execute("SELECT 1 FROM usuarios WHERE email=?", (email,)).fetchone():
        raise HTTPException(409, "Ya existe una cuenta con ese email")
    hoy_iso = iso(date.today())
    conn.execute(
        "INSERT INTO familias(nombre,timezone,idioma,puntos_tarea,puntos_dia,minimo_premio_pct,creado) "
        "VALUES(?,?,?,10,20,50,?)",
        (nombre_familia, "America/Argentina/Buenos_Aires", "es", hoy_iso),
    )
    familia_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO usuarios(familia_id,nombre,email,password_hash,creado) VALUES(?,?,?,?,?)",
        (familia_id, nombre, email, _hash_pw(password), hoy_iso),
    )
    usuario_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return _emit_tokens(conn, usuario_id, familia_id)


@router.post("/auth/login")
def login(body: dict = Body(...), conn=Depends(conn_dep)):
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    row = conn.execute("SELECT * FROM usuarios WHERE email=?", (email,)).fetchone()
    if not row or not _verify_pw(password, row["password_hash"] or ""):
        raise HTTPException(401, "Email o contraseña incorrectos")
    return _emit_tokens(conn, row["id"], row["familia_id"])


@router.post("/auth/refresh")
def refresh_token(body: dict = Body(...), conn=Depends(conn_dep)):
    token = str(body.get("refresh_token", ""))
    row = conn.execute(
        "SELECT rt.*, u.familia_id FROM refresh_tokens rt JOIN usuarios u ON u.id=rt.usuario_id "
        "WHERE rt.token=? AND rt.expira >= ?",
        (token, iso(date.today())),
    ).fetchone()
    if not row:
        raise HTTPException(401, "Refresh token inválido o expirado")
    conn.execute("DELETE FROM refresh_tokens WHERE id=?", (row["id"],))
    return _emit_tokens(conn, row["usuario_id"], row["familia_id"])


@router.post("/auth/logout")
def logout(body: dict = Body(...), conn=Depends(conn_dep)):
    conn.execute("DELETE FROM refresh_tokens WHERE token=?", (str(body.get("refresh_token", "")),))
    return {"ok": True}


# ── Familia ───────────────────────────────────────────────────────────────────

@router.get("/familia")
def get_familia(dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    familia = _familia_o_404(conn, fid)
    familia["premio_mensual"] = int(db.get_meta(conn, f"premio_mensual:{fid}", "0") or 0)
    return familia


@router.put("/familia")
def update_familia(body: dict = Body(...), dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    _familia_o_404(conn, fid)
    cols = {"nombre", "timezone", "puntos_tarea", "puntos_dia", "minimo_premio_pct"}
    updates = {k: v for k, v in body.items() if k in cols}
    if "premio_mensual" in body:
        monto = body["premio_mensual"]
        if not isinstance(monto, int) or isinstance(monto, bool) or not 0 <= monto <= 100_000_000:
            raise HTTPException(400, "premio_mensual debe ser entero ≥ 0")
        db.set_meta(conn, f"premio_mensual:{fid}", str(monto))
    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE familias SET {set_clause} WHERE id=?", (*updates.values(), fid))
    return get_familia(dep)


# ── Hijos ─────────────────────────────────────────────────────────────────────

@router.get("/hijos")
def get_hijos(dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    rows = conn.execute("SELECT * FROM hijos WHERE familia_id=? ORDER BY id", (fid,)).fetchall()
    return [dict(r) for r in rows]


@router.post("/hijos", status_code=201)
def crear_hijo(body: dict = Body(...), dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    nombre = str(body.get("nombre", "")).strip()
    emoji = str(body.get("emoji", "⭐"))
    color = str(body.get("color", "#333333"))
    fondo = str(body.get("fondo", "#eeeeee"))
    if not nombre or len(nombre) > 50:
        raise HTTPException(400, "Nombre requerido (hasta 50 chars)")
    if emoji not in EMOJIS_VALIDOS:
        raise HTTPException(400, "Emoji no válido")
    conn.execute(
        "INSERT INTO hijos(familia_id,nombre,emoji,color,fondo,creado) VALUES(?,?,?,?,?,?)",
        (fid, nombre, emoji, color, fondo, iso(date.today())),
    )
    hid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return dict(conn.execute("SELECT * FROM hijos WHERE id=?", (hid,)).fetchone())


@router.put("/hijos/{hijo_id}")
def update_hijo(hijo_id: int, body: dict = Body(...), dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    _hijo_o_404(conn, hijo_id, fid)
    cols = {"nombre": str, "emoji": str, "color": str, "fondo": str}
    for col, cast in cols.items():
        if col in body:
            val = cast(body[col]).strip() if col == "nombre" else cast(body[col])
            if col == "nombre" and not val:
                raise HTTPException(400, "Nombre no puede estar vacío")
            if col == "emoji" and val not in EMOJIS_VALIDOS:
                raise HTTPException(400, "Emoji no válido")
            conn.execute(f"UPDATE hijos SET {col}=? WHERE id=?", (val, hijo_id))
    return dict(conn.execute("SELECT * FROM hijos WHERE id=?", (hijo_id,)).fetchone())


@router.delete("/hijos/{hijo_id}")
def delete_hijo(hijo_id: int, dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    _hijo_o_404(conn, hijo_id, fid)
    ayer = iso(date.today() - timedelta(days=1))
    conn.execute("UPDATE asignaciones SET hasta=? WHERE hijo_id=? AND hasta IS NULL", (ayer, hijo_id))
    conn.execute("DELETE FROM hijo_tokens WHERE hijo_id=?", (hijo_id,))
    conn.execute("DELETE FROM hijos WHERE id=?", (hijo_id,))
    return {"ok": True}


# ── Día y marcado ─────────────────────────────────────────────────────────────

@router.get("/hijos/{hijo_id}/dia")
def dia_hijo(hijo_id: int, fecha: str | None = None, dep=Depends(hijo_o_padre_dep)):
    conn = dep["conn"]
    fid = dep.get("familia_id") or dep["payload"]["fid"]
    hijo = _hijo_o_404(conn, hijo_id, fid)
    if dep.get("is_hijo") and dep.get("hijo_id") != hijo_id:
        raise HTTPException(403, "No autorizado")
    familia = _familia_o_404(conn, fid)
    hoy = _hoy_familia(conn, fid)
    dia = date.fromisoformat(fecha) if fecha else hoy
    if dia > hoy:
        raise HTTPException(400, "Ese día todavía no llegó")
    return {
        **_resumen_hijo_v2(conn, hijo, hoy),
        "hoy": iso(hoy),
        "dia": _estado_dia_v2(conn, hijo_id, dia, familia),
        "max_dias_atras": 14,
    }


@router.get("/hijos/{hijo_id}/semana")
def semana_hijo(hijo_id: int, fecha: str | None = None, dep=Depends(hijo_o_padre_dep)):
    conn = dep["conn"]
    fid = dep.get("familia_id") or dep["payload"]["fid"]
    hijo = _hijo_o_404(conn, hijo_id, fid)
    if dep.get("is_hijo") and dep.get("hijo_id") != hijo_id:
        raise HTTPException(403, "No autorizado")
    familia = _familia_o_404(conn, fid)
    hoy = _hoy_familia(conn, fid)
    dia = date.fromisoformat(fecha) if fecha else hoy
    lunes = dia - timedelta(days=dia.weekday())
    semana = []
    for i in range(7):
        d = lunes + timedelta(days=i)
        st = _estado_dia_v2(conn, hijo_id, d, familia) if d <= hoy else None
        tareas_d = _tareas_hijo_db(conn, hijo_id, d)
        semana.append({
            "fecha": iso(d),
            "hay_tareas": bool(tareas_d),
            "completo": bool(st and st["completo"]),
            "avance": (st["hechas"] / st["total_tareas"]) if st and st["total_tareas"] else 0.0,
            "futuro": d > hoy,
        })
    return {**_resumen_hijo_v2(conn, hijo, hoy), "hoy": iso(hoy), "semana": semana}


@router.put("/hijos/{hijo_id}/tareas/{tarea_slug}")
def marcar_tarea(hijo_id: int, tarea_slug: str, body: dict = Body(...), dep=Depends(hijo_o_padre_dep)):
    if "hecha" not in body or not isinstance(body["hecha"], bool):
        raise HTTPException(400, "Falta 'hecha' (true/false)")
    conn = dep["conn"]
    fid = dep.get("familia_id") or dep["payload"]["fid"]
    _hijo_o_404(conn, hijo_id, fid)
    if dep.get("is_hijo") and dep.get("hijo_id") != hijo_id:
        raise HTTPException(403, "No autorizado")
    familia = _familia_o_404(conn, fid)
    hoy = _hoy_familia(conn, fid)
    fecha_str = body.get("fecha")
    dia = date.fromisoformat(fecha_str) if fecha_str else hoy
    slugs = {t["slug"] for t in _tareas_hijo_db(conn, hijo_id, dia)}
    limite = 14 if dep.get("is_hijo") else None
    quien = str(hijo_id) if dep.get("is_hijo") else "padres"
    try:
        r = points.marcar_v2(conn, hijo_id, dia, tarea_slug, body["hecha"],
                              quien, hoy, limite,
                              familia["puntos_tarea"], familia["puntos_dia"], slugs)
    except points.ReglaError as e:
        raise HTTPException(400, e.mensaje)
    hijo = dict(conn.execute("SELECT * FROM hijos WHERE id=?", (hijo_id,)).fetchone())
    return {**r, "dia": _estado_dia_v2(conn, hijo_id, dia, familia),
            "resumen": _resumen_hijo_v2(conn, hijo, hoy)}


# ── Resumen padres ────────────────────────────────────────────────────────────

@router.get("/padres/resumen")
def resumen_padres(dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    familia = _familia_o_404(conn, fid)
    hoy = _hoy_familia(conn, fid)
    lunes = hoy - timedelta(days=hoy.weekday())
    hijos = conn.execute("SELECT * FROM hijos WHERE familia_id=? ORDER BY id", (fid,)).fetchall()
    premio_total = int(db.get_meta(conn, f"premio_mensual:{fid}", "0") or 0)
    out = []
    for h in hijos:
        hijo = dict(h)
        hid = hijo["id"]
        get_slugs = _get_slugs_fn(conn, hid)
        dias = []
        for i in range(7):
            d = lunes + timedelta(days=i)
            st = _estado_dia_v2(conn, hid, d, familia)
            dias.append({**st, "futuro": d > hoy})
        mes = points.resumen_mes_v2(conn, hid, hoy, get_slugs,
                                    familia["puntos_tarea"], familia["puntos_dia"])
        con_premio = mes["pct"] >= familia["minimo_premio_pct"]
        out.append({
            **_resumen_hijo_v2(conn, hijo, hoy),
            "dias": dias,
            "mes": {**mes, "premio": con_premio,
                    "premio_monto": points.monto_premio(premio_total, mes, familia["minimo_premio_pct"])},
        })
    return {"hoy": iso(hoy), "minimo_premio": familia["minimo_premio_pct"],
            "premio_mensual": premio_total, "hijos": out}


# ── Tareas def ────────────────────────────────────────────────────────────────

@router.get("/tareas-def")
def get_tareas_def(dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    rows = conn.execute("SELECT * FROM tareas_def WHERE familia_id=? ORDER BY texto", (fid,)).fetchall()
    return [dict(r) for r in rows]


@router.post("/tareas-def", status_code=201)
def crear_tarea_def(body: dict = Body(...), dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    texto = str(body.get("texto", "")).strip()
    if not texto or len(texto) > 80:
        raise HTTPException(400, "Texto requerido (hasta 80 chars)")
    slug = make_slug(texto)
    if not slug:
        raise HTTPException(400, "Texto inválido")
    if conn.execute("SELECT 1 FROM tareas_def WHERE familia_id=? AND slug=?", (fid, slug)).fetchone():
        raise HTTPException(409, "Ya existe una tarea con ese nombre")
    conn.execute("INSERT INTO tareas_def(familia_id,texto,slug) VALUES(?,?,?)", (fid, texto, slug))
    tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return dict(conn.execute("SELECT * FROM tareas_def WHERE id=?", (tid,)).fetchone())


@router.delete("/tareas-def/{tarea_id}")
def delete_tarea_def(tarea_id: int, dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    if not conn.execute("SELECT 1 FROM tareas_def WHERE id=? AND familia_id=?", (tarea_id, fid)).fetchone():
        raise HTTPException(404, "Tarea no encontrada")
    ayer = iso(date.today() - timedelta(days=1))
    conn.execute("UPDATE asignaciones SET hasta=? WHERE tarea_id=? AND hasta IS NULL", (ayer, tarea_id))
    return {"ok": True}


# ── Asignaciones ──────────────────────────────────────────────────────────────

@router.get("/asignaciones")
def get_asignaciones(hijo_id: int | None = None, dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    if hijo_id:
        _hijo_o_404(conn, hijo_id, fid)
        rows = conn.execute("""
            SELECT a.*, td.texto, td.slug FROM asignaciones a
            JOIN tareas_def td ON a.tarea_id=td.id
            WHERE a.hijo_id=? AND a.hasta IS NULL ORDER BY a.id
        """, (hijo_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT a.*, td.texto, td.slug FROM asignaciones a
            JOIN tareas_def td ON a.tarea_id=td.id
            JOIN hijos h ON h.id=a.hijo_id
            WHERE h.familia_id=? AND a.hasta IS NULL ORDER BY a.hijo_id, a.id
        """, (fid,)).fetchall()
    return [dict(r) for r in rows]


@router.post("/asignaciones", status_code=201)
def crear_asignacion(body: dict = Body(...), dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    hijo_id = body.get("hijo_id")
    tarea_id = body.get("tarea_id")
    dias = body.get("dias")
    if not isinstance(hijo_id, int) or not isinstance(tarea_id, int):
        raise HTTPException(400, "hijo_id y tarea_id requeridos")
    if not isinstance(dias, list) or not dias or not all(isinstance(i, int) and 0 <= i <= 6 for i in dias):
        raise HTTPException(400, "dias debe ser lista de enteros 0-6")
    _hijo_o_404(conn, hijo_id, fid)
    if not conn.execute("SELECT 1 FROM tareas_def WHERE id=? AND familia_id=?", (tarea_id, fid)).fetchone():
        raise HTTPException(404, "Tarea no encontrada")
    slug = conn.execute("SELECT slug FROM tareas_def WHERE id=?", (tarea_id,)).fetchone()["slug"]
    existentes = conn.execute("""
        SELECT a.dias FROM asignaciones a JOIN tareas_def td ON a.tarea_id=td.id
        WHERE a.hijo_id=? AND td.slug=? AND a.hasta IS NULL
    """, (hijo_id, slug)).fetchall()
    for e in existentes:
        if set(map(int, e["dias"].split(","))) & set(dias):
            raise HTTPException(409, "Esa tarea ya está asignada en alguno de esos días")
    dias_str = ",".join(map(str, sorted(set(dias))))
    conn.execute("INSERT INTO asignaciones(hijo_id,tarea_id,dias,desde) VALUES(?,?,?,?)",
                 (hijo_id, tarea_id, dias_str, iso(date.today())))
    aid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return dict(conn.execute("""
        SELECT a.*, td.texto, td.slug FROM asignaciones a
        JOIN tareas_def td ON a.tarea_id=td.id WHERE a.id=?
    """, (aid,)).fetchone())


@router.delete("/asignaciones/{asig_id}")
def delete_asignacion(asig_id: int, dep=Depends(padre_dep)):
    conn, fid = dep["conn"], dep["payload"]["fid"]
    row = conn.execute("""
        SELECT a.id FROM asignaciones a JOIN hijos h ON h.id=a.hijo_id
        WHERE a.id=? AND h.familia_id=? AND a.hasta IS NULL
    """, (asig_id, fid)).fetchone()
    if not row:
        raise HTTPException(404, "Asignación no encontrada")
    conn.execute("UPDATE asignaciones SET hasta=? WHERE id=?",
                 (iso(date.today() - timedelta(days=1)), asig_id))
    return {"ok": True}


# ── Vinculación hijo–dispositivo ──────────────────────────────────────────────

@router.post("/hijo-link")
def generar_link(body: dict = Body(...), dep=Depends(padre_dep)):
    """Genera un token de un solo uso para vincular el dispositivo del hijo."""
    conn, fid = dep["conn"], dep["payload"]["fid"]
    hijo_id = body.get("hijo_id")
    if not isinstance(hijo_id, int):
        raise HTTPException(400, "hijo_id requerido")
    _hijo_o_404(conn, hijo_id, fid)
    conn.execute("DELETE FROM hijo_tokens WHERE hijo_id=?", (hijo_id,))
    token = secrets.token_urlsafe(24)
    expira = iso(date.today() + timedelta(days=7))
    conn.execute("INSERT INTO hijo_tokens(hijo_id,token,expira,creado) VALUES(?,?,?,?)",
                 (hijo_id, token, expira, iso(date.today())))
    return {"link_token": token, "expira": expira, "hijo_id": hijo_id}


@router.post("/hijo-link/activar")
def activar_link(body: dict = Body(...), conn=Depends(conn_dep)):
    """El dispositivo del hijo activa su sesión con el código del padre."""
    token = str(body.get("link_token", ""))
    row = conn.execute(
        "SELECT * FROM hijo_tokens WHERE token=? AND expira >= ?",
        (token, iso(date.today())),
    ).fetchone()
    if not row:
        raise HTTPException(401, "Código inválido o expirado")
    nuevo = secrets.token_urlsafe(32)
    expira = iso(date.today() + timedelta(days=365))
    conn.execute("UPDATE hijo_tokens SET token=?, expira=? WHERE id=?", (nuevo, expira, row["id"]))
    hijo = dict(conn.execute("SELECT * FROM hijos WHERE id=?", (row["hijo_id"],)).fetchone())
    return {"session_token": nuevo, "expira": expira, "hijo": hijo}


# ── Cuenta ────────────────────────────────────────────────────────────────────

@router.delete("/cuenta")
def borrar_cuenta(dep=Depends(padre_dep)):
    """Borra la familia y todos sus datos (requisito App Store 5.1.1(v))."""
    conn, fid = dep["conn"], dep["payload"]["fid"]
    hijos = [r["id"] for r in conn.execute("SELECT id FROM hijos WHERE familia_id=?", (fid,)).fetchall()]
    for hid in hijos:
        for t in ("hijo_tokens", "asignaciones", "completadas2", "bonus2", "eventos2"):
            conn.execute(f"DELETE FROM {t} WHERE hijo_id=?", (hid,))
    conn.execute("DELETE FROM hijos WHERE familia_id=?", (fid,))
    for uid in [r["id"] for r in conn.execute("SELECT id FROM usuarios WHERE familia_id=?", (fid,)).fetchall()]:
        conn.execute("DELETE FROM refresh_tokens WHERE usuario_id=?", (uid,))
    conn.execute("DELETE FROM usuarios WHERE familia_id=?", (fid,))
    conn.execute("DELETE FROM tareas_def WHERE familia_id=?", (fid,))
    conn.execute("DELETE FROM familias WHERE id=?", (fid,))
    return {"ok": True}
