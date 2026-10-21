from datetime import date, timedelta

import pytest

from app import config, db, points

LUNES = date(2026, 10, 19)  # lunes
DOM = date(2026, 10, 25)


@pytest.fixture
def cfg():
    return config.cargar()


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.sqlite3"))
    db.set_meta(c, "inicio", db.iso(LUNES))
    return c


def marcar(conn, cfg, chica, dia, tarea, hecho=True, hoy=None, limite=14):
    return points.marcar(conn, cfg, chica, dia, tarea, hecho, "test", hoy or dia, limite)


def hacer_todo(conn, cfg, chica, dia, hoy=None):
    for t in cfg.tareas(chica, dia):
        marcar(conn, cfg, chica, dia, t.id, True, hoy)


def test_semana_coincide_con_el_cuadro(cfg):
    # Lunes: Pili paquete A (4), Numi B (3), Vicky C (4)
    assert [t.texto for t in cfg.tareas("pili", LUNES)][0] == "Poner la mesa"
    assert len(cfg.tareas("pili", LUNES)) == 4
    assert len(cfg.tareas("numi", LUNES)) == 3
    assert len(cfg.tareas("vicky", LUNES)) == 4
    # Jueves repite lunes, miércoles rota
    assert cfg.tareas("pili", LUNES + timedelta(days=3)) == cfg.tareas("pili", LUNES)
    assert cfg.tareas("pili", LUNES + timedelta(days=2))[0].texto == "Levantar la mesa"
    # Sábado: variantes del cuadro
    sab = LUNES + timedelta(days=5)
    assert len(cfg.tareas("pili", sab)) == 3
    assert len(cfg.tareas("vicky", sab)) == 4
    # Domingo libre
    assert all(cfg.tareas(c, DOM) == [] for c in cfg.chicas)


def test_una_tarea_suma_10(conn, cfg):
    t = cfg.tareas("pili", LUNES)[0]
    r = marcar(conn, cfg, "pili", LUNES, t.id)
    assert r["cambio"] and not r["bonus_ganado"]
    assert points.total(conn, "pili") == 10


def test_marcar_dos_veces_no_suma_doble(conn, cfg):
    t = cfg.tareas("pili", LUNES)[0]
    marcar(conn, cfg, "pili", LUNES, t.id)
    r = marcar(conn, cfg, "pili", LUNES, t.id)
    assert not r["cambio"]
    assert points.total(conn, "pili") == 10


def test_desmarcar_resta_y_es_idempotente(conn, cfg):
    t = cfg.tareas("pili", LUNES)[0]
    marcar(conn, cfg, "pili", LUNES, t.id)
    marcar(conn, cfg, "pili", LUNES, t.id, False)
    marcar(conn, cfg, "pili", LUNES, t.id, False)
    assert points.total(conn, "pili") == 0


def test_dia_completo_da_bonus_una_sola_vez(conn, cfg):
    hacer_todo(conn, cfg, "numi", LUNES)  # 3 tareas
    assert points.total(conn, "numi") == 3 * 10 + 20
    hacer_todo(conn, cfg, "numi", LUNES)  # repetir no cambia nada
    assert points.total(conn, "numi") == 50


def test_bonus_se_retira_al_desmarcar_y_vuelve(conn, cfg):
    hacer_todo(conn, cfg, "numi", LUNES)
    t = cfg.tareas("numi", LUNES)[0]
    r = marcar(conn, cfg, "numi", LUNES, t.id, False)
    assert r["bonus_perdido"]
    assert points.total(conn, "numi") == 2 * 10
    r = marcar(conn, cfg, "numi", LUNES, t.id, True)
    assert r["bonus_ganado"]
    assert points.total(conn, "numi") == 50


def test_las_chicas_no_se_mezclan(conn, cfg):
    hacer_todo(conn, cfg, "pili", LUNES)
    assert points.total(conn, "numi") == 0 and points.total(conn, "vicky") == 0


def test_no_se_puede_marcar_el_futuro(conn, cfg):
    t = cfg.tareas("pili", LUNES + timedelta(days=1))[0]
    with pytest.raises(points.ReglaError) as e:
        points.marcar(conn, cfg, "pili", LUNES + timedelta(days=1), t.id, True, "t", LUNES, 14)
    assert e.value.codigo == "futuro"
    assert points.total(conn, "pili") == 0


def test_limite_de_dias_atras_para_chicas_pero_no_para_padres(conn, cfg):
    hoy = LUNES + timedelta(days=30)
    viejo = LUNES  # lunes, 30 días atrás
    t = cfg.tareas("pili", viejo)[0]
    with pytest.raises(points.ReglaError) as e:
        points.marcar(conn, cfg, "pili", viejo, t.id, True, "pili", hoy, 14)
    assert e.value.codigo == "muy_viejo"
    points.marcar(conn, cfg, "pili", viejo, t.id, True, "padres", hoy, None)
    assert points.total(conn, "pili") == 10


def test_tarea_que_no_es_del_dia_se_rechaza(conn, cfg):
    with pytest.raises(points.ReglaError):
        marcar(conn, cfg, "pili", LUNES, "ordenar-el-living")  # eso es de Numi ese día
    with pytest.raises(points.ReglaError):
        marcar(conn, cfg, "pili", LUNES, "inventada")
    assert points.total(conn, "pili") == 0


