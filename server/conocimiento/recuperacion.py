"""Recuperación híbrida y el veredicto de si hay con qué responder.

Traído de `server/knowledge/{retriever,service}.py` en vera_voice_agent, juntos:
allá estaban separados porque el servicio orquestaba además altas, bajas y
versiones sobre SQLite, que aquí no existen.

**Denso más disperso, fusionados con RRF.** Sobre los rangos y no sobre los
puntajes, porque las dos señales viven en escalas que no son comparables. Los
términos clínicos exactos —«pus», «fiebre», «38»— son justo lo que un modelo
denso pequeño diluye y BM25 clava.

**El veredicto lo da el código, no el modelo.** Que haya fragmentos no significa
que respondan: el índice siempre devuelve sus mejores k, así que siempre hay
texto clínico delante. Quien decide si eso es evidencia es el umbral, y esa
decisión es la que mantiene a Vera callada cuando el corpus no sabe.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi

from server.config import settings
from server.conocimiento.indice import Fragmento, Indice


@dataclass
class Cita:
    """Un fragmento recuperado, con lo que lo puso ahí."""

    fragmento: Fragmento
    denso: float
    disperso: float
    puntaje: float

    @property
    def id(self) -> int:
        return self.fragmento.id

    @property
    def texto(self) -> str:
        return self.fragmento.texto

    @property
    def referencia(self) -> str:
        """Cómo se nombra la fuente en la traza y en el panel del equipo clínico."""
        return f"{self.fragmento.documento} §{self.fragmento.seccion}"


# Palabras funcionales frecuentes que no cuentan como evidencia léxica: aparecen
# en cualquier texto clínico y por tanto no distinguen un fragmento de otro.
_VACIAS = {
    "para", "como", "pero", "porque", "cuando", "cuanto", "donde", "esto", "esta",
    "este", "tengo", "puedo", "puede", "debo", "hacer", "sobre", "segun", "desde",
    "hasta", "despues", "quiero", "necesito", "tener", "estoy", "siento", "muy",
    "mas", "algo", "cosa", "hola",
}


def _palabras(texto: str) -> set[str]:
    return {p for p in re.findall(r"[a-záéíóúñü]+", texto.lower())
            if len(p) >= 4 and p not in _VACIAS}


def _tok(s: str) -> list[str]:
    return re.findall(r"\w+", s.lower())


def _coseno(mat: np.ndarray, vec: np.ndarray) -> np.ndarray:
    mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    vn = vec / (np.linalg.norm(vec) + 1e-9)
    return mn @ vn


def _rangos(puntajes: np.ndarray) -> np.ndarray:
    """Rango de cada elemento, **dando el mismo rango a los empatados**.

    Parece un detalle de implementación y era el defecto más caro de esta capa.
    RRF suma el inverso del rango de cada señal, así que necesita que los rangos
    signifiquen algo. `argsort` sobre una señal empatada no dice que estén
    empatados: los ordena por su posición en el índice y produce un ranking
    completo, inventado, que RRF pondera igual que el bueno.

    Dónde se vio: a «¿cuándo me puedo bañar?» **ninguna** palabra de la consulta
    aparece en el corpus —el plan de egreso dice «ducharse»—, así que BM25 dio
    cero a los 133 fragmentos. El coseno tenía la respuesta en primer lugar con
    0,852, muy por encima del umbral; la fusión con ese ranking falso la empujó
    fuera de los tres que ve el modelo, y el veredicto de evidencia pasó a
    calcularse sobre 0,804. Vera se abstenía teniendo el pasaje delante.

    Con rangos por empate, una señal sin información reparte el mismo sumando
    entre todos y deja decidir a la otra, que es lo que RRF debía hacer desde el
    principio. No hay caso especial para «BM25 dio cero»: sale solo.
    """
    orden = np.argsort(-puntajes, kind="stable")
    rangos = np.empty(len(puntajes), dtype=int)
    i = 0
    while i < len(orden):
        j = i
        while j + 1 < len(orden) and puntajes[orden[j + 1]] == puntajes[orden[i]]:
            j += 1
        rangos[orden[i:j + 1]] = i
        i = j + 1
    return rangos


@dataclass
class Recuperado:
    """Lo recuperado y si alcanza para afirmar algo."""

    citas: list[Cita]
    hay_evidencia: bool
    max_denso: float
    solape_lexico: int


class Recuperador:
    def __init__(self, indice: Indice, embedder, rrf_k: int = 60):
        self.indice = indice
        self.embedder = embedder
        self.rrf_k = rrf_k
        # BM25 se construye una vez: el corpus no cambia mientras el proceso vive.
        # En el proyecto original se rehacía en cada consulta porque el índice sí
        # cambiaba —el evaluador subía documentos a mitad de llamada—, y eso
        # costaba milisegundos sobre diez mil fragmentos. Aquí sería pagar por una
        # flexibilidad que este corpus no tiene.
        self._bm25 = BM25Okapi([_tok(f.texto) for f in indice.fragmentos])

    def consultar(self, texto: str, k: int | None = None) -> Recuperado:
        k = k or settings.k_recuperados
        fragmentos = self.indice.fragmentos
        if not fragmentos:
            return Recuperado([], False, 0.0, 0)

        denso = _coseno(self.indice.vectores, self.embedder.consulta(texto))
        disperso = np.asarray(self._bm25.get_scores(_tok(texto)), dtype=np.float32)

        rd, rs = _rangos(denso), _rangos(disperso)
        rrf = 1.0 / (self.rrf_k + rd + 1) + 1.0 / (self.rrf_k + rs + 1)

        citas = [Cita(fragmentos[i], float(denso[i]), float(disperso[i]), float(rrf[i]))
                 for i in np.argsort(-rrf)[:k]]
        return self._veredicto(texto, citas)

    def _veredicto(self, texto: str, citas: list[Cita]) -> Recuperado:
        """Si lo recuperado constituye evidencia suficiente.

        La evidencia es híbrida a propósito: **semántica** —el mejor puntaje denso
        supera el umbral— **o léxica** —la consulta comparte términos clínicos
        exactos con un fragmento del top—. Los signos de alarma son palabras
        exactas que un modelo denso diluye; el solapamiento léxico las rescata.

        **El veredicto se juzga sobre los fragmentos que verá el modelo, no sobre
        todos los recuperados.** Parece un detalle y era un defecto real en el
        proyecto original: se recuperaba con k=8, se le pasaban 3 al modelo y la
        calibración medía con 5. Tres anchos para la misma decisión, y `max_denso`
        crece con el ancho porque es un máximo. Una pregunta fuera de corpus
        puntuaba 0,813 sobre el top 3 y 0,825 sobre el top 8: el arnés decía que
        el agente se abstenía y en la llamada afirmó sin una sola cita.
        """
        juzgadas = citas[:settings.k_evidencia]
        max_denso = max((c.denso for c in juzgadas), default=0.0)
        consulta = _palabras(texto)
        lexico = max((len(consulta & _palabras(c.texto)) for c in juzgadas), default=0)
        return Recuperado(
            citas=citas,
            hay_evidencia=(max_denso >= settings.min_evidencia
                           or lexico >= settings.min_lexico),
            max_denso=max_denso,
            solape_lexico=lexico,
        )


def formatear(citas: list[Cita]) -> str:
    """Los fragmentos como los ve el modelo.

    El número es **posicional dentro del turno** y no el id del fragmento en el
    índice. Así el esquema de la respuesta puede declarar qué valores son válidos
    sin cambiar de un turno a otro: cambiarlo obligaría a Claude a recompilarlo, y
    eso está medido en ~0,9 s de más en la primera frase.
    """
    if not citas:
        return "(sin fragmentos relevantes en la base de conocimiento)"
    return "\n".join(
        f"[{i}] ({c.referencia}) {c.texto}" for i, c in enumerate(citas, start=1))
