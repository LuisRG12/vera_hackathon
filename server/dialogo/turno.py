"""Un turno de Vera: del texto del paciente a lo que se le dice, con la seguridad al lado.

El orden es la promesa del sistema:

1. Las reglas leen el texto. No invocan ningún modelo.
2. El juez arranca en paralelo. No suma espera: su valoración llega mientras se
   habla, y si no llega, la decisión sale igual con las reglas.
3. Si las reglas ven una emergencia, lo que se dice lo escribe el código, al
   instante y sin generar nada.
4. Se recuperan los fragmentos del corpus, y **el código decide** si constituyen
   evidencia. Si el paciente preguntó algo que el corpus no responde, la
   respuesta también la escribe el código: el modelo no llega a verla.
5. Si no, el modelo responde en streaming y se dice frase a frase.
6. Si el modelo falla, un texto de respaldo escrito por el código. Si no dijo
   nada, otro: callar no es una respuesta en una llamada.
7. La cita se verifica contra los fragmentos que se le mostraron en este turno.
8. Al final se combinan las dos capas.

Cuando solo el juez ve el riesgo, la respuesta ya se estaba generando sin saberlo:
lo que corre en paralelo no puede cambiar lo que ya se dijo. Por eso la alerta al
equipo sale de la decisión combinada y no de lo que Vera dijo, y el prompt le pide
al modelo que ante un signo de alarma encamine al equipo por su cuenta.

Es la forma de `DialogueManager.stream_turn` en vera_voice_agent, sin lo que
depende de etapas que todavía no están: el estado de la llamada y las preguntas
de seguimiento.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from server.config import settings
from server.conocimiento.citas import derivar, limpiar
from server.conocimiento.recuperacion import Cita, formatear
from server.dialogo.pregunta import es_pregunta
from server.dialogo.prompts import (
    CON_EVIDENCIA,
    DEGRADADO,
    DEGRADADO_CON_ALARMA,
    RESPONDER_SYSTEM,
    SIN_CONTEXTO,
    SIN_EVIDENCIA,
    SIN_INFORMACION,
    SIN_RESPUESTA,
)
from server.modelo.flujo import SentenceSplitter
from server.modelo.llm import StructuredLLM
from server.seguridad.esquemas import RiskAssessment, SafetyDecision
from server.seguridad.juez import assess_risk, combinar
from server.seguridad.reglas import detect_red_flags, max_severity
from server.seguridad.respuestas import ACOMPANAR, EMERGENCIA

# El objetivo del turno lo fija el código, no lo elige el modelo.
OBJETIVO_NORMAL = "reconocer lo que dijo y dar seguimiento a cómo se siente"
# «Sin ofrecerle ayuda» porque con este objetivo el modelo tiende a ofrecerla
# —«¿necesita que le ayude a contactarlos?»—, y Vera no tiene cómo cumplir.
OBJETIVO_ALARMA = ("pedirle al paciente que contacte hoy mismo a su equipo clínico, "
                   "sin ofrecerle ayuda para hacerlo")

# Intercambios recientes que ve el modelo para no perder el hilo. Ocho, no tres:
# con tres, en una llamada de quince turnos Vera ya no se acuerda de la fiebre
# que el paciente le contó al principio. El proyecto original compensaba eso con
# un resumen del estado de la llamada que aquí todavía no existe, y mientras
# tanto lo barato es darle más historia: cada intercambio son unos sesenta
# tokens, que no se notan ni en el costo ni en el tiempo hasta la primera frase.
INTERCAMBIOS = 8


class RespuestaVera(BaseModel):
    """Lo que devuelve el modelo.

    `citas` va **antes** de `utterance` y no es cosmética: la salida estructurada
    respeta el orden del esquema, así que las citas llegan completas antes de que
    empiece a sonar la primera palabra. Si fueran después, la primera frase ya
    estaría en el parlante cuando se sepa con qué la respaldó.

    Los números son **posiciones dentro del turno** —1, 2, 3— y no ids del
    índice, para que el esquema sea el mismo en todos los turnos: Claude compila
    cada esquema nuevo y eso está medido en ~0,9 s de más en la primera frase.
    Que sean posiciones válidas lo comprueba el código, no el esquema.
    """

    citas: list[int] = Field(
        description="Números de los fragmentos del CONTEXTO que respaldan la "
                    "respuesta. Vacío si no usaste ninguno.")
    utterance: str = Field(description="Lo que se le dice al paciente, breve y claro.")


@dataclass
class EnCurso:
    """El turno que se está generando, por si lo interrumpen a la mitad."""

    texto: str
    flags: list
    juez: asyncio.Task
    dichas: list[str]
    t0: float
    citas: list = field(default_factory=list)
    marcas: list[int] = field(default_factory=list)
    hubo_evidencia: bool = False


@dataclass
class TurnoVera:
    utterance: str
    decision: SafetyDecision
    # Quién escribió lo que se dijo: `modelo` o `codigo`. Es la primera pregunta
    # de cualquier auditoría clínica, y sin el campo habría que deducirla.
    redactado_por: str
    # ok | emergencia | degradado_sin_modelo | respuesta_vacia | sin_evidencia
    marca: str = "ok"
    usage: dict = field(default_factory=dict)
    latencia_ms: dict = field(default_factory=dict)
    # Los fragmentos que respaldan lo dicho, ya verificados contra lo que se le
    # mostró al modelo en este turno. Lista vacía significa que no hay respaldo:
    # o el turno no afirmó nada clínico, o afirmó algo que no se pudo atribuir a
    # ninguna fuente. Las dos cosas hay que poder distinguirlas al auditar, y por
    # eso está también `hubo_evidencia`.
    citas: list[Cita] = field(default_factory=list)
    hubo_evidencia: bool = False


def _ms(desde: float) -> int:
    return round((time.perf_counter() - desde) * 1000)


class Conversacion:
    """Una por llamada: lleva lo poco que un turno necesita del anterior."""

    def __init__(self, llm: StructuredLLM, apertura: str | None = None,
                 recuperador=None):
        self.llm = llm
        # Sin recuperador, Vera funciona igual pero sin poder afirmar nada
        # clínico: es la misma degradación que ya tiene definida para el oído y
        # la voz, y la que corre en los arneses que no quieren cargar el modelo
        # de embeddings para probar otra cosa.
        self.rec = recuperador
        self.historial: list[dict] = []
        # Lo que Vera ya dijo al contestar, si lo dijo. El modelo no lo escribió
        # —es texto fijo—, así que sin esto no sabe que ya se presentó y vuelve a
        # saludar o a preguntar lo mismo.
        self.apertura = apertura
        self._en_curso: EnCurso | None = None
        # Lo que el juez ve del turno anterior. Solo si ese turno NO escaló: el
        # turno anterior está para completar una frase que el reconocedor partió
        # —«me duele el brazo, ¿cierto?» + «se me pasa al lado izquierdo»—, y lo
        # que ya escaló no hay que completarlo: ya se valoró y ya se avisó.
        #
        # Se probó pedírselo al juez por instrucción y no bastó. Con una
        # emergencia en el turno anterior, a «el doctor me dijo que puedo tomar
        # el doble de tramadol» le dio `critical`, y a «¿me puedo bañar?» tras una
        # fiebre, `high`, ambos por lo dicho antes. Ver evals/juez.py.
        self._previo: str | None = None

    async def turno(self, texto: str):
        """Genera ("speak", frase) mientras se habla y termina con ("turn", TurnoVera)."""
        t0 = time.perf_counter()
        lat: dict = {}
        flags = detect_red_flags(texto)
        severidad = max_severity(flags)

        async def juez_medido() -> tuple[RiskAssessment, dict]:
            r = await assess_risk(self.llm, texto, flags, self._previo)
            lat["juez_ms"] = _ms(t0)
            return r

        juez = asyncio.create_task(juez_medido())
        # Si el juez falla y nadie llega a esperarlo —la ruta degradada lo
        # cancela—, asyncio ensucia el log con un error que no lo es.
        juez.add_done_callback(lambda t: t.cancelled() or t.exception())
        dichas: list[str] = []
        self._en_curso = EnCurso(texto, flags, juez, dichas, t0)

        # Emergencia: la escribe el código. Ver server/seguridad/respuestas.py
        # por qué ante un crítico no se deja al modelo elegir las palabras.
        if severidad == "critical":
            ideacion = any(f.name == "ideacion_suicida" for f in flags)
            respuesta = ACOMPANAR if ideacion else EMERGENCIA
            lat["primera_frase_ms"] = _ms(t0)
            dichas.append(respuesta)
            yield "speak", respuesta
            ra, uso = await _esperar(juez)
            yield "turn", self._cerrar(texto, flags, ra, respuesta, "codigo", "emergencia",
                                       uso, lat, t0)
            return

        # El conocimiento, antes del modelo. Va en un hilo porque es CPU —el
        # embedding de la consulta y BM25— y el servidor entero es asíncrono:
        # sin esto, el turno bloquearía el bucle que está reproduciendo audio.
        recuperado = None
        if self.rec is not None and (flags or es_pregunta(texto)):
            recuperado = await asyncio.to_thread(self.rec.consultar, texto)
            lat["recuperacion_ms"] = _ms(t0)
        # Se recuperan más de los que ve el modelo: recuperar de más ordena mejor
        # y es barato; mostrar de más son cientos de tokens en la ruta crítica.
        citas = list(recuperado.citas[:settings.k_evidencia]) if recuperado else []
        hay_evidencia = bool(recuperado and recuperado.hay_evidencia)
        self._en_curso.citas = citas
        self._en_curso.hubo_evidencia = hay_evidencia

        # Una pregunta que el corpus no responde NO llega al modelo. Es la
        # diferencia entre pedirle que se abstenga y no darle la oportunidad de
        # no hacerlo: con fragmentos delante y sin evidencia, está medido que
        # afirma sobre ellos igual. Lo que se dice aquí lo escribe el código.
        if recuperado is not None and not hay_evidencia and es_pregunta(texto):
            lat["primera_frase_ms"] = _ms(t0)
            dichas.append(SIN_INFORMACION)
            yield "speak", SIN_INFORMACION
            ra, uso = await _esperar(juez)
            yield "turn", self._cerrar(texto, flags, ra, SIN_INFORMACION, "codigo",
                                       "sin_evidencia", uso, lat, t0, [], False)
            return

        objetivo = OBJETIVO_ALARMA if severidad == "high" else OBJETIVO_NORMAL
        user = self._instruccion(texto, objetivo, citas, hay_evidencia)
        if self.apertura and not self.historial:
            user = f"VERA YA DIJO AL CONTESTAR LA LLAMADA: «{self.apertura}»\n\n{user}"
        partidor = SentenceSplitter()
        obj, uso_resp = None, {}
        declaradas: list[int] = []
        marcas = self._en_curso.marcas
        try:
            async for tipo, dato in self.llm.astructured_stream(
                    RESPONDER_SYSTEM, user, RespuestaVera, historial=self.historial):
                if tipo == "grounding":
                    # Los campos anteriores a `utterance`, completos antes de la
                    # primera palabra. Es el único momento en que se pueden leer
                    # sin esperar al final del turno.
                    declaradas = [n for n in (dato.get("citas") or []) if isinstance(n, int)]
                elif tipo == "delta":
                    for frase in partidor.push(dato):
                        # La limpieza va AQUÍ, frase a frase, y no al final: cada
                        # frase se sintetiza en cuanto está completa, así que
                        # limpiar después no llega a tiempo y el paciente oiría
                        # «abre paréntesis citation ids dos».
                        frase, ids = limpiar(frase)
                        marcas.extend(ids)
                        if not frase:
                            continue
                        lat.setdefault("primera_frase_ms", _ms(t0))
                        dichas.append(frase)
                        yield "speak", frase
                elif tipo == "final":
                    obj, uso_resp = dato
        except Exception as exc:  # noqa: BLE001 — el turno se degrada, no se cae
            # Si el gateway no responde, el juez tampoco va a responder: esperarlo
            # sería alargar un turno que ya falló. Las reglas ya decidieron.
            juez.cancel()
            print(f"[degradado] {type(exc).__name__}: {str(exc)[:300]}", flush=True)
            respuesta = DEGRADADO_CON_ALARMA if severidad == "high" else DEGRADADO
            lat.setdefault("primera_frase_ms", _ms(t0))
            yield "speak", respuesta
            yield "turn", self._cerrar(texto, flags, None, respuesta, "codigo",
                                       "degradado_sin_modelo", {}, lat, t0)
            return

        if resto := partidor.flush():
            resto, ids = limpiar(resto)
            marcas.extend(ids)
            if resto:
                lat.setdefault("primera_frase_ms", _ms(t0))
                dichas.append(resto)
                yield "speak", resto
        utterance = " ".join(dichas)
        if not utterance and obj:
            utterance, ids = limpiar(obj.utterance)
            marcas.extend(ids)
            if utterance:
                lat.setdefault("primera_frase_ms", _ms(t0))
                yield "speak", utterance
        redactado_por, marca = "modelo", "ok"
        if not utterance:
            utterance, redactado_por, marca = SIN_RESPUESTA, "codigo", "respuesta_vacia"
            lat.setdefault("primera_frase_ms", _ms(t0))
            yield "speak", utterance

        # La cita, ya dicho todo. Lo declarado manda; si no declaró, valen las
        # marcas que escribió dentro del texto; si tampoco, se atribuye por
        # solapamiento. Todo contra los fragmentos de ESTE turno.
        _, verificadas = derivar(utterance, citas, declaradas or marcas)

        ra, uso_juez = await _esperar(juez)
        uso = {k: uso_resp.get(k, 0) + uso_juez.get(k, 0)
               for k in ("input_tokens", "output_tokens")}
        yield "turn", self._cerrar(texto, flags, ra, utterance, redactado_por, marca,
                                   uso, lat, t0, verificadas, hay_evidencia)

    def _instruccion(self, texto: str, objetivo: str, citas, hay_evidencia: bool) -> str:
        """La instrucción del turno la fija el CÓDIGO, no la elige el modelo.

        Son tres situaciones y cada una necesita lo contrario de la otra, así que
        una sola instrucción fija se equivoca en dos de las tres. El porqué de
        cada una está junto a su texto en `prompts.py`.
        """
        if not citas:
            cierre = SIN_CONTEXTO
        elif hay_evidencia:
            cierre = CON_EVIDENCIA
        else:
            cierre = SIN_EVIDENCIA
        partes = []
        if citas:
            partes.append(f"CONTEXTO:\n{formatear(citas)}\n")
        partes += [f"OBJETIVO DE ESTE TURNO: {objetivo}\n", f"PACIENTE: {texto}\n", cierre]
        return "\n".join(partes)

    async def cerrar_interrumpido(self) -> TurnoVera | None:
        """Cierra el turno que el paciente interrumpió, con lo que se alcanzó a decir.

        **Cancelar la respuesta no puede cancelar la seguridad.** El juez de ese
        turno ya estaba en camino cuando el paciente volvió a hablar, así que se
        espera su valoración y la decisión sale igual. Sin esto, el turno
        interrumpido —que suele serlo porque el paciente tiene algo más que
        contar— sería el único de la llamada sin la segunda capa.
        """
        if self._en_curso is None:
            return None
        e, self._en_curso = self._en_curso, None
        ra, uso = await _esperar(e.juez)
        dicho = " ".join(e.dichas)
        # El turno se cortó a la mitad, así que el campo de citas del modelo no
        # llegó nunca: lo que hay son las marcas que alcanzó a escribir y las
        # palabras que alcanzó a decir. Media respuesta también se audita.
        _, verificadas = derivar(dicho, e.citas, e.marcas)
        return self._cerrar(e.texto, e.flags, ra, dicho, "modelo", "interrumpido",
                            uso, {}, e.t0, verificadas, e.hubo_evidencia)

    def _cerrar(self, texto, flags, ra, utterance, redactado_por, marca,
                uso, lat, t0, citas=(), hubo_evidencia=False) -> TurnoVera:
        """Punto único por el que pasan todas las rutas del turno."""
        self.historial.append({"role": "user", "content": texto})
        # Solo si Vera alcanzó a decir algo. Un turno interrumpido antes de la
        # primera frase deja la respuesta vacía, y un mensaje vacío en el
        # historial lo rechaza el modelo: el gateway devuelve un 500 y, como el
        # historial se arrastra, caían también los turnos siguientes. Se vio en
        # una llamada completa, después de interrumpir a Vera.
        if utterance.strip():
            self.historial.append({"role": "assistant", "content": utterance})
        self.historial = self.historial[-2 * INTERCAMBIOS:]
        self._en_curso = None
        decision = combinar(flags, ra)
        self._previo = None if decision.risk in ("high", "critical") else texto
        lat["total_ms"] = _ms(t0)
        return TurnoVera(utterance=utterance, decision=decision,
                         redactado_por=redactado_por, marca=marca,
                         usage=uso, latencia_ms=lat, citas=list(citas),
                         hubo_evidencia=hubo_evidencia)


async def _esperar(tarea) -> tuple[RiskAssessment | None, dict]:
    """Perder el juez degrada la evaluación, no la anula: la capa determinista
    ya decidió y su veredicto sigue en pie."""
    try:
        return await tarea
    except Exception:  # noqa: BLE001
        return None, {}
