"""Configuración. Todo con un default que funciona; el .env solo ajusta.

La única variable sin default es la clave de AssemblyAI, porque no hay valor
razonable que inventar: sin ella el agente no oye.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    host: str = "127.0.0.1"
    port: int = 7860  # el que espera Hugging Face Spaces

    # --- Reconocimiento de voz (AssemblyAI Universal-Streaming) ---
    assemblyai_api_key: str = ""

    # El endpoint es v3. La página de transcripción multilingüe muestra un
    # `api.assemblyai.com/v2/realtime` que responde 404: se comprobó conectando
    # (ver docs/bitacora.md, 12-sep). Esto no se cambia por lo que diga una doc.
    stt_url: str = "wss://streaming.assemblyai.com/v3/ws"

    # `universal-3-5-pro` ya es el default del servidor, y aun así se declara.
    # Un default que cambie sin avisar no debe cambiar cómo oye el agente.
    stt_modelo: str = "universal-3-5-pro"
    stt_idiomas: str = "es"
    stt_sample_rate: int = 16000

    # Cómo decide dónde termina un turno.
    #
    # El modo por defecto del servicio es `balanced`, que corre la comprobación
    # de fin de turno tras 128 ms de silencio. Un paciente recién operado
    # —con dolor, mayor, buscando la palabra— pausa mucho más que eso, y la
    # frase le sale partida en pedazos. Es el mismo hallazgo que ya se había
    # medido con el reconocedor anterior, donde el umbral tuvo que subir de
    # 0,6 s a 1,1 s al pasar de audio sintético a micrófono real.
    #
    # `max_accuracy` sube esa comprobación a 512 ms y el corte forzado a
    # 2560 ms. Se paga algo de latencia y se compra que al paciente no se le
    # interrumpa a media idea, que en esta conversación vale más.
    stt_modo: str = "max_accuracy"

    # Se factura por tiempo de conexión abierta, no por audio enviado. Un socket
    # olvidado cuesta igual que una conversación. Esto lo cierra solo.
    stt_inactividad_s: float = 45.0

    @property
    def stt_configurado(self) -> bool:
        return bool(self.assemblyai_api_key)


settings = Settings()
