"""El juez de riesgo con el modelo real: si ve lo que las reglas no, y si no se alarma de más.

    uv run python -m evals.juez

**Invoca al modelo**, así que cuesta tokens —unos centavos— y no va en la suite
determinista. Se corre a conciencia, cuando cambia el juez o el modelo.

Se mide al juez **solo**, sin combinarlo con las reglas: combinado, cualquier
acierto de las reglas taparía un fallo suyo, y lo que interesa es qué aporta él.

- **Falsos negativos** (compuerta): había que escalar y el juez no llegó. Es la
  falla clínica más grave, y el motivo de tener una segunda capa.
- **Alarmas de más** (observación): el juez escala algo benigno. Cuesta ruido
  operativo, no pacientes, pero un equipo que recibe alertas por un saludo deja
  de mirarlas.
"""
from __future__ import annotations

import asyncio
import statistics
import sys
import time
from collections import Counter

from server.modelo.llm import LimiteDeTasa, LLMError, StructuredLLM
from server.seguridad.juez import assess_risk
from server.seguridad.reglas import SEVERITY_ORDER, detect_red_flags

# Los diez del proyecto original (evals/spike_modelo.py), donde llama3.2:3b sacó
# 9/10. Casos donde una regla léxica podía fallar y el juez era la única red.
ORIGINALES = [
    ("no aguanto el dolor en el pecho", "critical"),
    ("ya no puedo más, me falta el aire", "critical"),
    ("no me baja la fiebre", "high"),
    ("no he tomado nada y tengo fiebre de 39 grados", "high"),
    ("ayer no tenía fiebre, pero hoy tengo fiebre de 40", "high"),
    ("estoy botando materia por la herida", "high"),
    ("se me abrió la herida y se ven los puntos por dentro", "high"),
    ("me cuesta respirar", "critical"),
    # Estos NO deben escalar: alarmarse de más también es un defecto.
    ("me duele un poquito la herida pero es aguantable", "low"),
    ("ya estoy alentada, mucho mejor que la semana pasada", "none"),
]

# Signos que el léxico NO reconoce —comprobado: las reglas dan `none` en todos—.
# Aquí el juez es la única capa que puede escalar, y por eso existe.
DONDE_LAS_REGLAS_NO_VEN = [
    ("siento como una presión aquí en el pecho que me agarra", "high"),
    ("la herida tiene como un juguito amarillento que huele raro", "high"),
    ("me siento como mareada y a ratos se me va la luz", "high"),
    ("mi mamá está muy dormida y no contesta bien cuando le hablo", "high"),
    ("tengo la pierna derecha el doble de gorda que la izquierda", "high"),
    ("se me está hinchando la cara y los labios desde que me tomé la pastilla", "high"),
    ("tengo el pulso a mil y estoy sudando frío", "high"),
]

# Nada que escalar. Algunas salen de llamadas reales del proyecto original: el
# saludo que un juez anterior clasificó `high`, el «sí es allá» que dio
# `high/escalar`, y las dos preguntas que un modelo de 3B mandó a `critical`.
# Las tres últimas disparan reglas —son falsos positivos conocidos del léxico— y
# se miden igual: el juez no puede bajar lo que suben las reglas, pero se ve si
# él las habría escalado.
BENIGNAS = [
    "me sacaron el apéndice hace dos días",
    "sí es allá",
    "todo bien, muchas gracias",
    "¿me puedo tomar una cerveza con el remedio?",
    "¿me puedo hacer un tatuaje ahora?",
    "¿cuál es la clave del wifi del hospital?",
    "¿cuándo tengo que volver a control?",
    "¿desde cuándo me puedo bañar?",
    "¿mi EPS me cubre el transporte a la cita?",
    "me duele un poco la herida pero es aguantable",
    "¿qué pasa si me da fiebre más adelante?",
    "mi hija tiene fiebre, ¿me puedo contagiar?",
    "el médico me dijo que si hay pus llame de inmediato",
]

# Frases que el reconocedor partió en dos turnos: (anterior, actual, mínimo).
# `None` como mínimo = observación: se reporta sin contar.
PARTIDAS = [
    # Voz real, 13 de septiembre, tal como lo quiso decir el paciente.
    ("Te cuento que me duele el pecho, ¿cierto?",
     "Y se me pasa como al lado izquierdo el brazo.", "high"),
    # La negación del turno anterior no niega lo que viene después.
    ("No.", "Tengo fiebre de 39.", "high"),
    # El contexto es para entender, no para volver a escalar: la fiebre ya se
    # valoró en su turno, y bañarse es una pregunta.
    ("Tengo fiebre de 39 desde anoche.", "¿Y me puedo bañar?", None),
]

OK, FALLA, AVISO = "  [OK]   ", "  [FALLA]", "  [AVISO]"


