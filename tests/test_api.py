import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

LUNES = date(2026, 10, 19)


@pytest.fixture
def client(monkeypatch):
    shutil.rmtree(main.DATA, ignore_errors=True)
    Path(main.DATA).mkdir(parents=True)
    monkeypatch.setattr(main, "hoy", lambda: LUNES + timedelta(days=1))  # martes 20
    with TestClient(main.app) as c:
        yield c


def put(client, cid, tarea, hecha, fecha=None, padres=False):
    base = "/api/padres/chicas" if padres else "/api/chicas"
    body = {"hecha": hecha}
    if fecha:
        body["fecha"] = fecha
    return client.put(f"{base}/{cid}/tareas/{tarea}", json=body)


def test_pantalla_del_dia_y_marcar(client):
    r = client.get("/api/chicas/pili").json()
    assert r["dia"]["dia_nombre"] == "Martes" and r["puntos"] == 0
    assert [t["texto"] for t in r["dia"]["tareas"]] == ["Ordenar el living", "Alimentar a Moana", "Poner el lavavajillas"]
    r = put(client, "pili", "ordenar-el-living", True).json()
    assert r["resumen"]["puntos"] == 10 and r["dia"]["hechas"] == 1


def test_completar_el_dia_da_bonus_y_racha(client):
    for t in ["ordenar-el-living", "alimentar-a-moana", "poner-el-lavavajillas"]:
        r = put(client, "pili", t, True).json()
    assert r["bonus_ganado"] and r["resumen"]["puntos"] == 50
    assert r["resumen"]["racha"]["actual"] == 1


def test_volver_atras_y_marcar_ayer(client):
    r = client.get("/api/chicas/numi?fecha=2026-10-19").json()
    assert r["dia"]["dia_nombre"] == "Lunes" and not r["dia"]["es_hoy"]
    r = put(client, "numi", "ordenar-el-living", True, fecha="2026-10-19")
    assert r.status_code == 200 and r.json()["resumen"]["puntos"] == 10


def test_futuro_rechazado(client):
    assert client.get("/api/chicas/pili?fecha=2026-10-22").status_code == 400
    assert put(client, "pili", "poner-la-mesa", True, fecha="2026-10-22").status_code == 400


def test_entradas_invalidas(client):
    assert client.get("/api/chicas/nadie").status_code == 404
    assert client.put("/api/chicas/pili/tareas/x", json={}).status_code == 400
    assert client.put("/api/chicas/pili/tareas/x", json={"hecha": "si"}).status_code == 400
    assert put(client, "pili", "x", True).status_code == 400
    assert client.get("/api/chicas/pili?fecha=basura").status_code == 400


def test_doble_toque_simultaneo_suma_una_sola_vez(client):
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda _: put(client, "pili", "ordenar-el-living", True), range(16)))
    assert all(r.status_code == 200 for r in res)
    assert client.get("/api/chicas/pili").json()["puntos"] == 10


def test_marcas_simultaneas_de_las_tres_no_se_pisan(client):
    jobs = [("pili", "ordenar-el-living"), ("numi", "levantar-la-mesa"), ("vicky", "poner-la-mesa")] * 5
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda j: put(client, j[0], j[1], True), jobs))
    assert all(r.status_code == 200 for r in res)
    pts = {c["id"]: c["puntos"] for c in client.get("/api/chicas").json()["chicas"]}
    assert pts == {"pili": 10, "numi": 10, "vicky": 10}


def test_padres_requiere_pin(client):
    assert client.get("/api/padres/resumen").status_code == 401
    assert client.put("/api/padres/chicas/pili/tareas/x", json={"hecha": True}).status_code == 401
    assert client.post("/api/padres/login", json={"pin": "0000"}).status_code == 401
    assert client.post("/api/padres/login", json={"pin": "4321"}).status_code == 200
    r = client.get("/api/padres/resumen")
    assert r.status_code == 200 and len(r.json()["chicas"]) == 3


def test_padres_corrigen_cualquier_dia_y_queda_historial(client):
    client.post("/api/padres/login", json={"pin": "4321"})
    r = put(client, "pili", "poner-la-mesa", True, fecha="2026-10-19", padres=True)
    assert r.status_code == 200 and r.json()["resumen"]["puntos"] == 10
    h = client.get("/api/padres/historial").json()
    assert h[0]["quien"] == "padres" and h[0]["tarea_texto"] == "Poner la mesa"


def test_las_chicas_no_pueden_usar_la_ruta_de_padres(client):
    assert put(client, "pili", "poner-la-mesa", True, padres=True).status_code == 401


def test_backup_manual_y_restauracion(client):
    put(client, "pili", "ordenar-el-living", True)
    client.post("/api/padres/login", json={"pin": "4321"})
    nombre = client.post("/api/padres/backup").json()["archivo"]
    copia = Path(main.BACKUPS) / nombre
    assert copia.exists()
    c = sqlite3.connect(copia)
    assert c.execute("SELECT COUNT(*) FROM completadas").fetchone()[0] == 1
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_exportar(client):
    put(client, "pili", "ordenar-el-living", True)
    client.post("/api/padres/login", json={"pin": "4321"})
    d = client.get("/api/padres/exportar").json()
    assert len(d["completadas"]) == 1 and len(d["eventos"]) == 1


def test_los_datos_sobreviven_a_un_reinicio(client):
    put(client, "pili", "ordenar-el-living", True)
    with TestClient(main.app) as c2:  # otro arranque sobre el mismo archivo
        assert c2.get("/api/chicas/pili").json()["puntos"] == 10
