"""Servidor de Vera.

Dos entradas:

- `/ws/llamada`: la llamada por voz. El navegador manda el micrófono, el servidor
  lo reenvía a AssemblyAI, el motor determinista lee cada cosa que se oye antes
  de que la vea ningún modelo, y Vera contesta con su voz. Ver `server/voz/sesion.py`.
- `/ws/texto`: la misma conversación por texto —reglas, juez y respuesta—, para
  probar la cabeza sin micrófono.

**Las claves no pasan por el navegador.** El audio da este rodeo justo para eso:
el cliente habla con nosotros y nosotros con AssemblyAI y con Cartesia.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from server.config import settings
from server.dialogo.prompts import (
    DEGRADADO,
    DEGRADADO_CON_ALARMA,
    DESPEDIDA_FINAL,
    RETOMAR_SILENCIO,
    SALUDO,
    SIN_OIDO,
    SIN_RESPUESTA,
)
from server.dialogo.turno import Conversacion
from server.modelo.llm import StructuredLLM
from server.seguridad.lexico import LEXICON
from server.seguridad.respuestas import ACOMPANAR, EMERGENCIA
from server.voz.keyterms import KEYTERMS
from server.voz.sesion import SesionLlamada, turno_json
from server.voz.tts import FrasesFijas

WEB = Path(__file__).resolve().parent.parent / "web"

# Lo que Vera dice escrito por el código. Se sintetiza al arrancar y queda en
# disco: suena al instante y suena aunque Cartesia se caiga, que es justo cuando
# más falta hacen la emergencia y los respaldos.
FRASES_FIJAS = [SALUDO, EMERGENCIA, ACOMPANAR, DEGRADADO, DEGRADADO_CON_ALARMA,
                SIN_RESPUESTA, RETOMAR_SILENCIO, DESPEDIDA_FINAL, SIN_OIDO]


@asynccontextmanager
async def ciclo(app: FastAPI):
    # Un cliente del modelo por proceso: la conexión con el gateway se reutiliza
    # entre turnos y entre llamadas. Medido, abrirla de nuevo en cada petición
    # costaba medio segundo hasta la primera frase.
    app.state.llm = StructuredLLM()
    app.state.recuperador = _abrir_conocimiento()
    app.state.fijas = FrasesFijas()
    app.state.fijas_listas = await app.state.fijas.preparar(FRASES_FIJAS) \
        if settings.tts_configurado else {"sin_clave": len(FRASES_FIJAS)}
    yield
    await app.state.llm.aclose()


def _abrir_conocimiento():
    """El índice y el modelo de embeddings, una vez por proceso.

    **Sin conocimiento la llamada sigue, sin poder afirmar nada clínico.** Es la
    misma degradación que el proyecto ya tiene definida para el oído y para la
    voz: Vera pregunta, escucha y escala igual —la capa determinista no depende
    de esto—, pero cada pregunta cae en la respuesta que dice que no lo tiene en
    sus documentos, que es la conducta correcta cuando de verdad no lo tiene.
    Caerse al arrancar sería peor: dejaría al juez sin demo por una pieza que no
    decide la seguridad de nadie.
    """
    from server.conocimiento.embeddings import Embedder
    from server.conocimiento.indice import Indice
    from server.conocimiento.recuperacion import Recuperador
    try:
        indice = Indice.cargar()
        recuperador = Recuperador(indice, Embedder())
        print(f"[conocimiento] {len(indice)} fragmentos de "
              f"{len(indice.documentos)} documentos", flush=True)
        return recuperador
    except Exception as exc:  # noqa: BLE001 — se degrada, no tumba el servidor
        print(f"[conocimiento] sin índice ({type(exc).__name__}: {exc}); "
              f"Vera no podrá citar documentos", flush=True)
        return None


app = FastAPI(title="Vera", lifespan=ciclo)


@app.get("/")
async def inicio():
    # Sin esto el navegador se queda con la copia vieja y uno prueba un cambio
    # que no está corriendo. Es una página de kilobytes: no hay nada que ahorrar
    # cacheándola, y sí mucho que perder.
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/salud")
async def salud():
    """Para saber de un vistazo si el despliegue quedó bien configurado."""
    return JSONResponse({
        "stt": settings.stt_modelo,
        "configurado": settings.stt_configurado,
        "idiomas": settings.stt_idiomas,
        "keyterms": len(KEYTERMS),
        "lexico": len(LEXICON),
        "llm": settings.llm_modelo,
        "voz": settings.tts_voz if settings.tts_configurado else None,
        "frases_fijas": app.state.fijas_listas,
        "registro_turnos": settings.registro_turnos,
        "conocimiento": _estado_conocimiento(),
    })


def _estado_conocimiento() -> dict:
    rec = app.state.recuperador
    if rec is None:
        return {"cargado": False}
    return {
        "cargado": True,
        "fragmentos": len(rec.indice),
        "documentos": len(rec.indice.documentos),
        "embeddings": rec.indice.modelo,
        "umbral": settings.min_evidencia,
    }


@app.websocket("/ws/llamada")
async def llamada(ws: WebSocket):
    await SesionLlamada(ws, ws.app.state).atender()


@app.websocket("/ws/texto")
async def texto(ws: WebSocket):
    """Una conversación por conexión, como lo es cada llamada."""
    await ws.accept()
    conversacion = Conversacion(ws.app.state.llm,
                                recuperador=ws.app.state.recuperador)
    try:
        while True:
            m = await ws.receive_json()
            dicho = (m.get("texto") or "").strip()
            if not dicho:
                continue
            async for tipo, dato in conversacion.turno(dicho):
                if tipo == "speak":
                    await ws.send_json({"type": "speak", "texto": dato})
                else:
                    await ws.send_json({"type": "turno", **turno_json(dato)})
    except WebSocketDisconnect:
        return
