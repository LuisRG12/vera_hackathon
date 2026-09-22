"""Baja el modelo de embeddings a una carpeta propia, con archivos de verdad.

    MODELO_LOCAL=/ruta uv run scripts/modelo.py

Lo usa la imagen de Docker antes de construir el índice. En esta máquina no hace
falta: fastembed baja el modelo a su caché la primera vez y funciona.

**Por qué existe.** La primera construcción del Space falló cargando el modelo
con este error de onnxruntime:

    External data path validation failed for initializer
    embeddings.word_embeddings.weight: External data path escapes model directory

El modelo viene en dos archivos —`model.onnx`, de medio mega, y `model.onnx_data`,
con los dos gigas de pesos—, y onnxruntime exige que el segundo esté en la misma
carpeta que el primero, para que un modelo no pueda leer archivos arbitrarios del
disco. En Linux, la caché de Hugging Face guarda cada archivo como un enlace
simbólico a un *blob* con nombre de hash, y al resolver los enlaces los dos
archivos quedan en carpetas distintas. En Windows funcionaba porque ahí la caché
copia en vez de enlazar: el mismo código, un sistema de archivos distinto.

Con `local_dir` se bajan como archivos reales a una sola carpeta, y fastembed los
carga de ahí (`specific_model_path`) sin tocar su caché ni la red.

**La revisión va fijada.** El umbral de evidencia se calibró con esta revisión
exacta del modelo; si el repositorio publicara otra, el Space seguiría usando la
que se midió.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from huggingface_hub import snapshot_download  # noqa: E402

from server.config import settings  # noqa: E402

# El repositorio del que fastembed saca `intfloat/multilingual-e5-large`.
REPO = "qdrant/multilingual-e5-large-onnx"
REVISION = "66076b8dc6e367337e3e90e6fb309fb0f3addaf6"
ARCHIVOS = [
    "config.json", "model.onnx", "model.onnx_data", "sentencepiece.bpe.model",
    "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json",
]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    if not settings.modelo_local:
        print("[X] Falta MODELO_LOCAL: la carpeta donde dejar el modelo.")
        return 1
    destino = Path(settings.modelo_local)
    print(f"[..] bajando {REPO}@{REVISION[:7]} a {destino}")
    snapshot_download(REPO, revision=REVISION, local_dir=destino, allow_patterns=ARCHIVOS)

    faltan = [a for a in ARCHIVOS if not (destino / a).is_file()]
    enlaces = [a for a in ARCHIVOS if (destino / a).is_symlink()]
    if faltan or enlaces:
        print(f"[X] faltan {faltan} · enlaces simbólicos {enlaces}")
        return 1
    peso = sum((destino / a).stat().st_size for a in ARCHIVOS) / 1e9
    print(f"     {len(ARCHIVOS)} archivos reales, {peso:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
