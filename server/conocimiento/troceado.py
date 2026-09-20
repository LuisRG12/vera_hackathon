"""Partir un documento en fragmentos citables.

Traído de `server/knowledge/chunker.py` en vera_voice_agent, **sin la mitad que
reparaba PDFs**. Allá el corpus eran PDFs de terceros y la mayor parte del módulo
era reparación: detectar escaneos sin capa de texto, rejuntar documentos que el
extractor entregaba letra por letra, abrir rutas de más de 260 caracteres en
Windows. Nada de eso aplica a un corpus que este proyecto escribe desde sus
fuentes en markdown (`scripts/corpus.py`), y traerlo sería cargar con defensas
contra un problema que ya no existe.

Lo que sí se conserva entero es el troceado, porque su forma la decidieron
mediciones que siguen valiendo: se respetan las secciones que el documento
declara, y dentro de cada una se hacen ventanas por frase con solape.

El **fragmento es la unidad de la cita**, así que su tamaño no es un parámetro de
rendimiento: un fragmento demasiado grande cita un párrafo entero para respaldar
media frase, y uno demasiado pequeño parte el pasaje que responde.
"""
from __future__ import annotations

import re

_ENCABEZADO = re.compile(r"^(#{1,6})\s+(.*)$")


def _secciones(texto: str) -> list[tuple[str, str]]:
    """(encabezado, cuerpo) por cada sección declarada del documento."""
    secciones: list[tuple[str, str]] = []
    encabezado = ""
    buf: list[str] = []
    for linea in texto.splitlines():
        m = _ENCABEZADO.match(linea.strip())
        if m:
            if buf:
                secciones.append((encabezado, "\n".join(buf).strip()))
                buf = []
            encabezado = m.group(2).strip()
        else:
            buf.append(linea)
    if buf:
        secciones.append((encabezado, "\n".join(buf).strip()))
    return [(h, c) for h, c in secciones if c]


def _cola(texto: str, solape: int) -> str:
    """Cola de solape que empieza en palabra completa.

    Cortar el solape como `texto[-solape:]` —N caracteres a ciegas— parece inocuo
    y no lo es: deja que los fragmentos empiecen a media palabra («ue resalta la
    severidad…»). El embedding se calcula sobre ese texto, así que un fragmento
    que abre con un trozo de palabra inexistente queda con el vector corrido
    respecto al mismo pasaje bien cortado.

    Se prefiere empezar en frase; si no hay ninguna dentro de la ventana, en la
    siguiente palabra completa.
    """
    if len(texto) <= solape:
        return texto
    trozo = texto[-solape:]
    if (fin := re.search(r"(?<=[.?!])\s+", trozo)) is not None:
        return trozo[fin.end():]
    corte = trozo.find(" ")
    return trozo[corte + 1:] if corte >= 0 else ""


def _partir_larga(s: str, max_chars: int) -> list[str]:
    """Parte por palabras una frase más larga que la ventana.

    Hace falta por las listas: una viñeta no lleva punto final, así que una
    sección de viñetas es para el partidor de frases una sola frase larguísima.
    """
    if len(s) <= max_chars:
        return [s]
    trozos, cur = [], ""
    for palabra in s.split():
        if cur and len(cur) + len(palabra) + 1 > max_chars:
            trozos.append(cur)
            cur = palabra
        else:
            cur = f"{cur} {palabra}".strip()
    if cur:
        trozos.append(cur)
    return trozos


def _ventanas(texto: str, max_chars: int, solape: int) -> list[str]:
    frases = re.split(r"(?<=[.?!])\s+", texto.replace("\n", " ").strip())
    sueltas = [t for f in frases for t in _partir_larga(f, max_chars)]
    trozos: list[str] = []
    cur = ""
    for f in sueltas:
        if cur and len(cur) + len(f) + 1 > max_chars:
            trozos.append(cur.strip())
            cur = (_cola(cur, solape) + " " + f).strip()
        else:
            cur = (cur + " " + f).strip()
    if cur.strip():
        trozos.append(cur.strip())
    return trozos


def _etiqueta(fragmento: str, i: int, total: int) -> str:
    """Referencia para un fragmento sin encabezado propio.

    Antes que inventar una estructura con heurísticas frágiles, se cita la
    posición y las primeras palabras: es verificable y le permite al equipo
    clínico encontrar el pasaje en el documento real.
    """
    inicio = " ".join(fragmento.split()[:6])
    return f"parte {i}/{total} · {inicio}…" if inicio else f"parte {i}/{total}"


def trocear(texto: str, max_chars: int = 700, solape: int = 120) -> list[tuple[str, str]]:
    """(sección, texto) de cada fragmento citable del documento."""
    salida: list[tuple[str, str]] = []
    for encabezado, cuerpo in _secciones(texto):
        for v in _ventanas(cuerpo, max_chars, solape):
            salida.append((encabezado, v))

    sin_seccion = [i for i, (h, _) in enumerate(salida) if not h]
    for n, i in enumerate(sin_seccion, start=1):
        salida[i] = (_etiqueta(salida[i][1], n, len(sin_seccion)), salida[i][1])

    if not salida and texto.strip():
        ventanas = _ventanas(texto, max_chars, solape)
        salida = [(_etiqueta(v, i, len(ventanas)), v)
                  for i, v in enumerate(ventanas, start=1)]
    return salida
