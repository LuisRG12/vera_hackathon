"""Qué le hace AssemblyAI a lo que dice el paciente antes de que el motor lo lea.

    uv run python -m evals.confusiones            # analiza lo ya medido, sin red
    uv run python -m evals.confusiones --medir    # vuelve a medir (Windows, gasta crédito)

**Por qué existe.** El bloque de `confusiones` del léxico eran defectos de Vosk:
palabras que ese reconocedor ponía en boca del paciente. AssemblyAI tendrá otros.
Heredarlos a ciegas deja las dos cosas mal: se paga el falso positivo de una
confusión que ya no ocurre y no se cubre la que sí.

**Qué mide.** Cada frase de los tres arneses del motor se sintetiza, se pasa por
el mismo `Reconocedor` que usa la llamada, y se compara lo que el motor detecta
en la frase escrita con lo que detecta en lo que entregó AssemblyAI. No se mide
la transcripción palabra por palabra sino si **la decisión** sobrevive al viaje:

  - pérdida: el texto disparaba un concepto y el audio no. Es un falso negativo
    que pone el reconocedor, y es lo que hay que cerrar.
  - ganancia: el audio dispara algo que el texto no. Una alerta de más.

El motor corre **turno por turno**, como en la llamada: si una frase llega
partida en dos turnos, una regla que necesitaba las dos mitades no la ve.

**Lo que no mide.** La voz es sintética —Helena, español de España—: sin ruido,
sin acento colombiano, sin las pausas de un paciente. Sirve para barrer muchas
frases de forma repetible. La prueba con voz real va aparte.

**Por qué se guardan las transcripciones.** Medir cuesta crédito y nueve minutos;
analizar, nada. Con lo medido guardado, cualquier cambio al léxico se comprueba
contra el mismo audio al instante, y el resultado no depende de la red.
"""
from __future__ import annotations

import asyncio
import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
import wave
from datetime import date
from pathlib import Path

from evals import alarmas_adversariales, alarmas_base, lexico_colombiano
from server.seguridad.lexico import LEXICON
from server.seguridad.reglas import SEVERITY_ORDER, compilar_termino, detect_red_flags

DATOS = Path(__file__).resolve().parent / "datos" / "confusiones_helena.json"
VOZ = "Microsoft Helena Desktop"
MS_POR_TROZO = 100
TROZO = 16000 * MS_POR_TROZO // 1000 * 2

# Frases de los arneses que no son habla sino **salida de un reconocedor**, o
# texto tecleado sin tildes. Pronunciarlas no mide nada: la voz leería «manana»
# sin eñe, y «Me dio un yello» es lo que AssemblyAI escribió, no lo que se dijo.
# Su versión hablada está en `PROPIAS` o ya en los arneses.
#
# La primera medición, del 13 de septiembre, incluyó además tres frases con
# «inspección» —la confusión heredada de Vosk—, que se retiraron del léxico con
# esa misma medición.
NO_SE_DICEN = {
    "me desmaye esta manana",
    "tengo una infeccion en la herida",
    "vision borrosa desde ayer",
}


def _es_transcripcion(texto: str) -> bool:
    """Las frases «tal como las entrega AssemblyAI» del léxico: empiezan en
    mayúscula y cierran con punto, que es como las escribe el formateo y no
    como se teclea un caso de prueba."""
    return texto[:1].isupper() and texto.endswith(".")

# Lo que este arnés necesita y los otros no tenían por qué cubrir.
PROPIAS = [
    # Lo que el paciente dijo de verdad cuando Vosk entregó «inspección».
    "he tenido infección",
    "creo que tengo una infección en la herida",
    "me desmayé esta mañana",
    "visión borrosa desde ayer",
    # Cifras. AssemblyAI formatea y puede escribirlas en dígitos; el motor se
    # calibró sobre Vosk, que las entregaba en letras.
    "tengo treinta y ocho y medio de fiebre",
    "el termómetro marcó treinta y nueve cinco",
    "el dolor está en nueve sobre diez",
    "la temperatura me llegó a cuarenta",
    # Pausas. La coma hace pausar a la voz sintética, como pausa un paciente, y
    # AssemblyAI puntúa las pausas. Varias reglas usan el punto como límite de
    # frase: si pone un punto donde el paciente solo tomó aire, la regla se
    # corta a la mitad.
    "me duele el pecho, y se me corre hacia el brazo izquierdo",
    "me duele, y me sube hasta la quijada",
    "tengo la barriga hinchada, y dura",
    "de la herida me sale un líquido, como amarillo",
    "no, no tengo fiebre",
    "se me durmió la pierna, y la tengo helada",
]


