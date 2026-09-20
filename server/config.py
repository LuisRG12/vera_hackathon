"""Configuración. Todo con un default que funciona; el .env solo ajusta.

Las únicas variables sin default son las dos claves, porque no hay valor
razonable que inventar: sin la de AssemblyAI el agente no oye ni piensa, y sin la
de Cartesia no habla.
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

    # Cuánto aguanta Vera el silencio del paciente. 20 s para retomar: es mucho
    # más que una pausa para pensar y bastante menos de lo que aguanta alguien al
    # teléfono antes de creer que se cortó. Otros 30 s para cerrar, porque quien
    # no contestó a la primera casi nunca contesta a la segunda.
    #
    # Cerrar la llamada también es lo que evita pagar una conexión abierta que
    # nadie usa: AssemblyAI factura por tiempo, no por audio enviado.
    silencio_retomar_s: float = 20.0
    silencio_cerrar_s: float = 30.0

    # --- Modelo de lenguaje: Claude por el LLM Gateway de AssemblyAI ---
    # La misma clave de AssemblyAI; el gateway exige pago por uso habilitado.
    llm_url: str = "https://llm-gateway.assemblyai.com/v1/chat/completions"

    # Haiku y no Sonnet, medido (ver docs/bitacora.md, 13-sep): el primer token
    # llega en 1,4 s contra 2,2 s, y Sonnet por el gateway no acepta salida
    # estructurada. El id va tal como lo lista el gateway, con fecha.
    llm_modelo: str = "claude-haiku-4-5-20251001"

    # Con un modelo en la nube esperar mucho no es prudencia: es una llamada
    # colgada. Si no contesta a tiempo, la decisión sale con lo que vieron las
    # reglas, que ya evaluaron antes de preguntarle.
    llm_timeout_s: float = 12.0

    # --- Voz de Vera: Cartesia ---
    cartesia_api_key: str = ""
    tts_version: str = "2026-08-14"
    tts_modelo: str = "sonic-3.6"
    # Mariana: voz colombiana nativa, «maternal, de tono calmado». Elegida
    # oyendo las cuatro colombianas con el saludo y el mensaje de emergencia.
    tts_voz: str = "ae823354-f9be-4aef-8543-f569644136b4"
    # Cartesia no acepta `es-CO` como locale y `es` cae por defecto en el acento
    # de España. El acento se pide explícito, y tiene que ser uno de la voz.
    tts_acento: str = "colombian"
    tts_sample_rate: int = 24000

    # --- Conocimiento: el corpus y su índice ---
    # El modelo de embeddings se quedó local cuando el resto se fue a la nube:
    # la consulta va en la ruta crítica del turno, antes de que Claude pueda
    # empezar a responder, y un viaje de red más se oiría. Este es el que midió
    # el proyecto original contra su alternativa (AUC 1,00 frente a 0,94) y el
    # que la decisión 7 de arquitectura da por cabido en el despliegue.
    embedding_modelo: str = "intfloat/multilingual-e5-large"

    # Cuántos fragmentos se recuperan y cuántos ve el modelo. Son dos números
    # porque recuperar de más es barato —ordena mejor— y mostrar de más no:
    # cada fragmento son cientos de tokens en la ruta crítica del turno.
    k_recuperados: int = 8
    k_evidencia: int = 3

    # El umbral de evidencia: por debajo, Vera no afirma nada clínico. Calibrado
    # contra ESTE corpus con `evals/conocimiento.py`:
    #
    #   0.81 -> 16/16 respondidas, 0 rechazos falsos, 5 fugas
    #   0.82 -> 15/16 respondidas, 1 rechazo falso,   3 fugas (ninguna clínica)
    #   0.84 -> 11/16 respondidas, 5 rechazos falsos, 1 fuga
    #
    # Se queda 0,82: es donde las fugas clínicas llegan a cero —«¿puedo tomar
    # cerveza?», que es el error que el proyecto original sí cometió, se abstiene
    # con 0,817— y las tres que quedan son administrativas: el seguro, la
    # incapacidad y el costo de la consulta. Esas no las separa ningún umbral
    # —puntúan entre medio de preguntas legítimas— y subirlo hasta que caigan
    # cuesta cinco respuestas buenas; de ellas se encarga la cita verificada.
    #
    # OJO: el número NO transfiere. Ni entre modelos de embeddings ni entre
    # corpus, porque es un coseno contra los textos concretos que hay indexados.
    # Que coincida con el 0,82 del proyecto original es casualidad: allá eran
    # PDFs académicos, y aquí hasta el *pooling* del modelo es otro.
    min_evidencia: float = 0.82

    # La otra mitad del veredicto: términos clínicos exactos compartidos entre la
    # pregunta y un fragmento. Rescata lo que el modelo denso diluye —«pus»,
    # «fiebre», «38»—, que es justo el vocabulario de los signos de alarma.
    min_lexico: int = 2

    # --- Red de seguridad ---
    # Guarda cada turno cerrado —lo transcrito y lo que el motor vio— en
    # `registros/turnos.jsonl`. Sirve para medir con habla real lo que el
    # reconocedor le hace a las palabras del paciente, que es de donde salen las
    # confusiones del léxico. Apagado por defecto: es la voz de un paciente.
    registro_turnos: bool = False

    @property
    def stt_configurado(self) -> bool:
        return bool(self.assemblyai_api_key)

    @property
    def tts_configurado(self) -> bool:
        return bool(self.cartesia_api_key)


settings = Settings()
