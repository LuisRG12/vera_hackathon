"""Cliente del modelo de lenguaje: Claude por el LLM Gateway de AssemblyAI.

**Por qué el gateway y no la API de Anthropic directa.** Enruta y factura Claude
a través de AssemblyAI: el sistema entero vive con una sola credencial y quien lo
reproduzca necesita una clave, no dos (docs/arquitectura.md, decisión 2).

**Por HTTP directo.** El gateway habla el formato de chat completions. Se usa
`httpx`, que el proyecto ya trae; el SDK de Anthropic no habla este formato y uno
ajeno no aporta nada sobre una petición.

**Salida estructurada, impuesta y no pedida.** Con `response_format` el esquema no
es una instrucción que el modelo pueda desobedecer: no puede escribir fuera de él.
Medido: con un `enum` en un campo, sale un valor del `enum` aunque el contexto pida
otro. Es lo que reemplaza la gramática que imponía el runtime local del proyecto
original.

Pero Claude no soporta todas las restricciones de JSON Schema —longitudes,
mínimos y máximos, límites de elementos—, y el gateway las **acepta sin
cumplirlas**: pidiendo 60 caracteres salieron 67. Mandarlas daría una falsa
sensación de límite, así que se quitan del esquema que viaja
(`esquema_para_claude`) y el que las necesite las cumple en código.

Esta interfaz es la del proyecto original (`structured`), para que lo que la usa
se traiga sin reescribirlo. Cambia que es asíncrona: todo el servidor lo es.
"""
from __future__ import annotations

from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from server.config import settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LimiteDeTasa(LLMError):
    """El gateway limita las peticiones por modelo: 30 por ventana en esta cuenta,
    medido en sus cabeceras, y la ventana se libera en menos de un minuto.

    Aquí no se reintenta. En una llamada, esperar a que se libere el cupo es
    dejar al paciente en silencio; lo correcto es seguir sin el modelo, y la
    decisión de seguridad sale igual con las reglas. Quien sí puede esperar —un
    arnés de pruebas— tiene `espera` para saber cuánto.
    """

    def __init__(self, espera: float):
        super().__init__(f"límite de peticiones del gateway; se libera en {espera:.0f} s")
        self.espera = espera


# Lo que el esquema de Pydantic trae y Claude no impone. `title` y `default` no
# restringen nada; el resto son límites que el gateway aceptaría sin cumplir.
_FUERA = {
    "title", "default",
    "maxLength", "minLength", "pattern",
    "maxItems", "minItems", "uniqueItems",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
}


def esquema_para_claude(esquema: dict) -> dict:
    """El esquema de Pydantic, en la forma que la salida estructurada impone.

    Todo objeto cierra `additionalProperties` y declara todos sus campos como
    requeridos: así el modelo no puede omitir uno ni inventar otro.
    """
    def limpiar(nodo: Any) -> Any:
        if isinstance(nodo, list):
            return [limpiar(x) for x in nodo]
        if not isinstance(nodo, dict):
            return nodo
        salida = {}
        for clave, valor in nodo.items():
            if clave in _FUERA:
                continue
            if clave in ("properties", "$defs"):
                # Aquí las claves son nombres de campo, no palabras de JSON
                # Schema: un campo que se llamara «title» no debe desaparecer.
                salida[clave] = {nombre: limpiar(sub) for nombre, sub in valor.items()}
            else:
                salida[clave] = limpiar(valor)
        if salida.get("type") == "object":
            salida["additionalProperties"] = False
            salida["required"] = list(salida.get("properties", {}))
        return salida

    limpio = limpiar(esquema)
    # Pydantic pone el docstring de cada clase como descripción de su esquema, y
    # ese docstring es para quien lee el código, no para el modelo: viajaba en
    # cada petición, unos 200 tokens de notas de diseño. Las descripciones de los
    # campos sí se quedan; esas son instrucciones.
    for clase in (limpio, *limpio.get("$defs", {}).values()):
        clase.pop("description", None)
    return limpio


def _uso(u: dict) -> dict[str, int]:
    return {
        "input_tokens": u.get("prompt_tokens") or u.get("input_tokens") or 0,
        "output_tokens": u.get("completion_tokens") or u.get("output_tokens") or 0,
    }


class StructuredLLM:
    """Un cliente por proceso.

    La conexión se reutiliza entre turnos: abrir TLS con el gateway en cada
    petición suma una espera que en una llamada de voz se oye.
    """

    def __init__(self, modelo: str | None = None):
        self.modelo = modelo or settings.llm_modelo
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}
        self._http: httpx.AsyncClient | None = None

    def _cliente(self) -> httpx.AsyncClient:
        if self._http is None:
            if not settings.assemblyai_api_key:
                raise LLMError("falta ASSEMBLYAI_API_KEY")
            self._http = httpx.AsyncClient(
                timeout=settings.llm_timeout_s,
                headers={"authorization": settings.assemblyai_api_key},
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def structured(self, system: str, user: str, schema: type[T],
                         max_tokens: int = 400, temperatura: float = 0.0) -> tuple[T, dict]:
        """Una respuesta validada contra el esquema, y lo que costó en tokens.

        El esquema impuesto garantiza la forma, así que una salida que no valida
        casi siempre es una respuesta cortada por `max_tokens`. Se reintenta una
        vez con el doble de margen; más veces sería alargar un turno que ya
        está tarde.
        """
        cuerpo = {
            "model": self.modelo,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperatura,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": esquema_para_claude(schema.model_json_schema()),
            }},
        }
        for intento, tope in enumerate((max_tokens, max_tokens * 2)):
            try:
                r = await self._cliente().post(settings.llm_url,
                                               json={**cuerpo, "max_tokens": tope})
            except httpx.HTTPError as e:
                raise LLMError(f"el gateway no responde: {type(e).__name__}: {e}") from e
            if r.status_code == 429:
                raise LimiteDeTasa(float(r.headers.get("x-ratelimit-reset") or 60))
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")

            cuerpo_r = r.json()
            eleccion = cuerpo_r["choices"][0]
            self.last_usage = _uso(cuerpo_r.get("usage") or {})
            contenido = (eleccion.get("message") or {}).get("content") or ""
            try:
                return schema.model_validate_json(contenido), self.last_usage
            except (ValidationError, ValueError) as e:
                if eleccion.get("finish_reason") == "length" and intento == 0:
                    continue
                raise LLMError(f"salida inválida ({eleccion.get('finish_reason')}): {e}") from e
        raise LLMError("salida cortada por max_tokens también con el doble de margen")