def test_domingo_no_tiene_tareas_ni_bonus(conn, cfg):
    with pytest.raises(points.ReglaError):
        marcar(conn, cfg, "pili", DOM, "poner-la-mesa")
    assert not points.dia_completo(cfg, conn, "pili", DOM)


def test_editar_tareas_no_cambia_puntos_ya_ganados(conn, cfg):
    hacer_todo(conn, cfg, "numi", LUNES)
    antes = points.total(conn, "numi")
    cfg2 = config.Config({
        "puntos_por_tarea": 999, "puntos_dia_completo": 999,
        "chicas": {"numi": {"nombre": "Numi"}}, "paquetes": {"X": ["Otra cosa"]},
        "semana": {"lunes": {"numi": "X"}},
    })
    assert points.total(conn, "numi") == antes
    assert cfg2 is not None


def test_tarea_borrada_de_la_config_conserva_su_puntaje(conn, cfg):
    t = cfg.tareas("numi", LUNES)[0]
    marcar(conn, cfg, "numi", LUNES, t.id)
    cfg2 = config.Config({
        "chicas": {"numi": {"nombre": "Numi"}}, "paquetes": {"X": ["Otra cosa"]},
        "semana": {"lunes": {"numi": "X"}},
    })
    assert points.total(conn, "numi") == 10
    # y se puede desmarcar aunque ya no esté en la lista
    points.marcar(conn, cfg2, "numi", LUNES, t.id, False, "padres", LUNES, None)
    assert points.total(conn, "numi") == 0


def test_total_siempre_coincide_con_el_historial(conn, cfg):
    """La suma guardada debe poder reconstruirse desde la bitácora."""
    import random
    rnd = random.Random(7)
    dias = [LUNES + timedelta(days=i) for i in range(6)]
    hoy = LUNES + timedelta(days=5)
    for _ in range(400):
        d = rnd.choice(dias)
        ts = cfg.tareas("vicky", d)
        marcar(conn, cfg, "vicky", d, rnd.choice(ts).id, rnd.random() < .6, hoy)
    esperado = 0
    for d in dias:
        n = conn.execute("SELECT COUNT(*) c FROM completadas WHERE chica='vicky' AND dia=?", (db.iso(d),)).fetchone()["c"]
        esperado += n * 10
        if n == len(cfg.tareas("vicky", d)):
            esperado += 20
    assert points.total(conn, "vicky") == esperado
    # y la bitácora cuadra: marcar - desmarcar por tarea == está hecha
    for r in conn.execute("SELECT chica, dia, tarea FROM completadas"):
        m = conn.execute("SELECT COUNT(*) c FROM eventos WHERE chica=? AND dia=? AND tarea=? AND accion='marcar'",
                         (r["chica"], r["dia"], r["tarea"])).fetchone()["c"]
        u = conn.execute("SELECT COUNT(*) c FROM eventos WHERE chica=? AND dia=? AND tarea=? AND accion='desmarcar'",
                         (r["chica"], r["dia"], r["tarea"])).fetchone()["c"]
        assert m - u == 1


# ---- rachas ----

def test_racha_cuenta_dias_seguidos_y_salta_el_domingo(conn, cfg):
    # Sáb 24 completo, domingo libre, lunes 26 completo => 2 días
    sab = date(2026, 10, 24)
    lun = date(2026, 10, 26)
    db.set_meta(conn, "inicio", db.iso(sab))
    hacer_todo(conn, cfg, "numi", sab, hoy=lun)
    hacer_todo(conn, cfg, "numi", lun, hoy=lun)
    assert points.racha(conn, cfg, "numi", lun)["actual"] == 2


def test_hoy_incompleto_no_corta_la_racha(conn, cfg):
    hacer_todo(conn, cfg, "numi", LUNES, hoy=LUNES + timedelta(days=1))
    assert points.racha(conn, cfg, "numi", LUNES + timedelta(days=1))["actual"] == 1


def test_dia_pasado_incompleto_corta_la_racha(conn, cfg):
    hacer_todo(conn, cfg, "numi", LUNES, hoy=LUNES + timedelta(days=2))
    # martes sin hacer nada; hoy miércoles completo
    mie = LUNES + timedelta(days=2)
    hacer_todo(conn, cfg, "numi", mie, hoy=mie)
    r = points.racha(conn, cfg, "numi", mie)
    assert r["actual"] == 1 and r["mejor"] == 1


def test_mejor_racha_se_conserva(conn, cfg):
    for i in range(3):
        hacer_todo(conn, cfg, "numi", LUNES + timedelta(days=i), hoy=LUNES + timedelta(days=4))
    # jueves sin hacer; hoy viernes completo
    vie = LUNES + timedelta(days=4)
    hacer_todo(conn, cfg, "numi", vie, hoy=vie)
    r = points.racha(conn, cfg, "numi", vie)
    assert r["actual"] == 1 and r["mejor"] == 3


def test_racha_cero_si_no_hay_nada(conn, cfg):
    assert points.racha(conn, cfg, "pili", LUNES) == {"actual": 0, "mejor": 0}
