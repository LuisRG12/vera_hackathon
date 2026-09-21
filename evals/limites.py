"""Los topes de uso de la URL pública (0 tokens, sin red).

    uv run python -m evals.limites

Lo que se comprueba es lo que los hace confiables: que el cupo se devuelva, que
quien llega sin cupo no entre, y que el reloj de la llamada no corra antes de que
el paciente diga algo —el defecto que el proyecto original ya pagó una vez—.
"""
from __future__ import annotations

import sys

from server.limites import Cupo, Presupuesto

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {nombre}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(ok)


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n== Llamadas a la vez ==")
    cupo = Cupo(2)
    check("entran las dos primeras", cupo.tomar() and cupo.tomar())
    check("la tercera no entra", not cupo.tomar())
    cupo.soltar()
    check("al colgar una, entra otra", cupo.tomar())
    cupo.soltar()
    cupo.soltar()
    cupo.soltar()
    check("soltar de más no deja el cupo en negativo", cupo.en_curso == 0, str(cupo.en_curso))

    print("\n== El reloj arranca con el paciente, no con la conexión ==")
    reloj = Reloj()
    p = Presupuesto(max_turnos=3, max_segundos=600, reloj=reloj)
    reloj.t += 3600  # una hora con la pestaña abierta y nadie hablando
    check("sin turnos no hay tiempo transcurrido", p.transcurrido() == 0.0)
    check("y no hay tope excedido", p.excedido() is None)
    p.registrar_turno()
    reloj.t += 30
    check("desde el primer turno, sí corre", p.transcurrido() == 30.0, str(p.transcurrido()))

    print("\n== Los topes se aplican ==")
    p.registrar_turno()
    p.registrar_turno()
    check("en el tope de turnos todavía se responde", p.excedido() is None, p.excedido())
    p.registrar_turno()
    check("un turno más, se cierra", p.excedido() == "turnos", str(p.excedido()))

    reloj2 = Reloj()
    q = Presupuesto(max_turnos=30, max_segundos=600, reloj=reloj2)
    q.registrar_turno()
    reloj2.t += 599
    check("antes de los diez minutos, sigue", q.excedido() is None)
    reloj2.t += 2
    check("pasados, se cierra por duración", q.excedido() == "duracion", str(q.excedido()))

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones de los topes.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
