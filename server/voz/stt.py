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
from dataclasses import dataclass
from typing import AsyncIterator

import websockets

from server.config import settings


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
        self.nombre = f"assemblyai:{settings.stt_modelo}"

    @property
    def disponible(self) -> bool:
        return settings.stt_configurado

    def _url(self) -> str:
        p = {
            "sample_rate": settings.stt_sample_rate,
            "encoding": "pcm_s16le",
            "speech_model": settings.stt_modelo,
            "format_turns": "true",
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
        except websockets.ConnectionClosed:
            # Cerrar es el final normal de una llamada, no un fallo que reportar.
            pass
        finally:
            await self._cola.put(None)

    async def enviar(self, pcm: bytes) -> None:
        """Empuja audio. PCM 16 bits mono al sample rate configurado."""
        if self._ws is None:
            await self.abrir()
        try:
            await self._ws.send(pcm)
        except websockets.ConnectionClosed as e:
            raise ErrorSTT("se cayó la conexión con AssemblyAI") from e

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
            await self._ws.send(json.dumps({"type": "Terminate"}))
            await asyncio.wait_for(self._bomba, timeout=5)
        except (websockets.ConnectionClosed, asyncio.TimeoutError, TypeError):
            pass
        finally:
            if self._bomba and not self._bomba.done():
                self._bomba.cancel()
            await self._ws.close()
            self._ws = None


def crear_stt(keyterms: list[str] | None = None, contexto: str | None = None) -> Reconocedor:
    return Reconocedor(keyterms=keyterms, contexto=contexto)
