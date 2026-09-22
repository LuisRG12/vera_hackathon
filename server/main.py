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

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from server.config import settings
from server.dialogo.prompts import (
    CIERRE,
    DEGRADADO,
    DEGRADADO_CON_ALARMA,
    DESPEDIDA_FINAL,
    LIMITE,
    RETOMAR_SILENCIO,
    SALUDO,
    SIN_INFORMACION,
    SIN_OIDO,
    SIN_RESPUESTA,
)
from server.dialogo.turno import Conversacion
from server.limites import Cupo, Presupuesto
from server.modelo.llm import StructuredLLM
from server.seguridad.lexico import LEXICON
from server.seguridad.respuestas import (
    ACOMPANAR,
    CIERRE_ACOMPANAR,
    CIERRE_ALARMA,
    CIERRE_EMERGENCIA,
    EMERGENCIA,
    RETOMAR_ACOMPANAR,
)
from server.voz.keyterms import KEYTERMS
from server.voz.sesion import SesionLlamada, turno_json
from server.voz.tts import FrasesFijas

WEB = Path(__file__).resolve().parent.parent / "web"

# Lo que Vera dice escrito por el código. Se sintetiza al arrancar y queda en
# disco: suena al instante y suena aunque Cartesia se caiga, que es justo cuando
# más falta hacen la emergencia y los respaldos.
#
# `SIN_INFORMACION` entró con la etapa del conocimiento y se quedó fuera de esta
# lista: la escribe el código, pero sonaba sintetizada en vivo, así que tardaba
# lo que tarda Cartesia y no sonaba si Cartesia se caía.
FRASES_FIJAS = [SALUDO, EMERGENCIA, ACOMPANAR, DEGRADADO, DEGRADADO_CON_ALARMA,
                SIN_RESPUESTA, RETOMAR_SILENCIO, DESPEDIDA_FINAL, SIN_OIDO,
                SIN_INFORMACION, LIMITE, CIERRE, CIERRE_ALARMA, CIERRE_EMERGENCIA,
                RETOMAR_ACOMPANAR, CIERRE_ACOMPANAR]


@asynccontextmanager
async def ciclo(app: FastAPI):
    # Un cliente del modelo por proceso: la conexión con el gateway se reutiliza
    # entre turnos y entre llamadas. Medido, abrirla de nuevo en cada petición
    # costaba medio segundo hasta la primera frase.
    app.state.llm = StructuredLLM()
    app.state.recuperador = _abrir_conocimiento()
    app.state.cupo = Cupo(settings.max_llamadas_simultaneas)
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
        # Una consulta de prueba al arrancar: si el modelo no corre, se sabe aquí
        # y no con el primer paciente, y su tiempo queda en el registro.
        #
        # Nació como calentamiento, por una hipótesis que la medición descartó.
        # En la primera llamada por voz contra el Space, la abstención —que solo
        # espera a la recuperación— tardó tres segundos en empezar a sonar. Se
        # supuso que era la primera inferencia leyendo los pesos del disco en
        # frío; medida, cuesta 64 ms en esta máquina y 87 en el Space. Los tres
        # segundos siguen sin explicación, y por eso la página muestra ahora
        # cuánto tarda la recuperación en cada turno.
        t0 = time.perf_counter()
        recuperador.consultar("hola")
        print(f"[conocimiento] {len(indice)} fragmentos de "
              f"{len(indice.documentos)} documentos · calentado en "
              f"{(time.perf_counter() - t0) * 1000:.0f} ms", flush=True)
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
        "llamadas": {"en_curso": app.state.cupo.en_curso,
                     "maximo": app.state.cupo.maximo},
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


@app.get("/documentos/{archivo}")
async def documento(archivo: str):
    """El documento citado, tal como se indexó, para seguir una cita hasta su fuente.

    Solo sirve lo declarado en `fuentes.json`: el nombre se busca en el
    manifiesto y no se usa como ruta, así que no hay forma de pedir otro archivo.
    La cabecera con fuente y licencia la pone esta respuesta, no el documento:
    en el documento se indexaría y competiría con el texto clínico.
    """
    from server.conocimiento.indice import CORPUS, documentos_declarados
    meta = documentos_declarados().get(archivo)
    if meta is None:
        return PlainTextResponse("documento no declarado en el corpus", status_code=404)
    cabecera = "\n".join(filter(None, [
        meta["titulo"],
        f"Fuente: {meta['fuente']}",
        meta.get("url"),
        f"Licencia: {meta['licencia']}",
    ]))
    texto = (CORPUS / archivo).read_text(encoding="utf-8")
    return PlainTextResponse(f"{cabecera}\n\n{'─' * 60}\n\n{texto}")


@app.post("/traducir")
async def traducir_transcripcion(pedido: dict):
    """La transcripción en inglés, a demanda. Ver server/traduccion.py."""
    from server.traduccion import traducir
    # Sin filtrar las vacías: la respuesta se alinea por posición con lo que
    # hay en pantalla, y quitar una desplazaría todas las traducciones.
    lineas = [str(x) for x in (pedido.get("lineas") or [])]
    try:
        return JSONResponse({"traducciones": await traducir(app.state.llm, lineas)})
    except Exception as exc:  # noqa: BLE001 — la pantalla lo dice; la llamada no se entera
        return JSONResponse({"error": f"{type(exc).__name__}: {str(exc)[:200]}"},
                            status_code=502)


@app.websocket("/ws/llamada")
async def llamada(ws: WebSocket):
    await SesionLlamada(ws, ws.app.state).atender()


@app.websocket("/ws/texto")
async def texto(ws: WebSocket):
    """Una conversación por conexión, como lo es cada llamada."""
    await ws.accept()
    conversacion = Conversacion(ws.app.state.llm,
                                recuperador=ws.app.state.recuperador)
    # Por texto no hay audio que facturar, pero cada turno son dos peticiones
    # al gateway, y una pestaña con un script puede hacer cientos.
    presupuesto = Presupuesto(settings.max_turnos_llamada, settings.max_minutos_llamada * 60)
    try:
        while True:
            m = await ws.receive_json()
            dicho = (m.get("texto") or "").strip()
            if not dicho:
                continue
            presupuesto.registrar_turno()
            if motivo := presupuesto.excedido():
                await ws.send_json({"type": "speak", "texto": LIMITE})
                await ws.send_json({"type": "adios", "motivo": motivo})
                await ws.close()
                return
            async for tipo, dato in conversacion.turno(dicho):
                if tipo == "speak":
                    await ws.send_json({"type": "speak", "texto": dato})
                else:
                    await ws.send_json({"type": "turno", **turno_json(dato)})
            if conversacion.terminada:
                await ws.send_json({"type": "adios", "motivo": "cierre"})
                await ws.close()
                return
    except WebSocketDisconnect:
        return
