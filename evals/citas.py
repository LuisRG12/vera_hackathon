"""La cita se verifica con código, y esto lo comprueba sin gastar un turno.

    uv run python -m evals.citas

**Por qué existe.** El proyecto midió el 13 de septiembre que la salida
estructurada garantiza la forma de la cita y no su verdad: con el fragmento
correcto fuera de la lista, el modelo citó los otros tres de tres. La conclusión
de aquella medición fue «las citas se verifican con código». Este arnés es la
comprobación de que ese código hace lo que dice.

Los casos son las formas que un modelo escribió de verdad —el campo bien puesto,
la marca dentro del texto, el JSON que se escapa, la cita a un fragmento que no
se le mostró— y no hipótesis sobre lo que podría escribir.

Cero tokens y cero red: es aritmética sobre cadenas.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from server.conocimiento.citas import atribuir, derivar, limpiar

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {nombre}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(ok)


@dataclass
class CitaFalsa:
    """Lo mínimo que `derivar` mira de una cita: su texto."""

    texto: str
    referencia: str = "documento.md §sección"


BANO = CitaFalsa("Puede ducharse a partir del tercer día después de la cirugía. "
                 "No sumerja las incisiones en agua.")
FIEBRE = CitaFalsa("Llame el mismo día si presenta fiebre de 38 °C o más; si alguna "
                   "incisión está roja, caliente o sale pus.")
DOLOR = CitaFalsa("Acetaminofén de 500 mg, una tableta cada ocho horas si tiene dolor, "
                  "sin pasar de tres tabletas al día.")
CITAS = [BANO, FIEBRE, DOLOR]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n== Lo que se dice en voz alta sale limpio ==")
    # Las tres formas observadas en el proyecto original, más la llave suelta.
    casos = [
        ("Debe avisarle a su equipo (citation_ids: 2), ya que eso no es normal.",
         "Debe avisarle a su equipo, ya que eso no es normal.", [2]),
        ("Su dolor es esperable [#3 | plan.md §Dolor] estos días.",
         "Su dolor es esperable estos días.", [3]),
        ("En esta semana (1), puede ducharse.", "En esta semana, puede ducharse.", [1]),
        ("Puede ducharse desde el tercer día #1", "Puede ducharse desde el tercer día", [1]),
        ('} Puede ducharse desde el tercer día.', "Puede ducharse desde el tercer día.", []),
    ]
    for crudo, esperado, ids in casos:
        limpio, vistos = limpiar(crudo)
        check(f"«{crudo[:44]}…»", limpio == esperado and vistos == ids,
              f"salió «{limpio}» con {vistos}, se esperaba «{esperado}» con {ids}")

    check("una frase sin marcas no se toca",
          limpiar("Puede ducharse a partir del tercer día.")
          == ("Puede ducharse a partir del tercer día.", []))
    # El caso que importa que NO se rompa: una cifra clínica no es una marca.
    check("una cifra clínica no se confunde con una cita",
          limpiar("Llame si tiene 38 grados o más.")[0] == "Llame si tiene 38 grados o más.")

    print("\n== De qué fragmento salió lo dicho, cuando el modelo no lo dijo ==")
    check("se atribuye al fragmento del que salen las palabras",
          atribuir("Puede ducharse a partir del tercer día después de la cirugía.",
                   CITAS) == [1])
    check("y al de la fiebre cuando habla de la fiebre",
          atribuir("Si presenta fiebre de 38 grados o más, llame el mismo día.",
                   CITAS) == [2])
    check("dos palabras en común no bastan si son de relleno",
          atribuir("¿Cómo se ha sentido hoy?", CITAS) == [])

    print("\n== La cita se resuelve contra lo que el modelo vio en ESTE turno ==")
    texto, citas = derivar("Puede ducharse desde el tercer día.", CITAS, declaradas=[1])
    check("lo declarado en su campo manda", citas == [BANO])

    texto, citas = derivar("Puede ducharse desde el tercer día (2).", CITAS, declaradas=[])
    check("si no declaró nada, vale la marca del texto", citas == [FIEBRE])
    check("y esa marca no se oye", texto == "Puede ducharse desde el tercer día.")

    texto, citas = derivar(
        "Puede ducharse a partir del tercer día después de la cirugía.", CITAS)
    check("sin campo ni marca, se atribuye por solapamiento", citas == [BANO])

    # El caso del 13 de septiembre: el modelo cita algo que no se le mostró.
    texto, citas = derivar("Eso lo tiene que ver su equipo.", CITAS, declaradas=[7])
    check("una cita a un fragmento inexistente NO se acepta", citas == [])

    texto, citas = derivar("Eso lo tiene que ver su equipo.", CITAS, declaradas=[0])
    check("ni la posición cero, que no existe", citas == [])

    texto, citas = derivar("¿Cómo ha seguido del dolor?", [], declaradas=[1])
    check("sin fragmentos en el turno no hay cita posible", citas == [])

    texto, citas = derivar("Puede ducharse desde el tercer día.", CITAS,
                           declaradas=[3, 1])
    check("se conserva el orden en que el modelo las declaró",
          citas == [DOLOR, BANO])

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones de la cita.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
