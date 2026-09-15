"""La decisión es el máximo de las dos capas, y sobrevive al modelo caído.

    uv run python -m evals.decision_seguridad

Lo que se prueba aquí no es cada capa por separado —eso lo hacen `alarmas_base`
y `alarmas_adversariales`— sino **la regla de combinación**, que es donde vive la
promesa de seguridad del sistema:

- Si cualquiera de las dos ve riesgo, la decisión lo refleja.
- Si el juez no responde, la decisión sigue saliendo con lo que vieron las
  reglas. Escalar no puede quedar condicionado a que el modelo esté disponible.
- Queda constancia de **cuál capa lo decidió**, porque una decisión clínica que
  no se puede explicar no sirve para auditarla después.

No invoca al modelo: la valoración del juez se pasa como dato.
"""
from __future__ import annotations

import sys

from server.seguridad.esquemas import RiskAssessment
from server.seguridad.juez import combinar
from server.seguridad.reglas import detect_red_flags

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []


def check(label: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {label}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(bool(ok))


def juez(risk: str, motivo: str = "valoración del modelo") -> RiskAssessment:
    return RiskAssessment(risk=risk, rationale=motivo)


def _cap_del_juez() -> None:
    """El juez SOLO no manda a nadie a urgencias.

    `critical` lleva a `emergency`, que es la acción más drástica del agente. Un
    barrido de 30 conversaciones dio doce turnos en `critical`: cinco los
    sostenían las reglas, cinco elevaban algo que sí había disparado, y **dos
    salieron de la nada** —«¿me puedo tomar una cerveza con el remedio?» y «¿me
    puedo hacer un tatuaje ahora?»—. El código se abstuvo bien en las dos y la
    llamada quedó marcada como emergencia igual.

    El tope es a `high`: el equipo clínico sigue recibiendo la alerta, así que no
    se pierde señal. Solo se le niega al modelo mandar a urgencias sin que una
    sola regla lo respalde.
    """
    print("\n== El juez solo no declara una emergencia ==")
    for texto in ("¿me puedo tomar una cerveza con el remedio?",
                  "¿me puedo hacer un tatuaje ahora?",
                  "¿cuál es la clave del wifi?"):
        flags = detect_red_flags(texto)
        d = combinar(flags, RiskAssessment(risk="critical", rationale="alucinada"))
        check(f"sin reglas, «{texto[:34]}» no llega a urgencias",
              d.risk == "high" and d.action == "escalate", f"{d.risk}/{d.action}")

    # Pero no pierde sensibilidad: sigue escalando, que es lo que avisa al equipo.
    d = combinar([], RiskAssessment(risk="high", rationale="x"))
    check("y con `high` escala igual que siempre",
          d.action == "escalate", f"{d.risk}/{d.action}")

    # Y en cuanto una regla dispara —aunque sea moderate—, el juez puede subir a
    # critical como siempre: el tope es solo para el turno sin ninguna señal.
    flags = detect_red_flags("se me puso la piel amarilla y los ojos amarillos")
    d = combinar(flags, RiskAssessment(risk="critical", rationale="x"))
    check("con una regla disparada, el juez sí puede llegar a critical",
          d.risk == "critical" and d.action == "emergency", f"{d.risk}/{d.action}")

    # Y lo crítico de verdad no se toca: ahí las reglas ya lo sostienen solas.
    for texto in ("no puedo respirar bien y me falta el aire",
                  "me duele el pecho y el dolor me sube hacia el brazo",
                  "me desmayé esta mañana y me caí al piso"):
        d = combinar(detect_red_flags(texto), None)
        check(f"«{texto[:34]}» sigue siendo emergencia sin juez",
              d.risk == "critical" and d.action == "emergency", f"{d.risk}/{d.action}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n== Gana la capa más conservadora ==")
    casos = [
        # (texto, riesgo del juez, riesgo esperado, origen esperado)
        ("tengo fiebre de 39 grados", "none", "high", "rules"),
        ("me duele un poquito la herida", "high", "high", "llm"),
        ("sale pus con mal olor de la herida", "high", "high", "both"),
        ("todo bien, muchas gracias", "none", "none", "none"),
        ("me duele mucho el pecho", "moderate", "critical", "rules"),
    ]
    for texto, r_juez, esperado, origen in casos:
        d = combinar(detect_red_flags(texto), juez(r_juez))
        ok = d.risk == esperado and d.source == origen
        check(f"[{esperado:<8} · {origen:<5}] {texto[:44]}", ok,
              f"obtuvo riesgo={d.risk} origen={d.source}")

    print("\n== El escalamiento no depende de que el modelo responda ==")
    d = combinar(detect_red_flags("tengo fiebre de 39 y sale pus de la herida"), None)
    check("sin juez, las reglas deciden solas", d.risk == "high" and d.source == "rules",
          f"riesgo={d.risk} origen={d.source}")
    check("y la acción es escalar", d.action == "escalate", d.action)
    check("con la evidencia de qué reglas dispararon",
          "fiebre" in d.rule_flags and "infeccion" in d.rule_flags, str(d.rule_flags))

    d = combinar([], None)
    check("sin juez y sin señales, no se inventa riesgo",
          d.risk == "none" and d.action == "continue", f"{d.risk}/{d.action}")

    print("\n== La acción se deriva del riesgo, sin excepciones ==")
    # Con una regla disparada, para que la tabla se pruebe entera: el juez SOLO
    # ya no puede llegar a `critical` (ver `_cap_del_juez`), así que ejercitar
    # esta correspondencia sin reglas dejaría de medir la tabla y pasaría a medir
    # el tope. Se usa un síntoma `moderate` —una molestia—, de modo que el nivel
    # final lo sigue fijando el juez en todos los escalones menos el primero.
    con_regla = detect_red_flags("estoy muy maluco")
    for riesgo, accion in (("none", "advise"), ("low", "advise"), ("moderate", "advise"),
                           ("high", "escalate"), ("critical", "emergency")):
        d = combinar(con_regla, juez(riesgo))
        check(f"{riesgo:<9} -> {accion}", d.action == accion, d.action)

    print("\n== La justificación queda escrita, no implícita ==")
    d = combinar(detect_red_flags("me cuesta respirar"), juez("critical", "disnea aguda"))
    check("menciona las reglas que dispararon", "reglas:" in d.rationale, d.rationale)
    check("y el motivo del juez", "disnea aguda" in d.rationale, d.rationale)

    _cap_del_juez()

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones pasaron.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
