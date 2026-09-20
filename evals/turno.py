"""La lógica del turno, con un modelo de mentira (0 tokens, sin red).

    uv run python -m evals.turno

Con el modelo real las respuestas no son deterministas, así que lo que se prueba
aquí no es qué dice Vera sino **por qué ruta sale el turno**: que una emergencia
la escriba el código sin esperar al modelo, que un modelo caído no deje al
paciente en silencio, que el juez caído no anule la decisión, y que el juez solo
no mande a nadie a urgencias. Esas son las garantías; lo que diga el modelo
dentro de ellas se mide aparte, con el modelo real.
"""
from __future__ import annotations

import asyncio
import sys

from server.conocimiento.indice import Fragmento
from server.conocimiento.recuperacion import Cita, Recuperado
from server.dialogo.prompts import (
    DEGRADADO,
    DEGRADADO_CON_ALARMA,
    SIN_CONTEXTO,
    SIN_EVIDENCIA,
    SIN_INFORMACION,
    SIN_RESPUESTA,
)
from server.dialogo.turno import INTERCAMBIOS, OBJETIVO_ALARMA, Conversacion
from server.modelo.llm import LLMError
from server.seguridad.esquemas import RiskAssessment
from server.seguridad.respuestas import ACOMPANAR, EMERGENCIA

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {nombre}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(ok)


class ModeloDeMentira:
    """Responde lo que se le diga, y cuenta cuántas veces lo invocaron."""

    def __init__(self, respuesta="Qué bueno que pudo caminar. ¿Cómo ha estado la herida hoy?",
                 riesgo="none", falla_respuesta=False, falla_juez=False, pausa=0.0,
                 citas=()):
        self.respuesta, self.riesgo, self.pausa = respuesta, riesgo, pausa
        self.falla_respuesta, self.falla_juez = falla_respuesta, falla_juez
        # Lo que el modelo declara en su campo de citas. Va aparte de la
        # respuesta porque el turno lo lee ANTES de la primera palabra: la
        # salida estructurada respeta el orden del esquema.
        self.citas = list(citas)
        self.respuestas_pedidas = 0
        self.ultimo_prompt = ""
        self.ultimo_previo_al_juez: str | None = None

    async def astructured_stream(self, system, user, schema, max_tokens=260,
                                 temperatura=0.3, historial=None):
        self.respuestas_pedidas += 1
        self.ultimo_prompt = user
        if self.falla_respuesta:
            raise LLMError("el gateway no responde")
        yield "grounding", {"citas": self.citas}
        # Llega a pedazos, como del gateway. `pausa` sirve para que un turno dure
        # lo suficiente como para poder interrumpirlo.
        for i in range(0, len(self.respuesta), 7):
            if self.pausa:
                await asyncio.sleep(self.pausa)
            yield "delta", self.respuesta[i:i + 7]
        uso = {"input_tokens": 100, "output_tokens": 20}
        yield "final", (schema(citas=self.citas, utterance=self.respuesta), uso)

    async def structured(self, system, user, schema, max_tokens=400, temperatura=0.0):
        self.ultimo_previo_al_juez = user.split("TURNO ANTERIOR DEL PACIENTE: ")[1].split("\n")[0] \
            if "TURNO ANTERIOR" in user else None
        if self.falla_juez:
            raise LLMError("el juez no responde")
        return RiskAssessment(risk=self.riesgo, rationale="valoración de prueba"), \
            {"input_tokens": 600, "output_tokens": 50}


