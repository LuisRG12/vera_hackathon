"""La llamada: el oído, la cabeza y la voz en un solo bucle.

Es la forma de `server/voz/sesion.py` en vera_voice_agent, que ya había resuelto
lo difícil de un bucle de voz, con piezas nuevas: el oído es AssemblyAI, que
empuja turnos por un WebSocket, y la voz es Cartesia, que empuja audio por otro.

Lo que se conserva del original, cada cosa con su porqué:

- **El turno se genera en una tarea aparte y el bucle no la espera.** Esperarla
  dejaba el socket sin leer mientras Vera hablaba: ni el audio del micrófono ni
  la interrupción llegaban hasta que terminaba.
- **Todo lo que sale al navegador pasa por un cerrojo.** Hay varias corrutinas
  escribiendo en el mismo WebSocket, y dos envíos solapados corrompen el flujo.
- **La frase va antes que su audio.** La pantalla no tiene por qué esperar a la
  síntesis.

Lo nuevo:

- **La vigilancia lee todo lo que se oye antes de que exista el turno**, y la
  alerta sale de ahí. Lo que Vera contesta espera a que el paciente termine.
- **El audio va numerado por turno**, en los primeros cuatro bytes de cada
  trozo. Al interrumpir, el navegador descarta todo lo de ese turno que todavía
  tuviera en cola, llegue cuando llegue.
- **Lo que dice el código suena de inmediato**, porque ya está sintetizado
  (`FrasesFijas`); lo que genera el modelo se va sintetizando frase a frase.

Todavía no hay filtro de eco: con parlantes, el micrófono oye a Vera y la
transcribe como si fuera el paciente. Hasta el paso siguiente, se prueba con
audífonos.
"""
from __future__ import annotations

import asyncio
import json
import struct
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from server.config import settings
from server.dialogo.prompts import SALUDO
from server.dialogo.turno import Conversacion, TurnoVera
from server.seguridad.vigilancia import Lectura, Vigilancia
from server.voz.keyterms import CONTEXTO_CLINICO, KEYTERMS
from server.voz.stt import ErrorSTT, Turno, crear_stt
from server.voz.tts import ErrorVoz, FrasesFijas, TurnoDeVoz, VozCartesia

REGISTRO = Path(__file__).resolve().parents[2] / "registros" / "turnos.jsonl"


def senales_json(lectura: Lectura) -> list[dict]:
    return [{"concepto": s.concepto, "severidad": s.severidad, "coincidencia": s.coincidencia}
            for s in lectura.senales]


def turno_json(t: TurnoVera) -> dict:
    return {
        "utterance": t.utterance,
        "riesgo": t.decision.risk,
        "accion": t.decision.action,
        "fuente": t.decision.source,
        "motivo": t.decision.rationale,
        "reglas": t.decision.rule_flags,
        "redactado_por": t.redactado_por,
        "marca": t.marca,
        "latencia": t.latencia_ms,
        "tokens": t.usage,
    }


