"""El bucle de la llamada: eco, interrupción y quién alerta (0 tokens, sin red).

    uv run python -m evals.llamada

Los otros arneses prueban las piezas por separado. Este prueba lo que solo existe
cuando están juntas, que es donde el proyecto original se rompió más veces: que
Vera no se conteste a sí misma, que un signo crítico no se descarte nunca aunque
suene a eco, que el paciente pueda callarla, y que interrumpirla no deje ese
turno sin valoración del juez.

Todo con dobles: un reconocedor que entrega los turnos que se le digan, un modelo
que responde lo que se le diga y un Cartesia que devuelve audio de mentira.
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import server.voz.sesion as sesion_mod
from evals.turno import ModeloDeMentira
from evals.voz import CartesiaDeMentira
from server.dialogo.prompts import DESPEDIDA_FINAL, RETOMAR_SILENCIO, SALUDO
from server.voz.sesion import SesionLlamada
from server.voz.stt import Turno
from server.voz.tts import FrasesFijas, VozCartesia

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {nombre}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(ok)


class WebSocketDeMentira:
    def __init__(self):
        self.enviados: list[dict] = []
        self.audio: list[bytes] = []
        self.app = SimpleNamespace()
        self._entrante: asyncio.Queue = asyncio.Queue()

    async def accept(self):
        pass

    async def send_json(self, d):
        self.enviados.append(d)

    async def send_bytes(self, b):
        self.audio.append(b)

    async def receive(self):
        return await self._entrante.get()

    async def close(self):
        self._entrante.put_nowait({"type": "websocket.disconnect"})

    def de_tipo(self, tipo):
        return [m for m in self.enviados if m["type"] == tipo]


class OidoDeMentira:
    """Entrega los turnos que se le pidan, cuando se le pidan."""

    nombre = "mentira"
    error = None

    def __init__(self):
        self.cola: asyncio.Queue = asyncio.Queue()
        self.cerrado = False

    async def abrir(self):
        pass

    async def enviar(self, pcm):
        pass

    async def cerrar(self):
        self.cerrado = True

    def oye(self, texto, orden=0, cerrado=True):
        self.cola.put_nowait(Turno(texto=texto, cerrado=cerrado, orden=orden))

    async def eventos(self):
        while (t := await self.cola.get()) is not None:
            yield t


async def montar(modelo: ModeloDeMentira) -> tuple:
    """Una sesión con todo de mentira, ya saludando."""
    oido, cartesia = OidoDeMentira(), CartesiaDeMentira()

    async def conectar():
        return cartesia

    sesion_mod.crear_stt = lambda **kw: oido
    sesion_mod.VozCartesia = lambda: VozCartesia(conectar=conectar)

    async def sintetizar(texto):
        return b"\x01\x02" * 100

    fijas = FrasesFijas(sintetizar=sintetizar)
    fijas._audio = {t: b"\x01\x02" * 100 for t in (SALUDO,)}
    ws = WebSocketDeMentira()
    sesion = SesionLlamada(ws, SimpleNamespace(llm=modelo, fijas=fijas))
    asyncio.create_task(sesion.atender())
    await asyncio.sleep(0.05)
    return sesion, ws, oido


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    print("\n== Vera saluda al contestar ==")
    sesion, ws, oido = await montar(ModeloDeMentira())
    check("dice el saludo", [m["texto"] for m in ws.de_tipo("frase")] == [SALUDO])
    check("y suena de inmediato, ya sintetizado", len(ws.audio) == 1 and len(ws.audio[0]) > 4)
    check("el saludo queda apuntado como dicho por Vera",
          sesion.dichas.es_eco("soy un asistente virtual del equipo clínico"))

    print("\n== El eco de Vera no es un turno del paciente ==")
    oido.oye("Soy un asistente virtual del equipo clínico.", orden=0)
    await asyncio.sleep(0.05)
    check("se descarta y se dice por qué",
          [m["motivo"] for m in ws.de_tipo("descartado")] == ["eco"])
    check("no se genera respuesta al eco", len(ws.de_tipo("vera")) == 0)
    check("ni la vigilancia lo lee", len(ws.de_tipo("oido")) == 0)

    print("\n== Pero un signo crítico nunca se descarta ==")
    sesion, ws, oido = await montar(ModeloDeMentira())
    # Vera pregunta por el dolor de pecho; el paciente contesta con esas palabras.
    sesion.dichas.recordar("¿Ha tenido dolor en el pecho o le cuesta respirar?")
    oido.oye("dolor en el pecho", orden=1)
    await asyncio.sleep(0.1)
    check("el turno se atiende igual", len(ws.de_tipo("descartado")) == 0)
    check("y sale la emergencia",
          any(m["riesgo"] == "critical" for m in ws.de_tipo("vera")))

    print("\n== El paciente puede callar a Vera ==")
    lento = ModeloDeMentira(respuesta="Una frase larga que tarda en salir. Y otra más.",
                            pausa=0.03)
    sesion, ws, oido = await montar(lento)
    oido.oye("me duele un poco la herida", orden=1)
    await asyncio.sleep(0.05)
    antes = len(ws.de_tipo("callar"))
    oido.oye("pero además", orden=2, cerrado=False)  # el paciente vuelve a hablar
    await asyncio.sleep(0.3)
    callares = ws.de_tipo("callar")
    check("se le dice al navegador que calle", len(callares) > antes)
    # Callar es lo que para el audio que ya está en el navegador, y por eso se
    # manda aunque no quede nada por generar: una frase fija viaja entera y sigue
    # sonando cuando aquí ya no hay tarea que cancelar.
    check("y se le dice hasta qué turno tirar", callares[-1]["hasta"] == sesion._n,
          str(callares[-1]))
    interrumpidos = [m for m in ws.de_tipo("vera") if m["marca"] == "interrumpido"]
    check("el turno interrumpido se cierra igual", len(interrumpidos) == 1,
          str([m["marca"] for m in ws.de_tipo("vera")]))
    check("y conserva la valoración del juez",
          bool(interrumpidos) and "juez" in interrumpidos[0]["motivo"],
          str(interrumpidos[0]["motivo"]) if interrumpidos else "—")

    print("\n== Interrumpir una frase fija, que ya no se está generando ==")
    sesion, ws, oido = await montar(ModeloDeMentira())
    oido.oye("me duele el pecho y no me entra el aire", orden=1)
    await asyncio.sleep(0.2)          # la emergencia ya salió entera y sigue sonando
    sesion.sonando = True
    antes = len(ws.de_tipo("callar"))
    oido.oye("pero la verdad es", orden=2, cerrado=False)
    await asyncio.sleep(0.1)
    check("se calla igual, aunque no hubiera nada que cancelar",
          len(ws.de_tipo("callar")) > antes)

    print("\n== Un parcial de una palabra no la calla ==")
    sesion, ws, oido = await montar(ModeloDeMentira(respuesta="Una frase. Y otra más.",
                                                    pausa=0.03))
    oido.oye("me duele la herida", orden=1)
    await asyncio.sleep(0.05)
    antes = len(ws.de_tipo("callar"))
    oido.oye("eh", orden=2, cerrado=False)
    await asyncio.sleep(0.05)
    check("no se calla por un carraspeo", len(ws.de_tipo("callar")) == antes)

    print("\n== Si el escalamiento lo pone el juez, también se alerta ==")
    sesion, ws, oido = await montar(ModeloDeMentira(riesgo="high"))
    oido.oye("siento como una presión aquí que me agarra", orden=1)
    await asyncio.sleep(0.1)
    alertas = ws.de_tipo("alerta")
    check("sale la alerta del juez", [a["concepto"] for a in alertas] == ["lo vio el juez"],
          str([a["concepto"] for a in alertas]))
    # La tarjeta mostraba la respuesta de Vera como si fuera el reporte del
    # paciente. Una alerta clínica no puede confundir quién dijo qué.
    check("y cita lo que dijo el paciente, no lo que contestó Vera",
          alertas[0]["texto"] == "siento como una presión aquí que me agarra"
          and alertas[0]["orden"] == 1 and alertas[0]["origen"] == "juez",
          str(alertas[0]))
    oido.oye("y sigo igual de mal", orden=2)
    await asyncio.sleep(0.1)
    check("y no se repite en cada turno", len(ws.de_tipo("alerta")) == 1)

    print("\n== El silencio del paciente ==")
    sesion, ws, oido = await montar(ModeloDeMentira())
    check("una pausa para pensar no se toca", sesion._que_decir_al_silencio(5) is None)
    check("pasados 20 s, Vera retoma", sesion._que_decir_al_silencio(21) == RETOMAR_SILENCIO)
    sesion._retomes = 1
    check("si sigue callado, se despide y cierra",
          sesion._que_decir_al_silencio(31) == DESPEDIDA_FINAL)
    sesion._retomes = 2
    check("y no insiste una tercera vez", sesion._que_decir_al_silencio(300) is None)

    # Vera hablando no puede contar como que el paciente contestó: si contara,
    # cada retome se anularía a sí mismo y la llamada no se cerraría nunca.
    sesion._retomes = 1
    sesion._reloj()
    check("lo que dice Vera no reinicia los intentos", sesion._retomes == 1)
    sesion._movimiento()
    check("lo que dice el paciente sí", sesion._retomes == 0)

    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones de la llamada.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
