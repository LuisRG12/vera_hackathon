"""La voz de Vera: que el audio llegue a su turno y que interrumpir la calle.

    uv run python -m evals.voz          # con un Cartesia de mentira: sin red, sin gasto
    uv run python -m evals.voz --real   # contra Cartesia: mide el primer trozo

Lo delicado de la voz no es sintetizar —eso lo hace Cartesia— sino lo que pasa
alrededor: que cada trozo de audio vaya al turno que lo pidió, que tras una
interrupción no suene nada más, que nadie se quede esperando si la conexión se
cae, y que las frases fijas no se vuelvan a sintetizar en cada arranque.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
import time
from pathlib import Path

from server.config import settings
from server.voz.tts import FrasesFijas, VozCartesia

PASS, FAIL = "  [OK]", "  [FALLA]"
resultados: list[bool] = []


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    print(f"{PASS if ok else FAIL} {nombre}")
    if detalle and not ok:
        print(f"          -> {detalle}")
    resultados.append(ok)


class CartesiaDeMentira:
    """Un WebSocket que manda lo que se le diga y guarda lo que recibe."""

    def __init__(self):
        self.enviados: list[dict] = []
        self._entrante: asyncio.Queue = asyncio.Queue()

    async def send(self, texto: str) -> None:
        self.enviados.append(json.loads(texto))

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self._entrante.get()
        if m is None:
            raise StopAsyncIteration
        return json.dumps(m)

    async def close(self) -> None:
        self._entrante.put_nowait(None)

    def llega(self, **m) -> None:
        if "data" in m:
            m["data"] = base64.b64encode(m["data"]).decode()
        self._entrante.put_nowait(m)


async def todo(turno, espera: float = 1.0) -> list[bytes]:
    async def juntar():
        return [t async for t in turno.audio()]
    return await asyncio.wait_for(juntar(), espera)


async def mentira() -> None:
    falso = CartesiaDeMentira()

    async def conectar():
        return falso

    voz = VozCartesia(conectar=conectar)
    await voz.abrir()

    print("\n== Cada trozo va a su turno ==")
    t1, t2 = voz.turno(), voz.turno()
    await t1.decir("Qué bien que ya esté caminando.")
    pedido = falso.enviados[-1]
    check("se pide la frase como continuación del turno",
          pedido["context_id"] == t1.contexto and pedido["continue"] is True
          and pedido["transcript"] == "Qué bien que ya esté caminando.")
    check("con la voz, el acento colombiano y el español",
          pedido["voice"]["id"] == settings.tts_voz and pedido["accent"] == "colombian"
          and pedido["language"] == "es")
    await t1.terminar()
    check("el turno se cierra con texto vacío",
          falso.enviados[-1]["transcript"] == "" and falso.enviados[-1]["continue"] is False)
    falso.llega(type="chunk", context_id=t1.contexto, data=b"AA", done=False)
    falso.llega(type="chunk", context_id=t2.contexto, data=b"BB", done=False)
    falso.llega(type="chunk", context_id=t1.contexto, data=b"CC", done=False)
    falso.llega(type="done", context_id=t1.contexto, done=True)
    falso.llega(type="done", context_id=t2.contexto, done=True)
    check("el primer turno recibe solo lo suyo, en orden", await todo(t1) == [b"AA", b"CC"])
    check("y el segundo, lo suyo", await todo(t2) == [b"BB"])

    print("\n== Interrumpir calla a Vera de verdad ==")
    t3 = voz.turno()
    falso.llega(type="chunk", context_id=t3.contexto, data=b"ya", done=False)
    falso.llega(type="chunk", context_id=t3.contexto, data=b"en cola", done=False)
    await asyncio.sleep(0.05)  # que el lector los reparta antes de cancelar
    await t3.cancelar()
    check("se le pide a Cartesia que cancele",
          falso.enviados[-1] == {"context_id": t3.contexto, "cancel": True})
    falso.llega(type="chunk", context_id=t3.contexto, data=b"en camino", done=False)
    falso.llega(type="done", context_id=t3.contexto, done=True)
    check("no suena nada: ni lo que estaba en cola ni lo que venía en camino",
          await todo(t3) == [])
    await asyncio.sleep(0.05)
    check("y el contexto cancelado se olvida al cerrarse",
          t3.contexto not in voz._colas and t3.contexto not in voz._cancelados)

    print("\n== Un error o una caída no deja a nadie esperando ==")
    t4 = voz.turno()
    falso.llega(type="error", context_id=t4.contexto, title="Invalid", message="voz inválida")
    check("el turno con error termina", await todo(t4) == [])
    check("y el error queda a la vista", voz.error == "Invalid: voz inválida", str(voz.error))
    t5 = voz.turno()
    await falso.close()
    try:
        await todo(t5)
        termino = True
    except TimeoutError:
        termino = False
    check("si la conexión se cae, el turno en curso termina", termino)
    await voz.cerrar()

    print("\n== Las frases fijas se sintetizan una sola vez ==")
    with tempfile.TemporaryDirectory() as tmp:
        pedidas: list[str] = []

        async def sintetizar(texto):
            pedidas.append(texto)
            return texto.encode("utf-8") * 10

        fijas = FrasesFijas(Path(tmp), sintetizar=sintetizar)
        cuenta = await fijas.preparar(["Hola.", "Adiós.", "Hola."])
        check("la primera vez se sintetizan, sin repetir", cuenta["sintetizadas"] == 2
              and len(pedidas) == 2, str(cuenta))
        otra = FrasesFijas(Path(tmp), sintetizar=sintetizar)
        cuenta = await otra.preparar(["Hola.", "Adiós."])
        check("al reiniciar salen del disco, sin gastar nada",
              cuenta["disco"] == 2 and len(pedidas) == 2, str(cuenta))
        check("y el audio es el mismo", otra.audio("Hola.") == b"Hola." * 10)

        voz_antes = settings.tts_voz
        settings.tts_voz = "otra-voz"
        try:
            cuenta = await FrasesFijas(Path(tmp), sintetizar=sintetizar).preparar(["Hola."])
        finally:
            settings.tts_voz = voz_antes
        check("cambiar de voz vuelve a sintetizar", cuenta["sintetizadas"] == 1, str(cuenta))

        async def falla(texto):
            from server.voz.tts import ErrorVoz
            raise ErrorVoz("Cartesia no responde")

        rota = FrasesFijas(Path(tmp) / "otra", sintetizar=falla)
        cuenta = await rota.preparar(["Nueva."])
        check("si Cartesia no responde, no tumba el arranque",
              cuenta["fallidas"] == 1 and rota.audio("Nueva.") is None, str(cuenta))


async def real() -> None:
    print(f"\n== Contra Cartesia: {settings.tts_modelo}, voz {settings.tts_voz[:8]}… ==")
    voz = VozCartesia()
    await voz.abrir()
    try:
        t0 = time.perf_counter()
        turno = voz.turno()
        await turno.decir("Qué bien que ya esté caminando.")
        await turno.decir("¿El dolor es constante o solo cuando se mueve?")
        await turno.terminar()
        primero, total = None, 0
        async for trozo in turno.audio():
            primero = primero or (time.perf_counter() - t0) * 1000
            total += len(trozo)
        segundos = total / 2 / settings.tts_sample_rate
        print(f"  primer trozo a {primero:.0f} ms · {segundos:.1f} s de audio")
        check("el primer trozo llega en menos de 600 ms", primero is not None and primero < 600,
              f"{primero}")
        check("y el turno trae el audio de las dos frases", segundos > 2.5, f"{segundos:.1f} s")

        turno = voz.turno()
        await turno.decir("Por lo que me cuenta, esto no puede esperar: comuníquese de "
                          "inmediato con su equipo clínico o acuda a urgencias.")
        iterador = turno.audio()
        await iterador.__anext__()
        await turno.cancelar()
        despues = [t async for t in iterador]
        check("tras cancelar, no llega ni un trozo más", despues == [], f"{len(despues)} trozos")
    finally:
        await voz.cerrar()

    with tempfile.TemporaryDirectory() as tmp:
        fijas = FrasesFijas(Path(tmp))
        cuenta = await fijas.preparar(["Sigo aquí."])
        check("una frase fija real se sintetiza y queda guardada",
              cuenta["sintetizadas"] == 1 and bool(fijas.audio("Sigo aquí.")), str(cuenta))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(real() if "--real" in sys.argv else mentira())
    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones de la voz.")
    return 0 if ok == len(resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
