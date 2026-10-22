"""Carga y validación de tareas.yaml: quién hace qué cada día."""
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
DIAS_LARGOS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def slug(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")


@dataclass(frozen=True)
class Tarea:
    id: str
    texto: str


@dataclass(frozen=True)
class Chica:
    id: str
    nombre: str
    emoji: str
    color: str
    fondo: str


@dataclass(frozen=True)
class Extra:
    id: int
    chica: str
    tarea: Tarea
    dias: tuple[int, ...]
    desde: date
    hasta: date | None

    def aplica(self, dia: date) -> bool:
        return dia.weekday() in self.dias and self.desde <= dia and (self.hasta is None or dia <= self.hasta)


class Config:
    def __init__(self, data: dict):
        self.puntos_tarea = int(data.get("puntos_por_tarea", 10))
        self.puntos_dia = int(data.get("puntos_dia_completo", 20))
        self.chicas = {
            cid: Chica(cid, c["nombre"], c.get("emoji", "⭐"), c.get("color", "#333"), c.get("fondo", "#eee"))
            for cid, c in data["chicas"].items()
        }
        paquetes = {k: [Tarea(slug(t), t) for t in v] for k, v in data["paquetes"].items()}
        self._semana: dict[str, dict[str, list[Tarea]]] = {}
        self.extras: list[Extra] = []
        for dia in DIAS:
            plan = (data.get("semana") or {}).get(dia) or {}
            self._semana[dia] = {}
            for cid, asignado in plan.items():
                if cid not in self.chicas:
                    raise ValueError(f"{dia}: chica desconocida '{cid}'")
                if isinstance(asignado, str):
                    if asignado not in paquetes:
                        raise ValueError(f"{dia}/{cid}: paquete desconocido '{asignado}'")
                    tareas = list(paquetes[asignado])
                else:
                    tareas = [Tarea(slug(t), t) for t in asignado]
                ids = [t.id for t in tareas]
                if len(ids) != len(set(ids)):
                    raise ValueError(f"{dia}/{cid}: tarea repetida")
                self._semana[dia][cid] = tareas

    def tareas(self, chica: str, dia: date) -> list[Tarea]:
        base = self._semana[DIAS[dia.weekday()]].get(chica, [])
        return base + [e.tarea for e in self.extras if e.chica == chica and e.aplica(dia)]

    def cargar_extras(self, conn) -> None:
        self.extras = [
            Extra(r["id"], r["chica"], Tarea(slug(r["texto"]), r["texto"]),
                  tuple(int(x) for x in r["dias"].split(",")), date.fromisoformat(r["desde"]),
                  date.fromisoformat(r["hasta"]) if r["hasta"] else None)
            for r in conn.execute("SELECT * FROM tareas_extra ORDER BY id")
        ]

    def hay_conflicto(self, chica: str, tarea_id: str, dias: list[int]) -> bool:
        """¿Ya existe esa tarea (de la config o agregada y vigente) en alguno de esos días?"""
        for i in dias:
            if any(t.id == tarea_id for t in self._semana[DIAS[i]].get(chica, [])):
                return True
        return any(e.chica == chica and e.hasta is None and e.tarea.id == tarea_id and set(e.dias) & set(dias)
                   for e in self.extras)

    def texto_de(self, tarea_id: str) -> str:
        for e in self.extras:
            if e.tarea.id == tarea_id:
                return e.tarea.texto
        for plan in self._semana.values():
            for tareas in plan.values():
                for t in tareas:
                    if t.id == tarea_id:
                        return t.texto
        return tarea_id


def cargar(path: str | None = None) -> Config:
    p = Path(path or os.environ.get("CHORES_CONFIG", Path(__file__).resolve().parent.parent / "tareas.yaml"))
    return Config(yaml.safe_load(p.read_text(encoding="utf-8")))
