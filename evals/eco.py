"""Lo que el micrófono oyó del parlante no es un turno del paciente (0 tokens, sin red).

    uv run python -m evals.eco

Los casos vienen de llamadas reales del proyecto original, donde el filtro falló
en las dos direcciones: dejó pasar un eco que envenenó la historia clínica, y
descartó nueve turnos de un paciente porque su respuesta se parecía demasiado a
la pregunta. Las dos mitades importan, y por eso están las dos aquí.
"""
from __future__ import annotations

import sys

from server.seguridad.reglas import detect_red_flags, max_severity
from server.voz.eco import RegistroDeVoz

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {nombre}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(ok)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n== El eco del propio agente no es un turno ==")
    voz = RegistroDeVoz()
    voz.recordar("¿Cuál es la temperatura actual?", ahora=0.0)
    check("el eco de la llamada 19 se reconoce",
          voz.es_eco("la temperatura actual", ahora=1.0))
    check("y también la frase entera devuelta",
          voz.es_eco("cual es la temperatura actual", ahora=1.0))
    check("pasado el tiempo ya no se descarta como eco",
          not voz.es_eco("la temperatura actual", ahora=1000.0))

    print("\n== Contestar no es repetir ==")
    voz2 = RegistroDeVoz()
    voz2.recordar("¿Ha tenido fiebre o escalofríos desde la cirugía?", ahora=0.0)
    for real in ("no he tenido fiebre", "sí", "no", "tuve fiebre anoche de treinta y nueve",
                 "fiebre no pero escalofrios si"):
        check(f"«{real}» pasa como turno del paciente",
              not voz2.es_eco(real, ahora=1.0))

    # Vera contestó «no» a una pregunta de sí o no, y a partir de ahí cualquier
    # turno del paciente con un «no» dentro se descartaba por contención.
    voz3 = RegistroDeVoz()
    voz3.recordar("no", ahora=0.0)
    for real in ("no puedo tomar cerveza", "no no puedo tomar cerveza",
                 "no podria tomar cerveza", "entonces no debería hacer ejercicio"):
        check(f"tras un «no» de Vera, «{real[:34]}» sigue siendo del paciente",
              not voz3.es_eco(real, ahora=1.0))
    voz3.recordar("mantenga la herida seca", ahora=0.0)
    check("una frase de Vera con cuerpo dentro del turno sí es eco",
          voz3.es_eco("bueno mantenga la herida seca y sin humedad", ahora=1.0))

    # Elegir una de las opciones que Vera enumeró: cuanto más clara la
    # respuesta, más se parece a un eco.
    voz4 = RegistroDeVoz()
    voz4.recordar("¿Cuál es el tipo de ejercicio que le parece bien? "
                  "¿Caminar, estirar o algo más intenso?", ahora=0.0)
    for opcion in ("algo más intenso", "caminar", "estirar"):
        check(f"elegir la opción «{opcion}» no es eco",
              not voz4.es_eco(opcion, ahora=1.0))
    check("un trozo largo de esa misma pregunta sí es eco",
          voz4.es_eco("el tipo de ejercicio que le parece bien", ahora=1.0))

    # Enumerar con comas, no con « o »: las opciones se buscan sobre el texto
    # original, porque normalizar borra la puntuación.
    voz5 = RegistroDeVoz()
    voz5.recordar("¿Ha tenido dolor en estos días? Si lo ha tenido, dígame si le ha "
                  "bajado, sigue igual o le ha aumentado.", ahora=0.0)
    for opcion in ("sigue igual", "le ha aumentado"):
        check(f"contestar «{opcion}» no es eco", not voz5.es_eco(opcion, ahora=1.0))

    print("\n== Negar es información que solo pudo poner el paciente ==")
    voz6 = RegistroDeVoz()
    voz6.recordar("¿Ha tenido fiebre o escalofríos desde la cirugía?", ahora=0.0)
    for negada in ("no no ha tenido fiebre o escalofríos",
                   "no he tenido fiebre o escalofríos",
                   "nunca he tenido fiebre ni escalofríos"):
        check(f"«{negada[:38]}» es del paciente", not voz6.es_eco(negada, ahora=1.0))
    check("pero la pregunta devuelta sin negar sí es eco",
          voz6.es_eco("ha tenido fiebre o escalofríos desde la cirugía", ahora=1.0))
    voz7 = RegistroDeVoz()
    voz7.recordar("No sumergir la herida en agua hasta que el médico lo autorice.", ahora=0.0)
    check("una frase de Vera que ya negaba sigue siendo eco",
          voz7.es_eco("no sumergir la herida en agua hasta que el médico lo autorice",
                      ahora=1.0))

    print("\n== Lo que Vera dice ahora no puede sonar como una alarma del paciente ==")
    # Vera repite lo que el paciente le cuenta, así que su eco trae los mismos
    # síntomas. Es justo lo que este filtro existe para no dejar pasar.
    for frase in ("Esa fiebre de 39 es algo que su equipo debe saber hoy mismo.",
                  "Eso que ve en la herida, esa materia, hay que revisarlo.",
                  "¿Ese dolor en el pecho lo tuvo también anoche?"):
        v = RegistroDeVoz()
        v.recordar(frase, ahora=0.0)
        alarma = max_severity(detect_red_flags(frase))
        check(f"el eco de «{frase[:34]}…» se reconoce (dispara {alarma})",
              v.es_eco(frase.lower(), ahora=1.0))

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones del eco.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
