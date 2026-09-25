"""Deja en `audio/frases/` el audio de cada frase fija, y solo el de esas.

    uv run python scripts/frases.py

Se corre cuando cambia el texto de una frase que escribe el código, o la voz:
sintetiza lo que falte —solo eso gasta caracteres de Cartesia— y borra el audio
de textos que ya no existen, para que el repositorio no acumule versiones
viejas. Luego se commitea la carpeta. `evals/voz.py` falla si alguna frase fija
se quedó sin su archivo.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.config import settings  # noqa: E402
from server.main import FRASES_FIJAS  # noqa: E402
from server.voz.tts import CACHE, FrasesFijas  # noqa: E402


async def main() -> int:
    if not settings.tts_configurado:
        print("[x] falta CARTESIA_API_KEY en el .env")
        return 1
    cuenta = await FrasesFijas().preparar(FRASES_FIJAS)
    vigentes = {f"{FrasesFijas.clave(t)}.pcm" for t in FRASES_FIJAS}
    viejas = [p for p in CACHE.glob("*.pcm") if p.name not in vigentes]
    for p in viejas:
        p.unlink()
    print(f"[ok] {len(vigentes)} frases: {cuenta['disco']} ya estaban, "
          f"{cuenta['sintetizadas']} sintetizadas, {cuenta['fallidas']} fallidas; "
          f"{len(viejas)} audios viejos borrados")
    return 1 if cuenta["fallidas"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