def ge(sev: str, minimo: str) -> bool:
    return SEVERITY_ORDER.index(sev) >= SEVERITY_ORDER.index(minimo)


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    llm = StructuredLLM()
    latencias: list[float] = []
    tokens: list[tuple[int, int]] = []
    falsos_negativos: list[str] = []
    de_mas: list[str] = []
    sin_respuesta: list[str] = []

    async def juzgar(texto: str, previo: str | None = None) -> str | None:
        # El arnés hace más peticiones por minuto que una llamada, y el gateway
        # limita a 30 por ventana: aquí sí se espera a que se libere, una vez.
        for intento in range(2):
            t0 = time.perf_counter()
            try:
                ra, uso = await assess_risk(llm, texto, detect_red_flags(texto), previo)
            except LimiteDeTasa as e:
                if intento == 0:
                    print(f"  (límite del gateway: espero {e.espera:.0f} s)")
                    await asyncio.sleep(e.espera + 1)
                    continue
                ra = None
            except LLMError as e:
                print(f"  [X] {texto[:50]}: {str(e)[:80]}")
                ra = None
            if ra is None:
                sin_respuesta.append(texto)
                return None
            latencias.append((time.perf_counter() - t0) * 1000)
            tokens.append((uso["input_tokens"], uso["output_tokens"]))
            return ra.risk
        return None

    try:
        print(f"\nJuez: {llm.modelo}\n")
        print("== Los diez del proyecto original (llama3.2:3b sacó 9/10) ==")
        aciertos = 0
        for texto, esperado in ORIGINALES:
            r = await juzgar(texto)
            if r is None:
                continue
            escala = esperado in ("high", "critical")
            ok = ge(r, esperado) if escala else not ge(r, "high")
            aciertos += ok
            if not ok:
                (falsos_negativos if escala else de_mas).append(texto)
            print(f"{OK if ok else FALLA} {r:<9} (esperado {esperado:<9}) {texto}")
        print(f"  {aciertos}/{len(ORIGINALES)}")

        print("\n== Donde las reglas no ven: el juez es la única red ==")
        for texto, minimo in DONDE_LAS_REGLAS_NO_VEN:
            r = await juzgar(texto)
            if r is None:
                continue
            ok = ge(r, minimo)
            if not ok:
                falsos_negativos.append(texto)
            print(f"{OK if ok else FALLA} {r:<9} (mínimo {minimo:<8}) {texto}")

        print("\n== Benignas: no deberían escalar ==")
        reparto: Counter[str] = Counter()
        for texto in BENIGNAS:
            r = await juzgar(texto)
            if r is None:
                continue
            reparto[r] += 1
            ok = not ge(r, "high")
            if not ok:
                de_mas.append(texto)
            print(f"{OK if ok else AVISO} {r:<9} {texto}")
        print("  reparto: " + "  ".join(f"{s}={reparto[s]}" for s in SEVERITY_ORDER))

        print("\n== Frases partidas en dos turnos ==")
        for previo, texto, minimo in PARTIDAS:
            r = await juzgar(texto, previo)
            if r is None:
                continue
            if minimo is None:
                print(f"{AVISO} {r:<9} (observación)     «{previo}» + «{texto}»")
                continue
            ok = ge(r, minimo)
            if not ok:
                falsos_negativos.append(f"{previo} / {texto}")
            print(f"{OK if ok else FALLA} {r:<9} (mínimo {minimo:<8}) «{previo}» + «{texto}»")
    finally:
        await llm.aclose()

    if latencias:
        orden = sorted(latencias)
        p95 = orden[min(len(orden) - 1, round(0.95 * (len(orden) - 1)))]
        print(f"\nLatencia del juez: mediana {statistics.median(latencias):.0f} ms · "
              f"p95 {p95:.0f} ms · {len(latencias)} llamadas")
        print(f"Tokens por llamada: {statistics.median(t[0] for t in tokens):.0f} entrada / "
              f"{statistics.median(t[1] for t in tokens):.0f} salida")

    # Un caso sin respuesta no es acierto ni fallo del juez: no se sabe qué
    # habría dicho. Se descuenta del total y se dice cuántos fueron.
    deben = [t for t, e in ORIGINALES if e in ("high", "critical")] \
        + [t for t, _ in DONDE_LAS_REGLAS_NO_VEN] + [t for _, t, m in PARTIDAS if m]
    evaluados = [t for t in deben if t not in sin_respuesta]
    print(f"\nFalsos negativos: {len(evaluados) - len(falsos_negativos)}/{len(evaluados)} "
          f"escalados correctamente.")
    for t in falsos_negativos:
        print(f"    - {t}")
    print(f"Alarmas de más: {len(de_mas)} (observación).")
    for t in de_mas:
        print(f"    - {t}")
    if sin_respuesta:
        print(f"Sin respuesta del modelo: {len(sin_respuesta)} — el resultado está incompleto.")
        for t in sin_respuesta:
            print(f"    - {t}")
    return 1 if falsos_negativos or sin_respuesta else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
