"""Reconocimiento de voz con AssemblyAI Universal-Streaming.

**Por qué esta capa existe y no la de antes.** La versión local (Vosk) era
síncrona: se le entregaba un bloque de audio y devolvía en el acto el parcial y,
si tocaba, el enunciado cerrado. AssemblyAI no funciona así — es un WebSocket que
*empuja*: uno manda audio por un lado y los turnos llegan por el otro, sin
correspondencia uno a uno. Forzar aquí la firma vieja obligaría a bloquear
esperando una respuesta que puede no venir con ese bloque, y se perdería justo lo
que hace útil el streaming.

Así que la interfaz cambia a propósito: `enviar` empuja audio y `eventos` entrega
lo que va llegando. Quien consume decide el ritmo.

**El fin de turno lo decide AssemblyAI, no nosotros.** El servidor trae fin de
turno *semántico* —mira el sentido de lo dicho, no solo el silencio—, que es
mejor que la heurística propia de «frase a media idea». Esa heurística queda como
respaldo en `agent/turn_taking.py` y se mide si todavía aporta antes de
conservarla.

**La clave nunca sale del servidor.** El navegador habla con nosotros y nosotros
hablamos con AssemblyAI. Si algún día el navegador conectara directo, haría falta
un token temporal; mientras el audio pase por aquí, no.
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
from collections.abc import AsyncIterator
from dataclasses import dataclass

import websockets

from server.config import settings

# AssemblyAI rechaza trozos fuera de [50, 1000] ms y **cierra la conexión** con
# error 3007. No es un aviso: la llamada se cae. El `AudioWorklet` del navegador
# entrega bloques de 128 muestras —8 ms a 16 kHz— así que sin acumular aquí, una
# llamada dura un segundo. Se comprobó midiendo (ver docs/bitacora.md, 12-sep).
#
# La acumulación vive en este módulo y no en el navegador a propósito: la regla
# es de AssemblyAI, así que la conoce quien le habla. Cualquier otro cliente
# —un arnés de pruebas, un archivo de audio— queda cubierto sin repetirla.
MS_POR_ENVIO = 100
MS_MINIMO = 50


@dataclass(frozen=True)
class Turno:
    """Lo que AssemblyAI dice que lleva oído.

    `texto` es acumulativo dentro del turno: cada mensaje trae el enunciado
    completo hasta ese momento, no el trozo nuevo. `cerrado` marca el final.
    """

    texto: str
    cerrado: bool
    orden: int = 0
    idioma: str | None = None
    confianza_idioma: float | None = None

    @property
    def vacio(self) -> bool:
        return not self.texto.strip()


class ErrorSTT(RuntimeError):
    pass


class Reconocedor:
    """Una sesión de reconocimiento. Una por llamada: lleva el turno en curso."""

    def __init__(self, keyterms: list[str] | None = None, contexto: str | None = None):
        self.keyterms = keyterms or []
        self.contexto = contexto
        self._ws = None
        self._cola: asyncio.Queue[Turno | None] = asyncio.Queue()
        self._bomba: asyncio.Task | None = None
        self._pendiente = bytearray()
        self.error: str | None = None
        self.nombre = f"assemblyai:{settings.stt_modelo}"

    def _bytes(self, ms: int) -> int:
        """Cuántos bytes son `ms` de PCM 16 bits mono."""
        return int(settings.stt_sample_rate * ms / 1000) * 2

    @property
    def disponible(self) -> bool:
        return settings.stt_configurado

    def _url(self) -> str:
        p = {
            "sample_rate": settings.stt_sample_rate,
            "encoding": "pcm_s16le",
            "speech_model": settings.stt_modelo,
            "format_turns": "true",
            "mode": settings.stt_modo,
        }
        if settings.stt_idiomas:
            p["language_codes"] = settings.stt_idiomas
            p["language_detection"] = "true"
        # Límites del servicio: 100 términos, 50 caracteres cada uno. Los que se
        # pasan de largo se ignoran en silencio, así que se podan aquí para que
        # el recorte sea visible en el código y no una sorpresa en producción.
        if self.keyterms:
            terminos = [t for t in self.keyterms if len(t) <= 50][:100]
            p["keyterms_prompt"] = json.dumps(terminos, ensure_ascii=False)
        if self.contexto:
            p["prompt"] = self.contexto
        return f"{settings.stt_url}?{urllib.parse.urlencode(p)}"

    async def abrir(self) -> None:
        if not self.disponible:
            raise ErrorSTT("falta ASSEMBLYAI_API_KEY")
        if self._ws is not None:
            return

        cabeceras = {"Authorization": settings.assemblyai_api_key}
        try:
            self._ws = await websockets.connect(self._url(), additional_headers=cabeceras)
        except TypeError:
            # websockets < 14 llamaba `extra_headers` a lo mismo.
            self._ws = await websockets.connect(self._url(), extra_headers=cabeceras)

        primero = json.loads(await self._ws.recv())
        if primero.get("type") != "Begin":
            raise ErrorSTT(f"la sesión no abrió: {primero}")
        self._bomba = asyncio.create_task(self._bombear())

    async def _bombear(self) -> None:
        """Lee del socket y deja los turnos en la cola hasta que se cierre."""
        try:
            async for bruto in self._ws:
                m = json.loads(bruto)
                tipo = m.get("type")
                if tipo == "Error":
                    # AssemblyAI avisa y CIERRA. Si esto se traga en silencio,
                    # la llamada se muere sin que nadie sepa por qué —que fue
                    # exactamente lo que pasó con el trozo de 8 ms—.
                    self.error = f"{m.get('error_code')}: {m.get('error')}"
                    break
                if tipo == "Turn":
                    await self._cola.put(Turno(
                        texto=m.get("transcript", ""),
                        cerrado=bool(m.get("end_of_turn")),
                        orden=m.get("turn_order", 0),
                        idioma=m.get("language_code"),
                        confianza_idioma=m.get("language_confidence"),
                    ))
                elif tipo == "Termination":
                    break
        except websockets.ConnectionClosed as e:
            # Cerrar es el final normal de una llamada. Solo es un fallo si el
            # código no es de cierre limpio, y entonces hay que poder verlo.
            recibido = getattr(e, "rcvd", None)
            if recibido is not None and recibido.code not in (1000, 1001, 1005):
                self.error = self.error or f"cierre {recibido.code}: {recibido.reason}"
        finally:
            await self._cola.put(None)

    async def enviar(self, pcm: bytes) -> None:
        """Acumula audio y lo suelta en trozos que AssemblyAI acepte.

        PCM 16 bits mono al sample rate configurado. El que llama manda lo que
        tenga, del tamaño que sea; el reparto correcto se hace aquí.
        """
        if self._ws is None:
            await self.abrir()
        self._pendiente += pcm
        tope = self._bytes(MS_POR_ENVIO)
        while len(self._pendiente) >= tope:
            trozo, self._pendiente = bytes(self._pendiente[:tope]), self._pendiente[tope:]
            await self._soltar(trozo)

    async def _soltar(self, trozo: bytes) -> None:
        try:
            await self._ws.send(trozo)
        except websockets.ConnectionClosed as e:
            raise ErrorSTT(self.error or "se cayó la conexión con AssemblyAI") from e

    async def forzar_fin_de_turno(self) -> None:
        """Cierra el turno en curso sin esperar al silencio.

        Lo usa el arnés de reconocimiento para que cada frase medida caiga en su
        propio turno y no se mezcle con la siguiente.
        """
        if self._ws is not None:
            await self._ws.send(json.dumps({"type": "ForceEndpoint"}))

    async def eventos(self) -> AsyncIterator[Turno]:
        """Los turnos según llegan. Termina cuando se cierra la sesión."""
        while True:
            t = await self._cola.get()
            if t is None:
                return
            yield t

    async def cerrar(self) -> None:
        if self._ws is None:
            return
        try:
            if len(self._pendiente) >= self._bytes(MS_MINIMO):
                await self._soltar(bytes(self._pendiente))
            self._pendiente.clear()
            await self._ws.send(json.dumps({"type": "Terminate"}))
            await asyncio.wait_for(self._bomba, timeout=5)
        except (TimeoutError, websockets.ConnectionClosed, TypeError):
            pass
        finally:
            if self._bomba and not self._bomba.done():
                self._bomba.cancel()
            await self._ws.close()
            self._ws = None


def crear_stt(keyterms: list[str] | None = None, contexto: str | None = None) -> Reconocedor:
    return Reconocedor(keyterms=keyterms, contexto=contexto)
