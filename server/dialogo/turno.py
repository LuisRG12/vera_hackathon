"""Un turno de Vera: del texto del paciente a lo que se le dice, con la seguridad al lado.

El orden es la promesa del sistema:

1. Las reglas leen el texto. No invocan ningún modelo.
2. El juez arranca en paralelo. No suma espera: su valoración llega mientras se
   habla, y si no llega, la decisión sale igual con las reglas.
3. Si las reglas ven una emergencia, lo que se dice lo escribe el código, al
   instante y sin generar nada.
4. Si no, el modelo responde en streaming y se dice frase a frase.
5. Si el modelo falla, un texto de respaldo escrito por el código. Si no dijo
   nada, otro: callar no es una respuesta en una llamada.
6. Al final se combinan las dos capas.

Cuando solo el juez ve el riesgo, la respuesta ya se estaba generando sin saberlo:
lo que corre en paralelo no puede cambiar lo que ya se dijo. Por eso la alerta al
equipo sale de la decisión combinada y no de lo que Vera dijo, y el prompt le pide
al modelo que ante un signo de alarma encamine al equipo por su cuenta.

Es la forma de `DialogueManager.stream_turn` en vera_voice_agent, sin lo que
depende de etapas que todavía no están: el estado de la llamada y las preguntas de
seguimiento, los documentos y las citas.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from server.dialogo.prompts import (
    DEGRADADO,
    DEGRADADO_CON_ALARMA,
    RESPONDER_SYSTEM,
    SIN_CONTEXTO,
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

# Intercambios recientes que ve el modelo para no perder el hilo. Pocos a
# propósito: cada uno se paga en tokens y en tiempo hasta la primera frase.
INTERCAMBIOS = 3


class RespuestaVera(BaseModel):
    """Lo que devuelve el modelo. Un solo campo por ahora; las citas llegan con
    los documentos, y van antes de `utterance` para estar completas antes de que
    empiece a sonar la primera palabra."""

    utterance: str = Field(description="Lo que se le dice al paciente, breve y claro.")


@dataclass
class TurnoVera:
    utterance: str
    decision: SafetyDecision
    # Quién escribió lo que se dijo: `modelo` o `codigo`. Es la primera pregunta
    # de cualquier auditoría clínica, y sin el campo habría que deducirla.
    redactado_por: str
    # ok | emergencia | degradado_sin_modelo | respuesta_vacia
    marca: str = "ok"
    usage: dict = field(default_factory=dict)
    latencia_ms: dict = field(default_factory=dict)


def _ms(desde: float) -> int:
    return round((time.perf_counter() - desde) * 1000)


class Conversacion:
    """Una por llamada: lleva lo poco que un turno necesita del anterior."""

    def __init__(self, llm: StructuredLLM, apertura: str | None = None):
        self.llm = llm
        self.historial: list[dict] = []
        # Lo que Vera ya dijo al contestar, si lo dijo. El modelo no lo escribió
        # —es texto fijo—, así que sin esto no sabe que ya se presentó y vuelve a
        # saludar o a preguntar lo mismo.
        self.apertura = apertura
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

        # Emergencia: la escribe el código. Ver server/seguridad/respuestas.py
        # por qué ante un crítico no se deja al modelo elegir las palabras.
        if severidad == "critical":
            ideacion = any(f.name == "ideacion_suicida" for f in flags)
            respuesta = ACOMPANAR if ideacion else EMERGENCIA
            lat["primera_frase_ms"] = _ms(t0)
            yield "speak", respuesta
            ra, uso = await _esperar(juez)
            yield "turn", self._cerrar(texto, flags, ra, respuesta, "codigo", "emergencia",
                                       uso, lat, t0)
            return

        objetivo = OBJETIVO_ALARMA if severidad == "high" else OBJETIVO_NORMAL
        user = f"OBJETIVO DE ESTE TURNO: {objetivo}\n\nPACIENTE: {texto}\n\n{SIN_CONTEXTO}"
        if self.apertura and not self.historial:
            user = f"VERA YA DIJO AL CONTESTAR LA LLAMADA: «{self.apertura}»\n\n{user}"
        partidor = SentenceSplitter()
        dichas: list[str] = []
        obj, uso_resp = None, {}
        try:
            async for tipo, dato in self.llm.astructured_stream(
                    RESPONDER_SYSTEM, user, RespuestaVera, historial=self.historial):
                if tipo == "delta":
                    for frase in partidor.push(dato):
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
            lat.setdefault("primera_frase_ms", _ms(t0))
            dichas.append(resto)
            yield "speak", resto
        utterance = " ".join(dichas) or (obj.utterance.strip() if obj else "")
        if utterance and not dichas:
            lat.setdefault("primera_frase_ms", _ms(t0))
            yield "speak", utterance
        redactado_por, marca = "modelo", "ok"
        if not utterance:
            utterance, redactado_por, marca = SIN_RESPUESTA, "codigo", "respuesta_vacia"
            lat.setdefault("primera_frase_ms", _ms(t0))
            yield "speak", utterance

        ra, uso_juez = await _esperar(juez)
        uso = {k: uso_resp.get(k, 0) + uso_juez.get(k, 0)
               for k in ("input_tokens", "output_tokens")}
        yield "turn", self._cerrar(texto, flags, ra, utterance, redactado_por, marca,
                                   uso, lat, t0)

    def _cerrar(self, texto, flags, ra, utterance, redactado_por, marca,
                uso, lat, t0) -> TurnoVera:
        """Punto único por el que pasan todas las rutas del turno."""
        self.historial += [{"role": "user", "content": texto},
                           {"role": "assistant", "content": utterance}]
        self.historial = self.historial[-2 * INTERCAMBIOS:]
        decision = combinar(flags, ra)
        self._previo = None if decision.risk in ("high", "critical") else texto
        lat["total_ms"] = _ms(t0)
        return TurnoVera(utterance=utterance, decision=decision,
                         redactado_por=redactado_por, marca=marca,
                         usage=uso, latencia_ms=lat)


async def _esperar(tarea) -> tuple[RiskAssessment | None, dict]:
    """Perder el juez degrada la evaluación, no la anula: la capa determinista
    ya decidió y su veredicto sigue en pie."""
    try:
        return await tarea
    except Exception:  # noqa: BLE001
        return None, {}
