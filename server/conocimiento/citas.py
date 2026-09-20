"""La cita la deriva el código de la evidencia, no la declara el modelo.

Traído de `server/agent/citas.py` en vera_voice_agent, con un cambio: allá los
números que el modelo manejaba eran ids de fragmento en la base, y aquí son
**posiciones dentro del turno** —«[1]», «[2]», «[3]»—. El motivo es el esquema:
enumerar los ids reales obligaría a cambiarlo en cada turno, y Claude recompila
cada esquema nuevo, lo que está medido en ~0,9 s de más en la primera frase.

**Por qué no basta con que el modelo lo declare.** Está medido en este mismo
proyecto (docs/bitacora.md, 13-sep): cuando el fragmento que responde la pregunta
no está entre los que se le permite citar, el modelo cita los otros —tres de
tres—. El formato queda perfecto y la cita es falsa. Y aun cuando sí sabe cuál
usó, a veces lo escribe en el sitio equivocado:

    «Debe avisarle a su equipo (citation_ids: #1), ya que la salida…»
    «Su dolor es leve según [#2 | plan_casero.md §Dolor]…»

El fragmento correcto está identificado, escrito dentro del texto en vez de en su
campo. Es un fallo de enrutamiento, no de comprensión. De ahí los dos trabajos de
este módulo, y el segundo es más urgente de lo que parece:

1. **Recuperar la cita** de donde el modelo la haya puesto y, si no la puso,
   derivarla del solapamiento con la evidencia recuperada.
2. **Sacar del texto lo que no se debe hablar.** Va a un sintetizador de voz: sin
   limpiarlo, el paciente oye «abre paréntesis citation ids dos». Se observó
   además una llave suelta al inicio de una respuesta —resto del JSON que el
   modelo dejó escapar dentro del campo—, que se leería igual en voz alta.

El módulo es determinista y no invoca al modelo: se prueba entero sin gastar un
turno (`evals/citas.py`).
"""
from __future__ import annotations

import re

# Marcas de cita que el modelo escribe dentro del texto, en las formas observadas.
_MARCA = re.compile(
    r"""
    \s*
    (?:
        \(\s*(?:citation_ids?|citas?|referencias?|fuentes?)\s*[:=]?\s*[^)]*\)
      | \[\s*\#?\d+[^\]]*\]
      | \(\s*\#?\d+\s*(?:,\s*\#?\d+\s*)*\)
      | (?:seg[uú]n|referencia|fuente|ver)\s+(?:el\s+)?(?:documento|fragmento)\s*\#?\d+
      | \#\d+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_ID = re.compile(r"\d+")

# Restos de estructura JSON que el modelo deja escapar dentro del texto. Se
# quitan solo en los extremos: una llave en mitad de una frase clínica sería
# rarísima, pero quitarla ahí podría alterar una cita textual del documento.
_BASURA_EXTREMOS = re.compile(r'^[\s"\'\}\]\{\[,:]+|[\s"\'\{\[]+$')

# Palabras de contenido para la atribución. Se ignoran las funcionales: aparecen
# en cualquier texto clínico y no distinguen un fragmento de otro.
_PALABRA = re.compile(r"[a-záéíóúñü]{5,}")
_VACIAS = {
    "puede", "debe", "tiene", "estar", "hacer", "sobre", "desde", "hasta",
    "cuando", "porque", "aunque", "mientras", "tambien", "también", "para",
    "como", "esta", "este", "estos", "estas", "pueden", "deben", "presenta",
    "presente", "siguiente", "siguientes", "primeros", "primera",
}


def limpiar(texto: str) -> tuple[str, list[int]]:
    """Texto hablable, y los números que traían las marcas retiradas.

    El texto limpio va al sintetizador; los números, a la verificación y a la
    traza, que es donde sirven.
    """
    numeros: list[int] = []
    for marca in _MARCA.findall(texto):
        numeros.extend(int(n) for n in _ID.findall(marca))
    limpio = _MARCA.sub("", texto)
    limpio = _BASURA_EXTREMOS.sub("", limpio)
    # La marca suele dejar un espacio antes de la puntuación, y un paréntesis
    # huérfano si envolvía solo a la referencia.
    limpio = re.sub(r"\s+([.,;:!?])", r"\1", limpio)
    limpio = re.sub(r"[(\[]\s*[)\]]", "", limpio)
    limpio = re.sub(r"\s{2,}", " ", limpio).strip()
    return limpio, list(dict.fromkeys(numeros))


def _contenido(texto: str) -> set[str]:
    return {p for p in _PALABRA.findall(texto.lower()) if p not in _VACIAS}


def atribuir(utterance: str, citas, minimo: int = 2) -> list[int]:
    """De qué fragmentos procede lo dicho, por solapamiento de contenido.

    Es la red para cuando el modelo no marcó nada. No adivina intención: mide de
    qué fragmento salen las palabras que Vera usó. Con `minimo=2` hacen falta al
    menos dos palabras de contenido compartidas, que es lo que separa «esta frase
    viene de aquí» de «las dos hablan de medicina».

    Devuelve posiciones (1..n), ordenadas por solapamiento de mayor a menor.
    """
    dichas = _contenido(utterance)
    if not dichas:
        return []
    puntuadas = []
    for posicion, c in enumerate(citas, start=1):
        comunes = dichas & _contenido(getattr(c, "texto", "") or "")
        if len(comunes) >= minimo:
            puntuadas.append((len(comunes), -posicion))
    puntuadas.sort(reverse=True)
    return [-p for _, p in puntuadas]


def derivar(utterance: str, citas, declaradas: list[int] | None = None):
    """(texto para hablar, citas verificadas).

    Orden de preferencia, de más fiable a menos:

    1. Lo que el modelo declaró en su campo —cuando acierta, es lo más directo—.
    2. Las marcas que escribió dentro del texto: misma intención, campo errado.
    3. La atribución por solapamiento, cuando no dijo nada pero sí usó la
       evidencia.

    Todo se filtra contra las posiciones que **de verdad se le mostraron en este
    turno**: una cita a algo que no vio no es una cita, es una invención. Por eso
    la lista de salida son los objetos recuperados y no los números del modelo:
    lo que sale de aquí ya está resuelto contra el índice.
    """
    limpio, en_texto = limpiar(utterance)
    valido = range(1, len(citas) + 1)

    for candidatas in (declaradas or [], en_texto, atribuir(limpio, citas)):
        posiciones = [p for p in dict.fromkeys(candidatas) if p in valido]
        if posiciones:
            return limpio, [citas[p - 1] for p in posiciones]
    return limpio, []
