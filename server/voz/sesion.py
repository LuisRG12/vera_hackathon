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

- **Lo que Vera dice vuelve por el micrófono**, y el reconocedor lo transcribe
  como si hablara el paciente. El filtro de eco (`voz/eco.py`) lo reconoce y lo
  descarta, con una excepción: un signo crítico nunca se descarta. Sin esto,
  Vera repitiendo «ese dolor en el pecho» levantaría ella sola una emergencia.
- **El paciente calla a Vera en cuanto toma la palabra**, con lo que oiga el
  reconocedor mientras ella habla. Y cancelar su respuesta no cancela la
  seguridad: el turno interrumpido se cierra igual con la valoración del juez,
  que ya venía en camino.
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
from server.dialogo.prompts import DESPEDIDA_FINAL, RETOMAR_SILENCIO, SALUDO, SIN_OIDO
from server.dialogo.turno import Conversacion, TurnoVera
from server.seguridad.reglas import detect_red_flags, max_severity
from server.seguridad.vigilancia import Lectura, Vigilancia
from server.voz.eco import RegistroDeVoz
from server.voz.keyterms import CONTEXTO_CLINICO, KEYTERMS
from server.voz.stt import ErrorSTT, Turno, crear_stt
from server.voz.tts import ErrorVoz, FrasesFijas, TurnoDeVoz, VozCartesia

REGISTRO = Path(__file__).resolve().parents[2] / "registros" / "turnos.jsonl"

# Cuántas palabras hacen falta en un parcial para callar a Vera. Con una basta un
# carraspeo que el reconocedor transcriba como cualquier cosa; con dos, el
# paciente está tomando la palabra de verdad.
MINIMO_PARA_CORTAR = 2


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
        # Con qué documento se respalda lo que dijo. Va el nombre del archivo y
        # la sección —no el texto del fragmento— porque es lo que permite
        # seguirla hasta la fuente sin volcar el corpus en cada turno. `ficticio`
        # distingue el plan de egreso de demostración de una guía publicada: en
        # una auditoría clínica esa diferencia es lo primero que hay que ver.
        "citas": [{"documento": c.fragmento.documento,
                   "seccion": c.fragmento.seccion,
                   "ficticio": c.fragmento.ficticio} for c in t.citas],
        "hubo_evidencia": t.hubo_evidencia,
    }