def frases() -> list[dict]:
    vistas: set[str] = set()
    salida: list[dict] = []

    def agregar(texto: str, origen: str) -> None:
        clave = texto.strip().lower()
        if clave in vistas or clave in NO_SE_DICEN or _es_transcripcion(texto):
            return
        vistas.add(clave)
        salida.append({"dicho": texto, "origen": origen})

    for t, *_ in alarmas_base.CASES:
        agregar(t, "alarmas_base")
    for t, *_ in alarmas_adversariales.FALSE_NEGATIVE_CASES:
        agregar(t, "alarmas_adversariales")
    for t, *_ in alarmas_adversariales.FALSE_POSITIVE_CASES:
        agregar(t, "alarmas_adversariales")
    for t, *_ in lexico_colombiano.POSITIVOS:
        agregar(t, "lexico_colombiano")
    for t, *_ in lexico_colombiano.NEGATIVOS:
        agregar(t, "lexico_colombiano")
    for t in PROPIAS:
        agregar(t, "confusiones")
    return salida


# --------------------------------------------------------------------- medir

def _sintetizar(textos: list[str], carpeta: Path) -> list[Path]:
    """Una sola llamada a PowerShell para todas: arrancarla cuesta un segundo."""
    rutas = [carpeta / f"{i:03d}.wav" for i in range(len(textos))]
    lista = carpeta / "frases.json"
    pares = [{"ruta": str(r), "texto": t} for r, t in zip(rutas, textos, strict=True)]
    lista.write_text(json.dumps(pares, ensure_ascii=False), encoding="utf-8")
    guion = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice("{VOZ}")
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
  [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
  [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($f in (Get-Content -Raw -Encoding UTF8 "{lista}" | ConvertFrom-Json)) {{
  $s.SetOutputToWaveFile($f.ruta, $fmt)
  $s.Speak($f.texto)
  $s.SetOutputToNull()
}}
$s.Dispose()
"""
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", guion],
                   check=True)
    return rutas


def _pcm(ruta: Path) -> bytes:
    with wave.open(str(ruta)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise ValueError(f"{ruta}: se esperaba PCM 16 kHz mono 16 bits")
        return w.readframes(w.getnframes())


async def _una_frase(rec, llegadas: asyncio.Queue, pcm: bytes) -> tuple[list[dict], float]:
    """Manda una frase, fuerza el cierre y espera a que llegue el turno cerrado.

    Devuelve lo que llegó —con su hora, relativa al inicio de la frase— y cuándo
    terminó de salir el audio.
    """
    t0 = time.perf_counter()
    mensajes: list[dict] = []
    silencio = bytes(TROZO)

    async def mandar(trozo: bytes) -> None:
        await rec.enviar(trozo)
        await asyncio.sleep(MS_POR_TROZO / 1000)
        while not llegadas.empty():
            t, turno = llegadas.get_nowait()
            if not turno.vacio:
                mensajes.append({"t": round(t - t0, 3), "texto": turno.texto,
                                 "cerrado": turno.cerrado})

    for i in range(0, len(pcm), TROZO):
        await mandar(pcm[i:i + TROZO])
    fin_audio = round(time.perf_counter() - t0, 3)

    await rec.forzar_fin_de_turno()
    limite = time.perf_counter() + 6
    while time.perf_counter() < limite:
        await mandar(silencio)
        if mensajes and mensajes[-1]["cerrado"]:
            break
    for _ in range(5):  # medio segundo entre frases
        await mandar(silencio)
    return mensajes, fin_audio


async def _transcribir(lista: list[dict], wavs: list[Path]) -> None:
    """Una sola sesión para todas las frases, como una llamada larga.

    Tras cada frase se fuerza el fin de turno y se espera a que cierre: así cada
    frase cae en sus propios turnos y lo que se oyó no se mezcla con la siguiente.
    El audio se manda a ritmo de micrófono; mandarlo más rápido no adelanta nada,
    porque el servicio lo procesa en tiempo real igual.
    """
    from server.voz.keyterms import CONTEXTO_CLINICO, KEYTERMS
    from server.voz.stt import crear_stt

    rec = crear_stt(keyterms=KEYTERMS, contexto=CONTEXTO_CLINICO)
    await rec.abrir()
    llegadas: asyncio.Queue = asyncio.Queue()

    async def consumir():
        async for t in rec.eventos():
            llegadas.put_nowait((time.perf_counter(), t))

    consumo = asyncio.create_task(consumir())
    try:
        for n, (f, wav) in enumerate(zip(lista, wavs, strict=True), 1):
            f["mensajes"], f["t_fin_audio"] = await _una_frase(rec, llegadas, _pcm(wav))
            oido = " | ".join(m["texto"] for m in f["mensajes"] if m["cerrado"]) or "(nada)"
            print(f"  [{n:3d}/{len(lista)}] {f['dicho']}\n            -> {oido}", flush=True)
            if rec.error:
                raise RuntimeError(f"AssemblyAI cerró la sesión: {rec.error}")
    finally:
        await rec.cerrar()
        consumo.cancel()


def medir() -> dict:
    from server.config import settings

    lista = frases()
    print(f"\nSintetizando {len(lista)} frases con {VOZ}…", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        wavs = _sintetizar([f["dicho"] for f in lista], Path(tmp))
        segundos = sum(len(_pcm(w)) / 32000 for w in wavs)
        print(f"{segundos:.0f} s de audio. Transcribiendo con {settings.stt_modelo} "
              f"en modo {settings.stt_modo}…\n", flush=True)
        asyncio.run(_transcribir(lista, wavs))

    from server.voz.keyterms import KEYTERMS
    datos = {
        "medido": date.today().isoformat(),
        "voz": f"{VOZ} (es-ES, sintética)",
        "modelo": settings.stt_modelo,
        "modo": settings.stt_modo,
        "keyterms": len(KEYTERMS),
        "frases": lista,
    }
    DATOS.parent.mkdir(exist_ok=True)
    DATOS.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nGuardado en {DATOS}")
    return datos


# ------------------------------------------------------------------ analizar

def _conceptos(texto: str) -> dict[str, str]:
    return {f.name: f.match for f in detect_red_flags(texto)}


def _sev(conceptos) -> str:
    sevs = [LEXICON[c]["severidad"] for c in conceptos]
    return max(sevs, key=SEVERITY_ORDER.index) if sevs else "none"


# Las confusiones declaradas, compiladas aparte para saber si alguna coincidencia
# en lo transcrito vino de ellas y no de un término del paciente.
_CONFUSIONES = {
    concepto: re.compile("|".join(compilar_termino(t) for t in cfg["confusiones"]), re.I)
    for concepto, cfg in LEXICON.items() if cfg.get("confusiones")
}


def analizar(datos: dict) -> int:
    print(f"\nMedido el {datos['medido']} · {datos['voz']} · {datos['modelo']} "
          f"({datos['modo']}) · {datos['keyterms']} keyterms")

    perdidas, bajas, ganancias, partidas = [], [], [], []
    confusiones_vistas: dict[str, list[str]] = {}
    anticipos: list[float] = []
    solo_parcial: list[tuple[str, str]] = []

    for f in datos["frases"]:
        msgs = f.get("mensajes", [])
        cerrados = [m for m in msgs if m["cerrado"]]
        en_texto = _conceptos(f["dicho"])
        en_audio: dict[str, str] = {}
        for m in cerrados:
            en_audio.update(_conceptos(m["texto"]))
            for concepto, rx in _CONFUSIONES.items():
                if rx.search(m["texto"]):
                    confusiones_vistas.setdefault(concepto, []).append(m["texto"])
        oido = " | ".join(m["texto"] for m in cerrados) or "(nada)"
        if len(cerrados) > 1:
            partidas.append(f["dicho"])

        for c in sorted(en_texto.keys() - en_audio.keys()):
            perdidas.append((f["dicho"], oido, c))
        for c in sorted(en_audio.keys() - en_texto.keys()):
            ganancias.append((f["dicho"], oido, c, en_audio[c]))
        if SEVERITY_ORDER.index(_sev(en_audio)) < SEVERITY_ORDER.index(_sev(en_texto)):
            bajas.append((f["dicho"], oido, _sev(en_texto), _sev(en_audio)))

        # ¿Cuánto antes del cierre del turno ya se veía cada señal? El motor lee
        # los parciales también, así que la alerta puede salir mientras el
        # paciente todavía habla.
        vistos: set[str] = set()
        for m in msgs:
            if m["cerrado"]:
                vistos.clear()
                continue
            for c in _conceptos(m["texto"]):
                if c in vistos:
                    continue
                vistos.add(c)
                cierre = next((x["t"] for x in msgs if x["cerrado"] and x["t"] >= m["t"]), None)
                if c not in en_audio:
                    solo_parcial.append((m["texto"], c))
                elif cierre is not None:
                    anticipos.append(cierre - m["t"])

    total = len(datos["frases"])
    print(f"\n== PÉRDIDAS: el texto disparaba y lo transcrito no ({len(perdidas)}) ==")
    for dicho, oido, c in perdidas:
        print(f"  [FALLA] {c:24s} dicho «{dicho}»\n  {'':32s}oído  «{oido}»")

    print(f"\n== BAJAS DE SEVERIDAD: lo que decide si se escala ({len(bajas)}) ==")
    for dicho, oido, antes, despues in bajas:
        print(f"  [FALLA] {antes} -> {despues}  «{dicho}» -> «{oido}»")

    print(f"\n== GANANCIAS: el audio dispara algo que el texto no ({len(ganancias)}) ==")
    for dicho, oido, c, match in ganancias:
        print(f"  [AVISO] {c:24s} por «{match}» en «{oido}» (dicho «{dicho}»)")

    print("\n== CONFUSIONES DECLARADAS EN EL LÉXICO ==")
    if not _CONFUSIONES:
        print("  ninguna")
    for concepto, cfg in LEXICON.items():
        for termino in cfg.get("confusiones", []):
            vistas = [t for t in confusiones_vistas.get(concepto, [])
                      if re.search(compilar_termino(termino), t, re.I)]
            estado = f"apareció {len(vistas)} veces" if vistas else "no apareció nunca"
            print(f"  {concepto}: «{termino}» — {estado}")

    print("\n== ANTICIPO: la señal ya estaba en un parcial antes de cerrar el turno ==")
    if anticipos:
        print(f"  {len(anticipos)} señales · mediana {statistics.median(anticipos):.2f} s "
              f"antes del cierre · máximo {max(anticipos):.2f} s")
    print(f"  señales que un parcial vio y el turno cerrado no: {len(solo_parcial)}")
    for texto, c in solo_parcial:
        print(f"    {c}: «{texto}»")

    print(f"\n  {len(partidas)} de {total} frases llegaron partidas en más de un turno.")

    ok = total - len({d for d, *_ in perdidas} | {d for d, *_ in bajas})
    print(f"\nRESULTADO: {ok}/{total} frases conservan lo que el motor detecta en el texto.")
    return 0 if not perdidas and not bajas else 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    if "--medir" in sys.argv:
        datos = medir()
    elif DATOS.exists():
        datos = json.loads(DATOS.read_text(encoding="utf-8"))
    else:
        print(f"[X] No hay medición guardada en {DATOS}. Corra con --medir.")
        return 1
    return analizar(datos)


if __name__ == "__main__":
    raise SystemExit(main())