class RecuperadorDeMentira:
    """Devuelve siempre los mismos tres fragmentos, con el veredicto que se le pida.

    Lo que se prueba aquí no es la recuperación —eso lo mide `evals.conocimiento`
    contra el corpus real y su modelo de embeddings— sino **qué hace el turno con
    lo que le entreguen**. Separarlos deja este arnés en cero tokens, cero red y
    sin cargar dos gigas de modelo para comprobar un `if`.
    """

    def __init__(self, hay_evidencia: bool):
        self.hay_evidencia = hay_evidencia
        self.consultas = 0
        self.citas = [
            Cita(Fragmento(0, "plan.md", "Plan de egreso", "Baño",
                           "Puede ducharse a partir del tercer día después de la cirugía."),
                 0.85, 0.0, 0.03),
            Cita(Fragmento(1, "fiebre.md", "Fiebre", "Fiebre",
                           "Llame el mismo día si presenta fiebre de 38 grados o más."),
                 0.83, 0.0, 0.02),
            Cita(Fragmento(2, "dolor.md", "Dolor", "Dolor",
                           "El dolor es una señal que envía el sistema nervioso."),
                 0.80, 0.0, 0.01),
        ]

    def consultar(self, texto: str):
        self.consultas += 1
        return Recuperado(citas=list(self.citas), hay_evidencia=self.hay_evidencia,
                          max_denso=0.85 if self.hay_evidencia else 0.79,
                          solape_lexico=0)


