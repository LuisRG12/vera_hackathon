"""El índice: los fragmentos del corpus y sus vectores, en un archivo.

**Por qué un archivo y no la base SQLite del proyecto original.** Allá el almacén
llevaba versiones, borrado por tombstone y alta en caliente, porque el evaluador
subía documentos por la consola a mitad de llamada. Aquí el corpus es fijo, vive
versionado en `conocimiento/` y se reconstruye con un script: lo que aquella
máquina resolvía —olvidar un protocolo sin reconstruir el índice— no es un
problema que este proyecto tenga todavía. Cuando lo tenga, el almacén de allá
sigue escrito.

Lo que sí se conserva de aquel diseño es guardar los vectores en **media
precisión**. Se midió sobre el corpus real y 200 consultas: el mayor cambio en
`max_dense` —la cifra contra la que se compara el umbral— es 4e-07, cuatro
órdenes de magnitud por debajo de la granularidad con la que se decide el umbral,
así que ninguna decisión de responder o abstenerse cambia. El coseno se calcula
en `float32`: media precisión acumularía error al sumar mil términos.

**El índice se construye aparte y se entrega hecho** (`scripts/indice.py`).
Embeber el corpus tarda, y hacerlo al arrancar le sumaría ese tiempo a cada
despliegue que se despierta por inactividad, que es justo cuando el juez está
esperando a que cargue.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
CORPUS = RAIZ / "conocimiento"
MANIFIESTO = CORPUS / "fuentes.json"
INDICE = CORPUS / "indice.npz"

ALMACEN_VECTOR = np.float16

# El README de `conocimiento/` explica el corpus; no es corpus. Indexarlo metería
# en el índice prosa nuestra sobre licencias, que competiría por el top-k con los
# documentos clínicos y sería citable como si fuera una fuente.
NO_ES_CORPUS = {"README.md"}


def _documentos_en_disco() -> list[Path]:
    return sorted(p for p in CORPUS.glob("*.md") if p.name not in NO_ES_CORPUS)


@dataclass(frozen=True)
class Fragmento:
    """Un trozo citable del corpus. `id` es su posición en el índice."""

    id: int
    documento: str
    titulo: str
    seccion: str
    texto: str
    # Si su documento es material de demostración y no una fuente clínica real.
    # Viaja hasta la cita a propósito: quien audita una llamada tiene que poder
    # distinguir de un vistazo lo que respalda una guía publicada de lo que
    # respalda un plan de egreso inventado para la demo.
    ficticio: bool = False


def documentos_declarados() -> dict[str, dict]:
    """El manifiesto, por nombre de archivo.

    **Un documento sin declarar no entra al índice.** Es la regla que mantiene
    honesta la promesa del corpus: si algo se puede citar, se sabe de dónde salió
    y bajo qué licencia. Un archivo suelto en la carpeta no es una fuente.
    """
    datos = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    return {d["archivo"]: d for d in datos["documentos"]}


def huella_del_corpus() -> str:
    """Huella de lo que hay en `conocimiento/`, para detectar un índice viejo."""
    h = hashlib.sha256()
    for archivo in _documentos_en_disco():
        h.update(archivo.name.encode("utf-8"))
        h.update(archivo.read_bytes())
    h.update(MANIFIESTO.read_bytes())
    return h.hexdigest()


class Indice:
    """Los fragmentos y su matriz de vectores, ya en memoria."""

    def __init__(self, fragmentos: list[Fragmento], vectores: np.ndarray,
                 modelo: str, huella: str):
        self.fragmentos = fragmentos
        self.vectores = vectores.astype(np.float32)
        self.modelo = modelo
        self.huella = huella

    def __len__(self) -> int:
        return len(self.fragmentos)

    @property
    def documentos(self) -> set[str]:
        return {f.documento for f in self.fragmentos}

    def guardar(self, ruta: Path = INDICE) -> None:
        meta = {
            "modelo": self.modelo,
            "huella": self.huella,
            "fragmentos": [f.__dict__ for f in self.fragmentos],
        }
        np.savez_compressed(
            ruta,
            vectores=self.vectores.astype(ALMACEN_VECTOR),
            # Como texto y no como objeto: un `.npz` con `allow_pickle` es código
            # ejecutable disfrazado de datos, y este archivo va al repositorio.
            meta=np.array(json.dumps(meta, ensure_ascii=False)),
        )

    @classmethod
    def cargar(cls, ruta: Path = INDICE, verificar: bool = True) -> Indice:
        """El índice entregado.

        Con `verificar`, se comprueba que corresponda al corpus que está en disco.
        **Un índice viejo no es un índice lento: es uno que cita texto que el
        documento ya no dice.** El texto del fragmento sale de aquí, así que la
        cita que el equipo clínico revisa no coincidiría con la fuente. Antes que
        arrancar con eso, se para y se dice cómo arreglarlo.
        """
        if not ruta.exists():
            raise FileNotFoundError(
                f"no hay índice en {ruta}. Constrúyalo con: uv run scripts/indice.py")
        with np.load(ruta, allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            vectores = z["vectores"]
        fragmentos = [Fragmento(**f) for f in meta["fragmentos"]]
        indice = cls(fragmentos, vectores, meta["modelo"], meta["huella"])
        if verificar and indice.huella != huella_del_corpus():
            raise ValueError(
                "el índice no corresponde al corpus de conocimiento/. "
                "Reconstrúyalo con: uv run scripts/indice.py")
        return indice


def construir(embedder) -> Indice:
    """Trocea el corpus declarado, lo embebe y devuelve el índice."""
    from server.conocimiento.troceado import trocear

    declarados = documentos_declarados()
    sueltos = {p.name for p in _documentos_en_disco()} - set(declarados)
    if sueltos:
        raise ValueError(
            f"documentos sin declarar en fuentes.json: {', '.join(sorted(sueltos))}")

    fragmentos: list[Fragmento] = []
    for archivo in sorted(declarados):
        ruta = CORPUS / archivo
        if not ruta.exists():
            raise FileNotFoundError(f"declarado en fuentes.json pero no está: {archivo}")
        meta = declarados[archivo]
        ficticio = "ficticio" in meta["licencia"]
        for seccion, texto in trocear(ruta.read_text(encoding="utf-8")):
            fragmentos.append(Fragmento(
                id=len(fragmentos), documento=archivo, titulo=meta["titulo"],
                seccion=seccion, texto=texto, ficticio=ficticio))

    vectores = np.stack(embedder.fragmentos([f.texto for f in fragmentos]))
    return Indice(fragmentos, vectores, embedder.nombre, huella_del_corpus())
