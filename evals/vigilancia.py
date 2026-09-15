"""Cómo alerta la vigilancia a lo largo de una llamada (0 tokens, sin red).

    uv run python -m evals.vigilancia

Los otros arneses prueban qué detecta el motor en una frase. Este prueba lo que
pasa entre frases: que una alerta salte una sola vez y que no se retire cuando el
reconocedor reescribe el turno al cerrarlo. Lo que Vera responde ya no es cosa de
la vigilancia —lo decide la conversación al cerrar el turno— y se prueba en
`evals/turno.py`.
"""
from __future__ import annotations

import sys

from server.seguridad.vigilancia import Vigilancia

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {nombre}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(ok)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    # Voz real, 13 de septiembre: el parcial oyó bien y el turno cerrado lo
    # reescribió. Aquí el cierre dice algo que ninguna regla reconoce, para
    # probar el caso aunque «jejum» ya esté declarado como confusión.
    v = Vigilancia()
    p = v.leer("Me dio pues un yeyo sin entender.", orden=9, cerrado=False)
    c = v.leer("Medio pues un yeso, si no entendiste.", orden=9, cerrado=True)
    check("la alerta salta en el parcial",
          [a.senal.concepto for a in p.nuevas] == ["perdida_conciencia"])
    check("y no se retira cuando el cierre reescribe el turno", len(v.alertas) == 1)
    check("el turno cerrado conserva el riesgo del parcial", c.riesgo == "critical", c.riesgo)
    check("el riesgo no se arrastra al turno siguiente",
          v.leer("Todo bien, gracias.", orden=10, cerrado=True).riesgo == "none")

    # Una por concepto: la fiebre vuelve en cada parcial y en el turno siguiente.
    v = Vigilancia()
    for texto, orden, cerrado in [("Tengo fiebre", 0, False),
                                  ("Tengo fiebre de 39 grados.", 0, True),
                                  ("Sigo con la fiebre.", 1, True)]:
        v.leer(texto, orden, cerrado)
    check("una sola alerta por concepto en toda la llamada",
          len(v.alertas) == 1, str(len(v.alertas)))

    # Un concepto nuevo sí alerta, aunque ya haya otra alerta abierta.
    d = v.leer("Y ahora no me entra el aire.", orden=2, cerrado=True)
    check("la disnea después de la fiebre es otra alerta",
          [a.senal.concepto for a in d.nuevas] == ["dificultad_respiratoria"])

    # Lo moderado queda en el turno pero no alerta.
    v = Vigilancia()
    m = v.leer("Me duele durísimo la herida.", orden=0, cerrado=True)
    check("lo moderado no alerta", not m.nuevas and m.riesgo == "moderate", m.riesgo)

    # La negación también se respeta en los parciales.
    v = Vigilancia()
    n = v.leer("No tengo fiebre", orden=0, cerrado=False)
    check("un parcial negado no alerta", not n.nuevas)

    # La ideación alerta como emergencia; cómo se le responde es de la conversación.
    v = Vigilancia()
    i = v.leer("Ya no quiero vivir más.", orden=0, cerrado=True)
    check("la ideación alerta como emergencia",
          [a.senal.severidad for a in i.nuevas] == ["critical"])

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones de la vigilancia.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
