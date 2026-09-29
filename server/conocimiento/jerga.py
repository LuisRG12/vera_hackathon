"""Cómo dice un paciente lo que el corpus nombra de otra manera, para buscar.

**El mismo puente que `server/seguridad/lexico.py`, pero para la búsqueda.** Allá
«botando materia» lleva al concepto `infeccion` y a una alerta; acá «hacer del
cuerpo» lleva a «evacuar», que es la palabra del documento. No va en el léxico
de alarmas porque ninguna de estas frases es un signo de alarma: cada entrada
de allá tiene una severidad, y aquí no hay ninguna que ponerle.

**Por qué hace falta.** El modelo de embeddings no conoce el habla colombiana y
el corpus está escrito en español de guía clínica. Medido con el índice actual:
a «¿qué hago si no he podido hacer del cuerpo?» el mejor fragmento era la sección
«Actividad» del plan, y la guía de estreñimiento ni entraba; a «¿me puedo quitar
las curitas?» no entraba «La cirugía y el alta», que es la sección que dice
cuándo se retiran los apósitos. Con «apósitos» en la pregunta, esa sección sale
primera con 0,839.

**Solo añade, no reemplaza.** La consulta conserva lo que dijo el paciente y
suma la palabra del corpus al final: si la entrada estuviera mal, lo peor que
hace es sumar una palabra de más. Y solo puede sumar palabras declaradas aquí.

Frases planas, insensibles a tildes, con límite de palabra; `*` al final es
prefijo. Un clínico puede ampliarla sin tocar código.
"""
from __future__ import annotations

import re
import unicodedata

# frase del paciente -> palabras del corpus que se suman a la consulta
JERGA: dict[str, str] = {
    "hacer del cuerpo": "evacuar estreñimiento",
    "hacer popo": "evacuar estreñimiento",
    "hacer pupu": "evacuar estreñimiento",
    "no he podido obrar": "evacuar estreñimiento",
    "curita*": "apósitos",
    "bandita*": "apósitos",
    "calentura": "fiebre",
    # «Materia» no entra suelta: «materia fecal» también se dice, y sumaría
    # «pus». La infección ya la atrapan las reglas, con su propio léxico.
}


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


def _compilar(frase: str) -> re.Pattern:
    prefijo = frase.endswith("*")
    cuerpo = r"\s+".join(re.escape(p) for p in _sin_tildes(frase.rstrip("*")).split())
    return re.compile(rf"\b{cuerpo}" + (r"\w*" if prefijo else r"\b"))


_PATRONES = [(_compilar(f), palabras) for f, palabras in JERGA.items()]


def ampliar(texto: str) -> str:
    """La consulta con las palabras del corpus que corresponden a su jerga."""
    plano = _sin_tildes(texto)
    suma = [palabras for patron, palabras in _PATRONES if patron.search(plano)]
    return " ".join([texto, *dict.fromkeys(suma)]) if suma else texto
