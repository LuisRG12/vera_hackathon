"""Sube a Hugging Face Spaces una instantánea de lo que está commiteado.

    uv run scripts/desplegar.py usuario/space

Antes, una vez y en su propia terminal: `hf auth login`. El token queda guardado
por la CLI de Hugging Face y este script nunca lo ve.

**Por qué una instantánea y no un `git push` del historial.** El Space es otro
repositorio git, y Hugging Face rechaza cualquier push que traiga un archivo
binario en **cualquier** commit de su historia. El índice del corpus es binario y
está versionado desde la etapa del conocimiento, así que empujar la historia se
rechazaría aunque el archivo ya no estuviera. GitHub guarda la historia; el Space
recibe solo lo que corre.

Tres cosas que la instantánea hace distinto del repositorio:

- **Sale de `git archive HEAD`, no de la carpeta de trabajo.** Lo que se despliega
  es exactamente lo commiteado: ni un cambio a medias, ni un `.env` que se colara.
- **No lleva el índice.** Lo construye la imagen al hornearse (ver Dockerfile).
- **Su README lleva la cabecera que Spaces exige** —SDK, puerto, título—. En
  GitHub esa cabecera se vería como una tabla suelta encima del título, así que
  solo existe aquí.

Cada despliegue reemplaza el contenido del Space entero (`--delete "*"`): lo que
se borró del repositorio también desaparece del Space.
"""
from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

CABECERA = """---
title: Vera
emoji: 🩺
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Post-surgical follow-up voice agent in Colombian Spanish
---

"""

# Lo que el repositorio versiona y el Space no necesita.
FUERA_DEL_SPACE = ["conocimiento/indice.npz"]


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=RAIZ, check=True, capture_output=True).stdout


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser(description="Despliega Vera en un Space de Hugging Face.")
    ap.add_argument("space", help="usuario/nombre del Space, ya creado con SDK Docker")
    ap.add_argument("--revisar", action="store_true", help="arma la instantánea y no sube")
    args = ap.parse_args()

    if _git("status", "--porcelain").strip():
        print("[!] Hay cambios sin commitear. Se despliega HEAD; lo que no esté "
              "commiteado no va a subir.")
    commit = _git("rev-parse", "--short", "HEAD").decode().strip()

    with tempfile.TemporaryDirectory(prefix="vera-space-") as tmp:
        destino = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", "HEAD"))) as tar:
            tar.extractall(destino, filter="data")
        for ruta in FUERA_DEL_SPACE:
            (destino / ruta).unlink(missing_ok=True)
        readme = destino / "README.md"
        readme.write_text(CABECERA + readme.read_text(encoding="utf-8"), encoding="utf-8")

        archivos = sorted(p.relative_to(destino).as_posix()
                          for p in destino.rglob("*") if p.is_file())
        print(f"[..] instantánea de {commit}: {len(archivos)} archivos")
        if args.revisar:
            for a in archivos:
                print(f"     {a}")
            return 0

        hf = shutil.which("hf")
        if hf is None:
            print("[X] Falta la CLI de Hugging Face: uv tool install huggingface_hub")
            return 1
        r = subprocess.run([
            hf, "upload", args.space, str(destino), ".",
            "--repo-type", "space",
            "--delete", "*",
            "--commit-message", f"Despliega {commit}",
        ])
        if r.returncode != 0:
            print("[X] La subida falló. ¿Corrió `hf auth login` y el Space existe con SDK Docker?")
            return r.returncode

    print(f"\nSubido. El Space vuelve a construir la imagen: "
          f"https://huggingface.co/spaces/{args.space}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
