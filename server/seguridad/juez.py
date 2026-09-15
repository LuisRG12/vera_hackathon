"""Decisión de seguridad: dos capas independientes, se queda la más conservadora.

- **Capa A — determinista** (`reglas.py`): léxico clínico colombiano con manejo
  de negación. No invoca al modelo.
- **Capa B — juez** (`assess_risk`): el modelo clasifica el riesgo de lo que dijo
  el paciente.

**Por qué dos y no una.** La capa A no entiende lo que no está en su léxico; con
voz real se vio que tampoco lo que llega con muletillas y frases partidas. La
capa B entiende, pero es un modelo y puede fallar o no responder. Que la decisión
sea el máximo de ambas significa que **el escalamiento no depende de que el
modelo acierte, ni de que esté disponible**: si el gateway se cae a mitad de
turno, la capa A ya evaluó y la alerta sale igual.

La asimetría es deliberada. En seguridad clínica un falso positivo cuesta una
alerta de más; un falso negativo, un paciente que no fue a urgencias.

**Procedencia.** Traído de `server/agent/seguridad.py` en vera_voice_agent. Lo que
cambió durante el reto: el juez es Claude Haiku por el gateway en vez de un
modelo local de 3B, es asíncrono, y puede ver el turno anterior del paciente.
"""
from __future__ import annotations

from server.modelo.llm import StructuredLLM
from server.seguridad.esquemas import RiskAssessment, SafetyDecision
from server.seguridad.reglas import ACTION_FOR, RuleFlag, max_sev, max_severity

JUEZ_SYSTEM = """Evalúas el riesgo clínico de lo que reporta un paciente recién operado.

- critical: emergencia inmediata (no puede respirar, dolor en el pecho, sangrado masivo, convulsión, desmayo).
- high: signo de alarma que requiere contacto clínico prioritario (fiebre igual o mayor a 38.5, sangrado abundante, pus o signos de infección, signos de trombosis).
- moderate: síntoma que amerita vigilancia y consejo; escalar si empeora.
- low: molestia esperable del postoperatorio.
- none: sin síntoma de riesgo.

Ante duda, SUBE el nivel; nunca lo bajes.

Si el analizador de texto no detectó ninguna señal, solo asigna high o critical cuando el paciente describa un síntoma concreto que el analizador pudo haberse perdido. Mencionar la cirugía, saludar o preguntar algo no es un síntoma.

Si aparece un TURNO ANTERIOR, es solo para entender la frase actual: el reconocedor de voz a veces parte en dos lo que el paciente dijo de corrido. Valora lo que el paciente reporta ahora, leído con ese contexto."""


async def assess_risk(llm: StructuredLLM, user_text: str,
                      flags: list[RuleFlag] | None = None,
                      previo: str | None = None) -> tuple[RiskAssessment, dict]:
    """Capa B. Devuelve (valoración, consumo de tokens).

    Se le dice al juez **qué vio la capa determinista**, y no por ahorrarle
    trabajo: sin ese dato clasificaba `high` un saludo —«me sacaron el apéndice
    hace dos días»—, visto en el registro de una llamada real del proyecto
    original. Una alerta por un saludo hace que el equipo empiece a ignorarlas, y
    una alerta que se ignora no sirve para nada. Saberlo no le impide escalar por
    su cuenta —para eso está—, pero le exige algo concreto que lo justifique.

    **El juez NO recibe la evidencia recuperada de los documentos.** En el
    proyecto original se le pasaba como «PROTOCOLO», y el efecto medido fue el
    contrario del buscado: el corpus está lleno de vocabulario de alarma
    —«consulte de inmediato si presenta fiebre igual o mayor a 38.5»— y el modelo
    lo leía como si lo hubiera dicho el paciente. Sobre doce frases benignas,
    ninguna quedó en `none` con el protocolo delante, y «¿me puedo hacer un
    tatuaje ahora?» pasó a `critical`. Su trabajo es valorar lo que dijo **el
    paciente**; los umbrales del protocolo ya están en el léxico, donde se
    pueden auditar.

    **El turno anterior sí.** Voz real, 13 de septiembre: el paciente dijo «me
    duele el pecho, ¿cierto? y se me pasa al brazo izquierdo», y el reconocedor lo
    entregó en dos turnos. Visto por separado, ninguno de los dos es la emergencia
    que es junto. Se le da al juez para entender la frase, no para reevaluar lo
    que ya se valoró.
    """
    detectado = ", ".join(sorted({f.name for f in flags})) if flags else "nada"
    partes = [f"SEÑALES QUE DETECTÓ EL ANALIZADOR DE TEXTO: {detectado}"]
    if previo:
        partes.append(f"TURNO ANTERIOR DEL PACIENTE: {previo}")
    partes.append(f"PACIENTE: {user_text}\n\nClasifica el riesgo.")
    # Con margen: una justificación legítimamente larga no debe cortar el JSON.
    return await llm.structured(JUEZ_SYSTEM, "\n\n".join(partes), RiskAssessment,
                                max_tokens=400, temperatura=0.0)


def combinar(flags: list[RuleFlag], ra: RiskAssessment | None) -> SafetyDecision:
    """El máximo de las dos capas, con constancia de cuál lo decidió.

    **Con una excepción: el juez solo no declara una emergencia.** `critical`
    lleva a `emergency`, que es decirle al paciente que acuda a urgencias ahora
    mismo, la acción más drástica que este agente puede tomar. Si NINGUNA regla
    disparó, esa decisión quedaría enteramente en manos de un modelo, sin nada que
    la corrobore.

    Medido en el proyecto original sobre un barrido de 30 conversaciones: de doce
    turnos que acabaron en `critical`, cinco los sostenían las reglas, cinco
    elevaban un `moderate`/`high` que sí había disparado, y **dos salieron de la
    nada**: «¿me puedo tomar una cerveza con el remedio?» y «¿me puedo hacer un
    tatuaje ahora?».

    El tope es a `high`, no a `none`: el juez conserva toda su sensibilidad para
    **escalar**, así que el equipo clínico sigue recibiendo la alerta. Lo único
    que se le niega es mandar a alguien a urgencias sin que una sola regla lo
    respalde. Si las reglas disparan cualquier cosa —aunque sea `moderate`—, el
    juez puede subirlo a `critical` como siempre.
    """
    sev_reglas = max_severity(flags)
    sev_juez = ra.risk if ra else "none"
    if not flags and sev_juez == "critical":
        sev_juez = "high"
    final = max_sev(sev_reglas, sev_juez)

    if sev_reglas == sev_juez == "none":
        origen = "none"
    elif sev_reglas == final and sev_juez == final:
        origen = "both"
    elif sev_reglas == final:
        origen = "rules"
    else:
        origen = "llm"

    motivos = []
    if flags:
        motivos.append("reglas: " + ", ".join(sorted({f.name for f in flags})))
    if ra and ra.rationale:
        motivos.append(f"juez: {ra.rationale}")

    return SafetyDecision(
        risk=final,
        action=ACTION_FOR[final],
        rationale=" · ".join(motivos) or "Sin señales de riesgo.",
        rule_flags=sorted({f.name for f in flags}),
        source=origen,
    )
