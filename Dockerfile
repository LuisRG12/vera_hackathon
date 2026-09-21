# Vera en Hugging Face Spaces (SDK Docker).
#
# Tres restricciones del destino, asumidas en docs/arquitectura.md (decisión 7):
#
# - El contenedor corre como el usuario 1000, no como root. Todo lo que la
#   aplicación escriba en tiempo de ejecución —la caché de las frases fijas— tiene
#   que ser de ese usuario, o el arranque falla al crear la carpeta.
# - El disco es efímero y el Space se suspende por inactividad. Lo pesado se hace
#   al construir la imagen, no al arrancar: si el modelo de embeddings (2 GB) se
#   bajara en el primer arranque, el juez que abre la URL esperaría un minuto.
# - El puerto es el 7860.

FROM python:3.12-slim

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/app/.venv/bin:/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    MODELOS_DIR=/home/user/modelos

# La carpeta la crea el propio usuario: un WORKDIR que no existe lo crea root, y
# entonces la aplicación no podría escribir en ella.
RUN mkdir -p /home/user/app /home/user/modelos
WORKDIR /home/user/app

# Las dependencias van antes que el código: cambiar una línea del servidor no
# vuelve a instalar nada. La versión de uv es la que generó el lock.
RUN pip install --user --no-cache-dir uv==0.12.5
COPY --chown=user pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY --chown=user . .

# Baja el modelo de embeddings a MODELOS_DIR y construye el índice del corpus.
# El índice se construye aquí y no se copia del repositorio: así corresponde por
# construcción al corpus que va en la imagen, y el repositorio del Space no
# necesita guardar ningún binario.
RUN python scripts/indice.py

EXPOSE 7860
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "7860"]