class SesionLlamada:
    """Una por llamada."""

    def __init__(self, ws: WebSocket, estado) -> None:
        self.ws = ws
        self.fijas: FrasesFijas = estado.fijas
        self.stt = crear_stt(keyterms=KEYTERMS, contexto=CONTEXTO_CLINICO)
        self.vigilancia = Vigilancia()
        # Lo que Vera lleva dicho, para reconocerlo si vuelve por el micrófono.
        self.dichas = RegistroDeVoz()
        self.conversacion = Conversacion(estado.llm, apertura=SALUDO,
                                         recuperador=estado.recuperador)
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
        self._retomes = 0
        # Una sola alerta por llamada cuando el escalamiento lo pone el juez: su
        # valoración vuelve turno a turno, y repetirla sería el ruido que hace
        # que el equipo clínico deje de mirar las alertas.
        self._alerta_juez = False
        # Lo último que dijo el paciente, y en qué turno: la alerta del juez
        # tiene que citarlo a él.
        self._dicho, self._orden = "", 0

    # ------------------------------------------------------- envío al cliente
    async def _enviar(self, dato: dict) -> None:
        async with self._envio:
            await self.ws.send_json(dato)

    async def _enviar_audio(self, n: int, pcm: bytes) -> None:
        async with self._envio:
            await self.ws.send_bytes(struct.pack("<I", n) + pcm)

    def _reloj(self) -> None:
        """Reinicia la cuenta del silencio, sin tocar los intentos.

        Es lo que hace Vera al hablar: mientras suena su voz no hay silencio que
        medir, pero su propia frase no puede contar como que el paciente
        contestó. Confundir las dos cosas dejaba al vigilante sin escalar nunca
        —cada retome se anulaba a sí mismo— y por tanto sin cerrar la llamada.
        """
        self._ultimo = asyncio.get_running_loop().time()

    def _movimiento(self) -> None:
        """El PACIENTE dijo algo: se reinicia la cuenta y los intentos."""
        self._reloj()
        self._retomes = 0

    # ------------------------------------------------------------ lo que dice
    async def _decir_fija(self, texto: str, n: int) -> None:
        self.dichas.recordar(texto)
        await self._enviar({"type": "frase", "texto": texto})
        if (pcm := self.fijas.audio(texto)) is not None:
            await self._enviar_audio(n, pcm)

    async def _reenviar(self, n: int, turno: TurnoDeVoz) -> None:
        async for pcm in turno.audio():
            await self._enviar_audio(n, pcm)

    def _nuevo_turno(self, texto: str) -> None:
        # Un turno nuevo del paciente corta lo que Vera estuviera diciendo.
        self._cortar()
        self._generando = asyncio.create_task(self._emitir_turno(texto))

    async def _emitir_turno(self, texto: str) -> None:
        """Genera el turno y va mandando cada frase con su audio."""
        self._n += 1
        n = self._n
        # Si Cartesia se cayó en un turno anterior, se intenta una vez por turno:
        # una llamada no puede quedarse muda para siempre por un corte de un rato.
        if not self.con_voz:
            try:
                await self.voz.cerrar()
                await self.voz.abrir()
                self.con_voz = True
                await self._enviar({"type": "aviso", "detalle": "voz recuperada"})
            except ErrorVoz:
                pass
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
                    await self._alertar_juez(dato)
                    continue
                self._reloj()
                if self.fijas.audio(dato) is not None:
                    # Lo que venía del modelo termina de mandarse antes: el
                    # respaldo fijo va después de lo ya dicho, no encima.
                    await cerrar_voz()
                    await self._decir_fija(dato, n)
                    continue
                # Se apunta ANTES de mandarla: el reconocedor puede devolverla
                # mientras todavía se sintetiza la siguiente, y apuntarla después
                # dejaría una ventana en la que el eco no se reconoce.
                self.dichas.recordar(dato)
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
            # El «callar» lo manda `_cortar`, que es quien sabe que el paciente
            # tomó la palabra; aquí solo se deja de generar audio que ya nadie
            # va a oír.
            if voz is not None:
                await voz.cancelar()
            if reenvio is not None:
                reenvio.cancel()
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

    def _es_eco(self, texto: str) -> bool:
        """Si esto lo dijo Vera y volvió por el micrófono.

        **Un signo crítico nunca se descarta**, aunque coincida con lo que Vera
        acaba de decir. El caso es real y es el peor posible: Vera pregunta «¿ha
        tenido dolor en el pecho?», el paciente contesta «dolor en el pecho», y
        eso es literalmente lo que ella dijo. Se descartaba en silencio y la
        urgencia no llegaba a existir.

        La exención es de `critical` y no de toda alarma: los textos fijos de
        Vera no disparan ninguna crítica por sí solos, pero sí `high` —dicen
        «fiebre», «materia», lo que el paciente acaba de contar—, y exentar
        `high` reabriría el defecto que este filtro existe para cerrar.
        """
        if max_severity(detect_red_flags(texto)) == "critical":
            return False
        return self.dichas.es_eco(texto, reproduciendo=self.sonando)

    def _cortar(self) -> None:
        """Calla a Vera: deja de generar, y sobre todo deja de sonar.

        **Callar y cancelar no son lo mismo, y confundirlos fue un defecto real.**
        Antes esto solo cancelaba la generación en curso, así que interrumpir no
        hacía nada cuando ya no quedaba nada que generar —una frase fija viaja
        entera y de una vez, y un turno del modelo termina de generarse mucho
        antes de terminar de sonar—. Probándolo con voz: Vera acababa su frase
        de emergencia completa y solo entonces contestaba a lo que el paciente
        había dicho encima, como si le hubiera hecho cola.

        El navegador tiene audio en cola que aquí ya no se puede quitar, así que
        se le dice que lo tire: todo lo del turno de voz en curso y anteriores.

        Cancelar la generación no puede cancelar la seguridad: el juez de ese
        turno ya venía en camino, así que la conversación lo cierra igual con lo
        que alcanzó a decirse. Sin esto, el turno que el paciente interrumpe
        —justo cuando tiene algo urgente que contar— sería el único sin la
        segunda capa.
        """
        asyncio.create_task(self._enviar({"type": "callar", "hasta": self._n}))
        if not (self._generando and not self._generando.done()):
            return
        self._generando.cancel()
        self._generando = None
        asyncio.create_task(self._cerrar_interrumpido())

    async def _cerrar_interrumpido(self) -> None:
        turno = await self.conversacion.cerrar_interrumpido()
        if turno is not None:
            await self._enviar({"type": "vera", **turno_json(turno)})
            await self._alertar_juez(turno)

    async def _alertar_juez(self, turno: TurnoVera) -> None:
        """Cuando el escalamiento lo pone el juez y no las reglas.

        Las alertas de la vigilancia salen de las reglas. Si el juez escala algo
        que las reglas no vieron —para eso está—, el equipo clínico tiene que
        enterarse igual, y hasta aquí eso solo se veía en la decisión del turno.

        La alerta cita **lo que dijo el paciente**, no lo que contestó Vera. Se
        vio en una llamada: la tarjeta mostraba la pregunta de Vera como si
        fuera el reporte del paciente, que es exactamente lo que una alerta
        clínica no puede confundir.
        """
        d = turno.decision
        if self._alerta_juez or d.source != "llm" or d.action not in ("escalate", "emergency"):
            return
        self._alerta_juez = True
        await self._enviar({
            "type": "alerta",
            "concepto": "lo vio el juez",
            "severidad": d.risk,
            "accion": d.action,
            "coincidencia": d.rationale.removeprefix("juez: ")[:160],
            "texto": self._dicho,
            "orden": self._orden,
            "en_parcial": False,
            "origen": "juez",
        })

    async def _bajada(self) -> None:
        """Lo que llega del reconocedor: primero se descarta el eco, luego lee la
        vigilancia, y con lo que queda se calla a Vera si el paciente habla."""
        async for t in self.stt.eventos():
            if t.vacio:
                continue
            if self._es_eco(t.texto):
                # No es el paciente: ni reinicia el reloj del silencio, ni lo lee
                # la vigilancia —Vera repite los síntomas que le cuentan, así que
                # su eco levantaría alarmas por lo que ella misma dijo—, ni corta
                # su propia frase a la mitad.
                if t.cerrado:
                    await self._enviar({"type": "descartado", "motivo": "eco",
                                        "texto": t.texto, "orden": t.orden})
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
                    "origen": "reglas",
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

            if not t.cerrado:
                # El paciente tomó la palabra mientras Vera hablaba: se calla.
                # Interrumpir a un agente que se equivocó no puede exigir
                # esperar a que termine, que es lo que vuelve insoportable
                # hablar con una máquina.
                if len(t.texto.split()) >= MINIMO_PARA_CORTAR:
                    self._cortar()
                continue

            self._anotar(t, lectura)
            self._dicho, self._orden = t.texto, t.orden
            self._nuevo_turno(t.texto)
        # El reconocedor se cayó. Vera todavía puede hablar —su voz es otro
        # servicio—, así que lo dice en vez de colgar en silencio, y la llamada
        # termina: seguir abierta sin oír a nadie no es degradarse, es fingir.
        if self.stt.error:
            await self._enviar({"type": "error", "detalle": self.stt.error})
            self._n += 1
            await self._decir_fija(SIN_OIDO, self._n)
            await asyncio.sleep(len(SIN_OIDO) * 0.06)  # que alcance a sonar

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

    def _que_decir_al_silencio(self, callado: float) -> str | None:
        """Qué toca decir tras `callado` segundos sin nada, o nada.

        Aparte del bucle a propósito: la decisión es determinista y se prueba en
        milisegundos, mientras que probarla dentro del temporizador exigiría un
        arnés que duerme medio minuto, y un arnés lento deja de correrse.
        """
        if self._retomes == 0 and callado >= settings.silencio_retomar_s:
            return RETOMAR_SILENCIO
        if self._retomes == 1 and callado >= settings.silencio_cerrar_s:
            return DESPEDIDA_FINAL
        return None

    async def _vigilar_silencio(self) -> None:
        """Retoma la llamada cuando el paciente lleva rato callado, y la cierra.

        El reconocedor sabe cuándo termina un turno, pero nadie medía «el
        paciente lleva medio minuto sin decir nada». Cuando el turno de Vera
        acaba sin pregunta —el mensaje de escalamiento, por ejemplo— el paciente
        no sabe si le toca hablar, y la llamada se queda muerta. En una llamada
        telefónica ese silencio es lo que hace colgar a la gente.

        Cerrar también es lo que impide pagar una conexión abierta que nadie usa.
        """
        while True:
            await asyncio.sleep(1.0)
            if self.sonando or (self._generando and not self._generando.done()):
                self._reloj()          # Vera está hablando: no hay silencio que contar
                continue
            frase = self._que_decir_al_silencio(
                asyncio.get_running_loop().time() - self._ultimo)
            if frase is None:
                continue
            self._retomes += 1
            self._n += 1
            await self._decir_fija(frase, self._n)
            self._reloj()
            if self._retomes >= 2:
                await self._enviar({"type": "adios", "detalle": "la llamada se cerró sola"})
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

        tareas = [asyncio.create_task(c)
                  for c in (self._subida(), self._bajada(), self._vigilar_silencio())]
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
