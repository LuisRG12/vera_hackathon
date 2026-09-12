"""Servidor de la llamada.

Etapa 1: el oído. El navegador captura el micrófono, manda PCM por WebSocket, y
este servidor lo reenvía a AssemblyAI y devuelve lo que va oyendo. Todavía no hay
diálogo ni voz — solo se comprueba que el reconocimiento en español funciona y
que los turnos cierran donde deben.

**La clave de AssemblyAI no pasa por el navegador.** El audio da este rodeo justo
para eso: el cliente habla con nosotros y nosotros con AssemblyAI.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from server.config import settings
from server.voz.keyterms import CONTEXTO_CLINICO, KEYTERMS
from server.voz.stt import ErrorSTT, crear_stt

WEB = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Vera")


@app.get("/")
async def inicio():
    return FileResponse(WEB / "index.html")


@app.get("/salud")
async def salud():
    """Para saber de un vistazo si el despliegue quedó bien configurado."""
    return JSONResponse({
        "stt": settings.stt_modelo,
        "configurado": settings.stt_configurado,
        "idiomas": settings.stt_idiomas,
        "keyterms": len(KEYTERMS),
    })


@app.websocket("/ws/llamada")
async def llamada(ws: WebSocket):
    await ws.accept()
    stt = crear_stt(keyterms=KEYTERMS, contexto=CONTEXTO_CLINICO)

    try:
        await stt.abrir()
    except ErrorSTT as e:
        await ws.send_json({"type": "error", "detalle": str(e)})
        await ws.close()
        return

    await ws.send_json({
        "type": "listo",
        "stt": stt.nombre,
        "modo": settings.stt_modo,
        "sample_rate": settings.stt_sample_rate,
    })

    # AssemblyAI factura por tiempo de conexión abierta, no por audio enviado:
    # un socket olvidado cuesta lo mismo que una conversación. Este reloj lo
    # cierra si nadie dice nada. Cuando exista el diálogo habrá que revisarlo,
    # porque entonces el silencio del paciente mientras habla el agente es parte
    # normal de la llamada.
    ultimo = asyncio.get_running_loop().time()

    async def del_navegador_a_assemblyai():
        # Colgar es el final normal de una llamada. Se atrapa aquí y no fuera
        # porque la excepción vive dentro de la tarea: si se deja escapar,
        # asyncio la guarda sin que nadie la recoja y ensucia el log con un
        # error que no lo es.
        try:
            while True:
                pcm = await ws.receive_bytes()
                await stt.enviar(pcm)
        except (WebSocketDisconnect, ErrorSTT, RuntimeError):
            return

    async def de_assemblyai_al_navegador():
        async for t in stt.eventos():
            if t.vacio:
                continue
            nonlocal ultimo
            ultimo = asyncio.get_running_loop().time()
            await ws.send_json({
                "type": "turno",
                "texto": t.texto,
                "cerrado": t.cerrado,
                "orden": t.orden,
                "idioma": t.idioma,
                "confianza_idioma": t.confianza_idioma,
            })
        # Si el reconocedor se cayó por algo, que se vea en la pantalla y no
        # solo en el log: quien prueba la llamada no está mirando la consola.
        if stt.error:
            await ws.send_json({"type": "error", "detalle": stt.error})

    async def vigilar_inactividad():
        while True:
            await asyncio.sleep(2)
            quieto = asyncio.get_running_loop().time() - ultimo
            if quieto >= settings.stt_inactividad_s:
                await ws.send_json({
                    "type": "inactiva",
                    "detalle": f"cerrada tras {int(quieto)}s sin voz, para no gastar crédito",
                })
                return

    subida = asyncio.create_task(del_navegador_a_assemblyai())
    bajada = asyncio.create_task(de_assemblyai_al_navegador())
    reloj = asyncio.create_task(vigilar_inactividad())
    try:
        # La primera que termine manda: si el navegador cuelga no tiene sentido
        # seguir esperando turnos, y si AssemblyAI cierra no hay a quién mandarle
        # el audio.
        await asyncio.wait({subida, bajada, reloj}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in (subida, bajada, reloj):
            t.cancel()
        await stt.cerrar()