async def turno(conv: Conversacion, texto: str) -> tuple[list[str], object]:
    frases, final = [], None
    async for tipo, dato in conv.turno(texto):
        if tipo == "speak":
            frases.append(dato)
        else:
            final = dato
    return frases, final


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n== Una emergencia la escribe el código ==")
    m = ModeloDeMentira()
    frases, t = await turno(Conversacion(m), "me duele el pecho y no me entra el aire")
    check("se dice el texto fijo de emergencia", frases == [EMERGENCIA], str(frases))
    check("sin pedirle respuesta al modelo", m.respuestas_pedidas == 0)
    check("y queda como escrito por el código",
          t.redactado_por == "codigo" and t.marca == "emergencia")
    check("la decisión es emergencia", t.decision.action == "emergency", t.decision.action)
    frases, t = await turno(Conversacion(ModeloDeMentira()), "ya no quiero vivir más")
    check("ante ideación, se acompaña", frases == [ACOMPANAR], str(frases))

    print("\n== Lo normal: el modelo responde y se dice frase a frase ==")
    m = ModeloDeMentira()
    frases, t = await turno(Conversacion(m), "me duele un poco la herida pero ya caminé")
    check("sale en dos frases, en orden",
          frases == ["Qué bueno que pudo caminar.", "¿Cómo ha estado la herida hoy?"], str(frases))
    check("escrito por el modelo", t.redactado_por == "modelo" and t.marca == "ok")
    check("se mide cuándo salió la primera frase", "primera_frase_ms" in t.latencia_ms)
    check("el uso suma respuesta y juez", t.usage == {"input_tokens": 700, "output_tokens": 70},
          str(t.usage))

    print("\n== El objetivo del turno lo fija el código ==")
    m = ModeloDeMentira()
    await turno(Conversacion(m), "tengo fiebre de 39 grados")
    check("con una alarma alta, se le pide encaminar al equipo", OBJETIVO_ALARMA in m.ultimo_prompt)

    print("\n== El modelo caído no deja al paciente en silencio ==")
    frases, t = await turno(Conversacion(ModeloDeMentira(falla_respuesta=True)), "hola, todo bien")
    check("se dice el respaldo", frases == [DEGRADADO], str(frases))
    check("marcado como degradado",
          t.marca == "degradado_sin_modelo" and t.redactado_por == "codigo")
    frases, t = await turno(Conversacion(ModeloDeMentira(falla_respuesta=True)),
                            "tengo fiebre de 39 grados")
    check("con alarma, el respaldo dice que se comunique ya", frases == [DEGRADADO_CON_ALARMA])
    check("y la decisión sale igual, con las reglas",
          t.decision.action == "escalate" and t.decision.source == "rules",
          f"{t.decision.action}/{t.decision.source}")

    print("\n== Un modelo que no dice nada ==")
    frases, t = await turno(Conversacion(ModeloDeMentira(respuesta="")), "ya comí algo")
    check("se dice el respaldo en vez de callar", frases == [SIN_RESPUESTA], str(frases))
    check("marcado", t.marca == "respuesta_vacia" and t.redactado_por == "codigo")

    print("\n== El juez caído no anula la decisión ==")
    frases, t = await turno(Conversacion(ModeloDeMentira(falla_juez=True)),
                            "estoy botando materia por la herida")
    check("el paciente oye la respuesta igual", len(frases) == 2, str(frases))
    check("y las reglas deciden solas", t.decision.risk == "high" and t.decision.source == "rules",
          f"{t.decision.risk}/{t.decision.source}")

    print("\n== El juez escala, pero solo no manda a urgencias ==")
    frases, t = await turno(Conversacion(ModeloDeMentira(riesgo="high")),
                            "siento como una presión aquí que me agarra")
    check("donde las reglas no ven, escala el juez",
          t.decision.action == "escalate" and t.decision.source == "llm",
          f"{t.decision.action}/{t.decision.source}")
    frases, t = await turno(Conversacion(ModeloDeMentira(riesgo="critical")),
                            "¿me puedo hacer un tatuaje?")
    check("un critical del juez sin reglas se queda en escalar", t.decision.action == "escalate",
          t.decision.action)

    print("\n== Lo que pasa de un turno al siguiente ==")
    m = ModeloDeMentira()
    conv = Conversacion(m)
    for i in range(INTERCAMBIOS + 4):
        await turno(conv, f"turno {i}")
    check("el historial se recorta y no crece sin límite",
          len(conv.historial) == 2 * INTERCAMBIOS, str(len(conv.historial)))
    check("y el más reciente es el último",
          conv.historial[-2]["content"] == f"turno {INTERCAMBIOS + 3}")
    check("el juez recibe el turno anterior",
          m.ultimo_previo_al_juez == f"turno {INTERCAMBIOS + 2}",
          str(m.ultimo_previo_al_juez))

    # Pero no el que ya escaló: ya se valoró, y dárselo hacía que el juez volviera
    # a escalar el turno siguiente por lo dicho antes.
    m = ModeloDeMentira()
    conv = Conversacion(m)
    await turno(conv, "me duele el pecho y no me entra el aire")
    await turno(conv, "¿y puedo tomar el doble de tramadol?")
    check("el turno que escaló no se le vuelve a mostrar al juez",
          m.ultimo_previo_al_juez is None, str(m.ultimo_previo_al_juez))
    m = ModeloDeMentira()
    conv = Conversacion(m)
    await turno(conv, "Te cuento que me duele el brazo, ¿cierto?")
    await turno(conv, "Y se me pasa como al lado izquierdo el brazo.")
    check("el que no escaló sí, para completar la frase partida",
          m.ultimo_previo_al_juez == "Te cuento que me duele el brazo, ¿cierto?",
          str(m.ultimo_previo_al_juez))

    print("\n== Un turno interrumpido antes de decir nada ==")
    conv = Conversacion(ModeloDeMentira(respuesta="Tarda en salir.", pausa=0.05))
    tarea = asyncio.create_task(turno(conv, "me duele la herida"))
    await asyncio.sleep(0.02)
    tarea.cancel()
    t = await conv.cerrar_interrumpido()
    check("se cierra igual, con la valoración del juez",
          t is not None and t.marca == "interrumpido", str(t and t.marca))
    check("y no mete en el historial un mensaje vacío, que el modelo rechaza",
          all(m["content"].strip() for m in conv.historial), str(conv.historial))

    print("\n== El conocimiento va antes del modelo, y el código decide si alcanza ==")
    # Con evidencia: los fragmentos entran al prompt y la cita se resuelve.
    m = ModeloDeMentira(respuesta="Puede ducharse a partir del tercer día.", citas=[1])
    conv = Conversacion(m, recuperador=RecuperadorDeMentira(hay_evidencia=True))
    frases, t = await turno(conv, "¿cuándo me puedo bañar?")
    check("el fragmento le llega al modelo como CONTEXTO", "CONTEXTO:" in m.ultimo_prompt)
    check("numerado por posición y no por id del índice", "[1] (" in m.ultimo_prompt,
          m.ultimo_prompt[:120])
    check("la cita queda resuelta al documento",
          [c.fragmento.documento for c in t.citas] == ["plan.md"],
          str([c.fragmento.documento for c in t.citas]))
    check("y el turno queda marcado como respaldado", t.hubo_evidencia)

    # Sin evidencia y con una pregunta: el modelo no llega a verla. Es la
    # diferencia entre pedirle que se abstenga y no darle la ocasión de no
    # hacerlo, que está medida: con fragmentos delante afirma sobre ellos igual.
    m = ModeloDeMentira()
    conv = Conversacion(m, recuperador=RecuperadorDeMentira(hay_evidencia=False))
    frases, t = await turno(conv, "¿puedo tomar cerveza?")
    check("una pregunta sin evidencia la responde el código",
          frases == [SIN_INFORMACION], str(frases))
    check("sin invocar al modelo", m.respuestas_pedidas == 0)
    check("queda marcada como sin evidencia",
          t.marca == "sin_evidencia" and t.redactado_por == "codigo", t.marca)
    check("y sin citas, porque no afirmó nada", t.citas == [])

    # Una alarma y una pregunta caben en el mismo turno. Ahí la abstención sería
    # la peor respuesta: deja al paciente con una infección y una nota
    # administrativa. Escalar no depende de tener documentos.
    m = ModeloDeMentira(respuesta="Eso necesita que hable hoy con su equipo clínico.")
    conv = Conversacion(m, recuperador=RecuperadorDeMentira(hay_evidencia=False))
    frases, t = await turno(conv, "se me puso la herida roja y con pus, ¿eso es normal?")
    check("con un signo de alarma NO se abstiene", frases != [SIN_INFORMACION], str(frases))
    check("y el objetivo del turno sigue siendo encaminarlo al equipo",
          OBJETIVO_ALARMA in m.ultimo_prompt)
    check("pero se le prohíbe apoyarse en un contexto que no responde",
          SIN_EVIDENCIA in m.ultimo_prompt)

    # Lo que NO es pregunta ni dispara reglas no paga la recuperación: el
    # embedding de la consulta va en la ruta crítica del turno.
    rec = RecuperadorDeMentira(hay_evidencia=True)
    conv = Conversacion(ModeloDeMentira(), recuperador=rec)
    await turno(conv, "sí, listo")
    check("un turno que no pregunta nada no consulta el índice", rec.consultas == 0,
          str(rec.consultas))
    rec2 = RecuperadorDeMentira(hay_evidencia=True)
    conv = Conversacion(ModeloDeMentira(), recuperador=rec2)
    await turno(conv, "la herida está botando materia")
    check("un signo de alarma sí lo consulta, aunque no pregunte", rec2.consultas == 1)

    print("\n== Una cita que el modelo se inventa no es una cita ==")
    m = ModeloDeMentira(respuesta="Eso lo tiene que ver su equipo.", citas=[9])
    conv = Conversacion(m, recuperador=RecuperadorDeMentira(hay_evidencia=True))
    frases, t = await turno(conv, "¿cuándo me quitan los puntos?")
    check("citar un fragmento que no se le mostró no cuenta", t.citas == [],
          str([c.fragmento.documento for c in t.citas]))

    m = ModeloDeMentira(respuesta="Puede ducharse desde el tercer día (2).", citas=[])
    conv = Conversacion(m, recuperador=RecuperadorDeMentira(hay_evidencia=True))
    frases, t = await turno(conv, "¿cuándo me puedo bañar?")
    check("la marca escrita dentro del texto sí vale",
          [c.fragmento.documento for c in t.citas] == ["fiebre.md"],
          str([c.fragmento.documento for c in t.citas]))
    check("y no se oye en voz alta",
          frases == ["Puede ducharse desde el tercer día."], str(frases))

    print("\n== Sin conocimiento, la llamada sigue ==")
    m = ModeloDeMentira()
    frases, t = await turno(Conversacion(m, recuperador=None), "¿cuándo me puedo bañar?")
    check("el modelo responde sin contexto clínico", "CONTEXTO:" not in m.ultimo_prompt)
    check("y se le prohíbe afirmar nada", SIN_CONTEXTO in m.ultimo_prompt)
    check("el turno queda sin citas y sin evidencia",
          t.citas == [] and not t.hubo_evidencia)

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones del turno.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
