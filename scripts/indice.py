"""Construye el índice del corpus y lo deja listo para entregar.

    uv run scripts/indice.py

Trocea cada documento declarado en `conocimiento/fuentes.json`, embebe los
fragmentos y escribe `conocimiento/indice.npz`. El índice se versiona: pesa poco
—los vectores van en media precisión— y evitarle al despliegue el trabajo de
embeber el corpus al arrancar es lo que hace que el servicio esté disponible
cuando el juez abre la URL y no medio minuto después.

Que el índice sea publicable es consecuencia de la licencia del corpus, no una
casualidad: un índice a nivel de fragmento contiene el texto de sus fuentes, así
que solo se puede publicar si ese texto se puede redistribuir.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.conocimiento.embeddings import Embedder  # noqa: E402
from server.conocimiento.indice import INDICE, construir  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    print("[..] cargando el modelo de embeddings (la primera vez lo baja)")
    embedder = Embedder()
    print(f"     {embedder.nombre}")

    print("[..] troceando y embebiendo el corpus")
    indice = construir(embedder)
    indice.guardar()

    por_documento = Counter(f.documento for f in indice.fragmentos)
    largos = [len(f.texto) for f in indice.fragmentos]
    ficticios = sum(1 for f in indice.fragmentos if f.ficticio)

    print(f"\n{len(indice)} fragmentos de {len(por_documento)} documentos")
    print(f"  {ficticios} de material de demostración, el resto de dominio público")
    print(f"  fragmento medio: {sum(largos) // len(largos)} caracteres "
          f"(el mayor, {max(largos)})")
    print(f"  índice: {INDICE} ({INDICE.stat().st_size / 1024:.0f} KB)")
    for documento, n in por_documento.most_common():
        print(f"    {n:3d}  {documento}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
