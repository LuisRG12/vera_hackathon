"""Embeddings locales con fastembed (ONNX, sin torch).

Traído sin cambios de `server/knowledge/embedder.py` en vera_voice_agent.

**Documento y consulta no se embeben igual.** Los modelos entrenados para
recuperación esperan los prefijos `query:` y `passage:`, y sin ellos rinden por
debajo de lo que pueden. fastembed los aplica por modelo en `query_embed()` y
`passage_embed()`; usar `embed()` para todo desaprovecha justamente lo que
distingue a un modelo de recuperación de uno de paráfrasis.

**Por qué sigue siendo local.** Es la única pieza del sistema que no se fue a la
nube al migrar, y no por inercia: la consulta va en la ruta crítica del turno
—antes de que el modelo pueda empezar a responder—, así que un viaje de red más
se oiría. Y no cuesta nada: el modelo cabe en la memoria del despliegue y se baja
en tiempo de construcción, no en la primera llamada.
"""
from __future__ import annotations

import numpy as np
from fastembed import TextEmbedding

from server.config import settings


class Embedder:
    """Uno por proceso: cargar el modelo cuesta segundos y memoria."""

    def __init__(self, modelo: str | None = None):
        self.nombre = modelo or settings.embedding_modelo
        # `model_name` sigue decidiendo cómo se usa el modelo —prefijos,
        # pooling—; `specific_model_path` solo cambia de dónde se leen los
        # archivos.
        self._modelo = TextEmbedding(model_name=self.nombre,
                                     specific_model_path=settings.modelo_local or None)

    def fragmentos(self, textos: list[str]) -> list[np.ndarray]:
        """Lado «passage»: el texto de los documentos."""
        return [np.asarray(v, dtype=np.float32)
                for v in self._modelo.passage_embed(list(textos))]

    def consulta(self, texto: str) -> np.ndarray:
        """Lado «query»: lo que dijo el paciente."""
        return np.asarray(next(iter(self._modelo.query_embed([texto]))), dtype=np.float32)