class SesionLlamada:
    """Una por llamada."""

    def __init__(self, ws: WebSocket, estado) -> None:
        self.ws = ws
        self.fijas: FrasesFijas = estado.fijas
        self.stt = crear_stt(keyterms=KEYTERMS, contexto=CONTEXTO_CLINICO)
        self.vigilancia = Vigilancia()
        self.conversacion = Conversacion(estado.llm, apertura=SALUDO)
        self.voz = VozCartesia()
        self.con_voz = False
        self.llamada = uuid.uuid4().hex[:8]
        self._envio = asyncio.Lock()
        self._generando: asyncio.Task | None = None
        # Número del turno de voz en curso: va delante de cada trozo de audio.
        self._n = 0
        # Si el navegador está reproduciendo a Vera. Lo dice él, porque el
        # servidor solo sabe cuándo terminó de mandar el audio, no de sonar.
        self.sonando = False
        self._ultimo = 0.0

    # ------------------------------------------------------- envío al cliente
    async def _enviar(self, dato: dict) -> None:
        async with self._envio:
            await self.ws.send_json(dato)

    async def _enviar_audio(self, n: int, pcm: bytes) -> None:
        async with self._envio:
            await self.ws.send_bytes(struct.pack("<I", n) + pcm)

    def _movimiento(self) -> None:
        self._ultimo = asyncio.get_running_loop().time()

    # ------------------------------------------------------------ lo que dice
    async def _decir_fija(self, texto: str, n: int) -> None:
        await self._enviar({"type": "frase", "texto": texto})
        if (pcm := self.fijas.audio(texto)) is not None:
            await self._enviar_audio(n, pcm)

    async def _reenviar(self, n: int, turno: TurnoDeVoz) -> None:
        async for pcm in turno.audio():
            await self._enviar_audio(n, pcm)

    def _nuevo_turno(self, texto: str) -> None:
        # Un turno nuevo del paciente corta lo que Vera estuviera diciendo.
        if self._generando and not self._generando.done():
            self._generando.cancel()
        self._generando = asyncio.create_task(self._emitir_turno(texto))

    async def _emitir_turno(self, texto: str) -> None:
        """Genera el turno y va mandando cada frase con su audio."""
        self._n += 1
        n = self._n
        voz: TurnoDeVoz | None = None
        reenvio: asyncio.Task | None = None

        async def sin_voz(e: ErrorVoz) -> None:
            # Cartesia se cayó a mitad de turno. El turno sigue —el texto sale en
            # pantalla y la decisión se toma igual—, pero sin voz generada. Lo
            # fijo sigue sonando, porque ya estaba sintetizado.
            nonlocal voz, reenvio
            self.con_voz = False
            voz = reenvio = None
            await self._enviar({"type": "aviso", "detalle": f"sin voz: {e}"})

        async def cerrar_voz() -> None:
            nonlocal voz, reenvio
            if voz is not None:
                try:
                    await voz.terminar()
                    await reenvio
                    voz = reenvio = None
                except ErrorVoz as e:
                    await sin_voz(e)

        try:
            async for tipo, dato in self.conversacion.turno(texto):
                if tipo != "speak":
                    await self._enviar({"type": "vera", **turno_json(dato)})
                    continue
                self._movimiento()
                if self.fijas.audio(dato) is not None:
                    # Lo que venía del modelo termina de mandarse antes: el
                    # respaldo fijo va después de lo ya dicho, no encima.
                    await cerrar_voz()
                    await self._decir_fija(dato, n)
                    continue
                await self._enviar({"type": "frase", "texto": dato})
                if not self.con_voz:
                    continue
                try:
                    if voz is None:
                        voz = self.voz.turno()
                        reenvio = asyncio.create_task(self._reenviar(n, voz))
                    await voz.decir(dato)
                except ErrorVoz as e:
                    await sin_voz(e)
            await cerrar_voz()
        except asyncio.CancelledError:
            if voz is not None:
                await voz.cancelar()
            if reenvio is not None:
                reenvio.cancel()
            await self._enviar({"type": "callar", "hasta": n})
            raise

    # ---------------------------------------------------------- lo que oye
    def _anotar(self, t: Turno, lectura: Lectura) -> None:
        """Deja el turno cerrado en el registro, si está encendido (ver config)."""
        if not settings.registro_turnos:
            return
        REGISTRO.parent.mkdir(exist_ok=True)
        fila = {
            "llamada": self.llamada,
            "hora": datetime.now().isoformat(timespec="seconds"),
            "orden": t.orden,
            "texto": t.texto,
            "riesgo": lectura.riesgo,
            "senales": senales_json(lectura),
            "alertas": [{"concepto": a.senal.concepto, "coincidencia": a.senal.coincidencia,
                         "texto": a.texto, "en_parcial": a.en_parcial}
                        for a in self.vigilancia.alertas_del_turno(t.orden)],
            "stt": settings.stt_modelo,
        }
        with REGISTRO.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")

    async def _bajada(self) -> None:
        """Lo que llega del reconocedor: primero lo lee la vigilancia."""
        async for t in self.stt.eventos():
            if t.vacio:
                continue
            self._movimiento()
            lectura = self.vigilancia.leer(t.texto, t.orden, t.cerrado)
            for a in lectura.nuevas:
                await self._enviar({
                    "type": "alerta",
                    "concepto": a.senal.concepto,
                    "severidad": a.senal.severidad,
                    "accion": a.senal.accion,
                    "coincidencia": a.senal.coincidencia,
                    "texto": a.texto,
                    "orden": a.orden,
                    "en_parcial": a.en_parcial,
                })
            await self._enviar({
                "type": "oido",
                "texto": t.texto,
                "cerrado": t.cerrado,
                "orden": t.orden,
                "idioma": t.idioma,
                "confianza_idioma": t.confianza_idioma,
                "riesgo": lectura.riesgo,
                "senales": senales_json(lectura),
            })
            if t.cerrado:
                self._anotar(t, lectura)
                self._nuevo_turno(t.texto)
        if self.stt.error:
            await self._enviar({"type": "error", "detalle": self.stt.error})

    async def _subida(self) -> None:
        """Lo que manda el navegador: audio del micrófono y avisos de reproducción."""
        # Colgar es el final normal de una llamada: se atrapa aquí porque la
        # excepción vive dentro de la tarea, y si escapa asyncio la guarda sin
        # que nadie la recoja.
        try:
            while True:
                sobre = await self.ws.receive()
                if sobre.get("type") == "websocket.disconnect":
                    return
                if (pcm := sobre.get("bytes")) is not None:
                    await self.stt.enviar(pcm)
                    continue
                if not sobre.get("text"):
                    continue
                msg = json.loads(sobre["text"])
                if msg.get("type") == "sonando":
                    self.sonando = bool(msg.get("valor"))
                    self._movimiento()
                elif msg.get("type") == "colgar":
                    return
        except (WebSocketDisconnect, ErrorSTT, RuntimeError):
            return

    async def _reloj(self) -> None:
        """Cierra la llamada si nadie dice nada, para no gastar crédito.

        AssemblyAI factura por tiempo de conexión abierta. Mientras Vera habla
        —o se está generando lo que va a decir— no hay silencio que contar: el
        del paciente escuchándola es parte normal de la llamada. El paso de la
        etapa 5 sobre el silencio cambia esto por algo más humano que colgar.
        """
        while True:
            await asyncio.sleep(2)
            ocupada = self.sonando or (self._generando and not self._generando.done())
            if ocupada:
                self._movimiento()
                continue
            quieto = asyncio.get_running_loop().time() - self._ultimo
            if quieto >= settings.stt_inactividad_s:
                await self._enviar({
                    "type": "inactiva",
                    "detalle": f"cerrada tras {int(quieto)}s sin voz, para no gastar crédito",
                })
                return

    # ------------------------------------------------------------- la llamada
    async def atender(self) -> None:
        await self.ws.accept()
        try:
            await self.stt.abrir()
        except ErrorSTT as e:
            await self._enviar({"type": "error", "detalle": str(e)})
            await self.ws.close()
            return
        try:
            await self.voz.abrir()
            self.con_voz = True
        except ErrorVoz as e:
            await self._enviar({"type": "aviso", "detalle": f"sin voz: {e}"})

        await self._enviar({
            "type": "listo",
            "stt": self.stt.nombre,
            "modo": settings.stt_modo,
            "sample_rate": settings.stt_sample_rate,
            "voz_sample_rate": settings.tts_sample_rate if self.con_voz else None,
            "llm": settings.llm_modelo,
        })

        # Vera saluda primero, como en una llamada de verdad, y se identifica
        # como asistente virtual antes de preguntar nada.
        self._n += 1
        await self._decir_fija(SALUDO, self._n)
        self._movimiento()

        tareas = [asyncio.create_task(c) for c in (self._subida(), self._bajada(), self._reloj())]
        try:
            # La primera que termine manda: si el navegador cuelga no tiene
            # sentido seguir esperando turnos, y si AssemblyAI cierra no hay a
            # quién mandarle el audio.
            await asyncio.wait(tareas, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tareas:
                t.cancel()
            if self._generando and not self._generando.done():
                self._generando.cancel()
            await self.stt.cerrar()
            await self.voz.cerrar()
