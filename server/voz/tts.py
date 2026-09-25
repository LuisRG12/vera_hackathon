"""La voz de Vera: Cartesia, en español colombiano.

**Por qué Cartesia.** El camino del reto que sigue este proyecto —el reconocedor
en tiempo real de AssemblyAI con orquestación propia— dice «bring your own LLM
and text-to-speech», y el ejemplo de AssemblyAI con Pipecat empareja su
reconocedor con Cartesia. Tiene voces colombianas nativas; se eligió Mariana
oyendo las cuatro. Medido: el primer trozo de audio llega a ~0,2 s.

El proyecto original usaba Piper, local. Su argumento era que nada de la llamada
salía de la máquina, y ese argumento cayó cuando el oído y la cabeza pasaron a
servicios en la nube. Lo que quedaba a su favor —gratis, sin clave— no compensa
que suene sintética justo en la parte que el paciente oye.

**Un contexto por turno.** Las frases de un turno entran al mismo contexto a
medida que el modelo las termina, así que la entonación sigue de una a otra en
vez de empezar de cero en cada frase. Al final del turno se cierra con un texto
vacío: mientras el modelo genera no se sabe cuál es la última.

**Interrumpir.** Cancelar un contexto no es instantáneo: medido, llegan todavía
cuatro trozos que ya venían en camino. Si pasaran al navegador, Vera seguiría
hablando un momento después de que el paciente la interrumpió. Se descartan aquí.

**Lo que dice el código se sintetiza una vez** (`FrasesFijas`): el saludo, la
emergencia, los respaldos. Se conoce de antemano, así que suena al instante y
suena aunque Cartesia se caiga, que es cuando más falta hace.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import websockets

from server.config import settings

URL_WS = "wss://api.cartesia.ai/tts/websocket"
URL_BYTES = "https://api.cartesia.ai/tts/bytes"
# El audio de las frases fijas va en el repositorio, no en una caché de disco.
# El disco del Space es efímero: cada despliegue y cada reinicio las volvía a
# sintetizar todas, unos 2.700 caracteres del plan de Cartesia cada vez, y el
# 22-sep el plan gratuito se agotó a mitad de un arranque —seis frases se
# quedaron sin audio, entre ellas las despedidas tras una alarma—. Lo regenera
# `scripts/frases.py` cuando cambia un texto; si falta alguna, el servidor la
# sintetiza al arrancar como antes.
CACHE = Path(__file__).resolve().parents[2] / "audio" / "frases"


class ErrorVoz(RuntimeError):
    pass


def _pedido_base() -> dict:
    return {
        "model_id": settings.tts_modelo,
        "voice": {"mode": "id", "id": settings.tts_voz},
        "language": "es",
        "accent": settings.tts_acento,
        "output_format": {"container": "raw", "encoding": "pcm_s16le",
                          "sample_rate": settings.tts_sample_rate},
    }


class TurnoDeVoz:
    """Lo que Vera dice en un turno: frases que entran, audio que sale."""

    def __init__(self, voz: VozCartesia, contexto: str, cola: asyncio.Queue):
        self._voz = voz
        self.contexto = contexto
        self._cola = cola
        self.cancelado = False

    async def decir(self, frase: str) -> None:
        await self._voz._enviar({**_pedido_base(), "context_id": self.contexto,
                                 "transcript": frase, "continue": True})

    async def terminar(self) -> None:
        """No hay más frases: que genere lo que queda y cierre."""
        await self._voz._enviar({**_pedido_base(), "context_id": self.contexto,
                                 "transcript": "", "continue": False})

    async def cancelar(self) -> None:
        """El paciente interrumpió. Lo que siga llegando de este turno no suena."""
        if self.cancelado:
            return
        self.cancelado = True
        self._voz._cancelados.add(self.contexto)
        # Lo que ya estaba en la cola tampoco suena: son justo los trozos que
        # seguirían oyéndose después de la interrupción.
        while not self._cola.empty():
            self._cola.get_nowait()
        self._cola.put_nowait(None)
        try:
            await self._voz._enviar({"context_id": self.contexto, "cancel": True})
        except ErrorVoz:
            pass

    async def audio(self) -> AsyncIterator[bytes]:
        """PCM 16 bits mono a `tts_sample_rate`, a trozos, hasta que termine."""
        while (trozo := await self._cola.get()) is not None:
            yield trozo


class VozCartesia:
    """Una por llamada: una conexión con Cartesia, que se reutiliza entre turnos."""

    def __init__(self, conectar=None):
        # `conectar` existe para poder probar el enrutado sin red.
        self._conectar = conectar or self._conectar_cartesia
        self._ws = None
        self._lector: asyncio.Task | None = None
        self._colas: dict[str, asyncio.Queue] = {}
        self._cancelados: set[str] = set()
        self._envio = asyncio.Lock()
        self.error: str | None = None

    async def _conectar_cartesia(self):
        if not settings.tts_configurado:
            raise ErrorVoz("falta CARTESIA_API_KEY")
        return await websockets.connect(
            f"{URL_WS}?cartesia_version={settings.tts_version}",
            additional_headers={"X-API-Key": settings.cartesia_api_key})

    async def abrir(self) -> None:
        if self._ws is not None:
            return
        try:
            self._ws = await self._conectar()
        except (OSError, websockets.WebSocketException) as e:
            raise ErrorVoz(f"no se pudo conectar con Cartesia: {e}") from e
        self._lector = asyncio.create_task(self._leer())

    def turno(self) -> TurnoDeVoz:
        contexto = uuid.uuid4().hex
        cola: asyncio.Queue = asyncio.Queue()
        self._colas[contexto] = cola
        return TurnoDeVoz(self, contexto, cola)

    async def _enviar(self, mensaje: dict) -> None:
        if self._ws is None:
            await self.abrir()
        try:
            async with self._envio:
                await self._ws.send(json.dumps(mensaje, ensure_ascii=False))
        except websockets.ConnectionClosed as e:
            raise ErrorVoz(self.error or "se cayó la conexión con Cartesia") from e

    async def _leer(self) -> None:
        """Reparte el audio que llega a la cola de su turno."""
        try:
            async for bruto in self._ws:
                m = json.loads(bruto)
                contexto = m.get("context_id")
                cola = self._colas.get(contexto)
                tipo = m.get("type")
                fin = tipo == "done" or bool(m.get("done"))
                if tipo == "error":
                    self.error = f"{m.get('title')}: {m.get('message')}"
                    if cola is not None:
                        cola.put_nowait(None)
                    continue
                if contexto in self._cancelados:
                    # Los trozos que venían en camino se tiran; con el `done`
                    # el contexto ya no existe y se olvida.
                    if fin:
                        self._cancelados.discard(contexto)
                        self._colas.pop(contexto, None)
                    continue
                if cola is None:
                    continue
                if tipo == "chunk" and m.get("data"):
                    cola.put_nowait(base64.b64decode(m["data"]))
                if fin:
                    cola.put_nowait(None)
                    self._colas.pop(contexto, None)
        except websockets.ConnectionClosed as e:
            self.error = self.error or f"Cartesia cerró la conexión: {e}"
        finally:
            # Nadie se queda esperando audio que ya no va a llegar.
            for cola in self._colas.values():
                cola.put_nowait(None)

    async def cerrar(self) -> None:
        if self._lector and not self._lector.done():
            self._lector.cancel()
        if self._ws is not None:
            await self._ws.close()
            self._ws = None


class FrasesFijas:
    """El audio de lo que dice el código, sintetizado una vez y guardado.

    Se guarda en disco con una clave que incluye voz, acento, modelo y formato,
    además del texto: cambiar cualquiera de ellos vuelve a sintetizar, y no
    cambiar nada no gasta un solo carácter del plan de Cartesia al reiniciar.
    """

    def __init__(self, carpeta: Path = CACHE, sintetizar=None):
        self.carpeta = carpeta
        # `sintetizar` existe para poder probar la caché sin red.
        self._sintetizar = sintetizar or self._sintetizar_cartesia
        self._audio: dict[str, bytes] = {}

    @staticmethod
    def clave(texto: str) -> str:
        firma = "|".join([settings.tts_modelo, settings.tts_voz, settings.tts_acento,
                          str(settings.tts_sample_rate), texto])
        return hashlib.sha1(firma.encode("utf-8")).hexdigest()[:16]

    async def _sintetizar_cartesia(self, texto: str) -> bytes:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(URL_BYTES, json={**_pedido_base(), "transcript": texto}, headers={
                "Authorization": f"Bearer {settings.cartesia_api_key}",
                "Cartesia-Version": settings.tts_version})
        if r.status_code != 200:
            raise ErrorVoz(f"HTTP {r.status_code}: {r.text[:200]}")
        return r.content

    async def preparar(self, textos: list[str]) -> dict[str, int]:
        """Deja listo el audio de cada texto. Devuelve cuántos salieron de dónde."""
        self.carpeta.mkdir(parents=True, exist_ok=True)
        cuenta = {"disco": 0, "sintetizadas": 0, "fallidas": 0}
        for texto in dict.fromkeys(textos):
            ruta = self.carpeta / f"{self.clave(texto)}.pcm"
            if ruta.exists():
                self._audio[texto] = ruta.read_bytes()
                cuenta["disco"] += 1
                continue
            try:
                pcm = await self._sintetizar(texto)
            except (ErrorVoz, httpx.HTTPError):
                cuenta["fallidas"] += 1
                continue
            ruta.write_bytes(pcm)
            self._audio[texto] = pcm
            cuenta["sintetizadas"] += 1
        return cuenta

    def audio(self, texto: str) -> bytes | None:
        return self._audio.get(texto)
