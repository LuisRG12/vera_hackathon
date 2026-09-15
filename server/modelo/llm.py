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

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from server.config import settings
from server.modelo.flujo import PartialToolJSON

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

    def _cuerpo(self, system: str, user: str, schema: type[BaseModel],
                max_tokens: int, temperatura: float,
                historial: list[dict] | None = None) -> dict:
        return {
            "model": self.modelo,
            "messages": [{"role": "system", "content": system}, *(historial or []),
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperatura,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": esquema_para_claude(schema.model_json_schema()),
            }},
        }

    def _revisar(self, r: httpx.Response) -> None:
        if r.status_code == 429:
            raise LimiteDeTasa(float(r.headers.get("x-ratelimit-reset") or 60))
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")

    async def astructured_stream(self, system: str, user: str, schema: type[T],
                                 max_tokens: int = 260, temperatura: float = 0.3,
                                 historial: list[dict] | None = None):
        """Los eventos del turno, para hablar mientras se genera:

            ("grounding", dict)  -> campos anteriores a `utterance`, completos
            ("delta", str)       -> texto nuevo de `utterance`
            ("final", (obj|None, usage))

        La salida estructurada respeta el orden del esquema, así que lo que va
        antes de `utterance` —las citas, cuando las haya— llega completo antes de
        la primera palabra que se dice.

        **El esquema tiene que ser el mismo en todos los turnos.** Claude compila
        cada esquema nuevo antes de usarlo, y medido, las dos primeras peticiones
        con uno nuevo tardan ~1,8 s en dar la primera frase contra ~0,95 s después.
        Un esquema que cambiara por turno pagaría eso cada vez.
        """
        parser = PartialToolJSON()
        uso: dict = {}
        cuerpo = {**self._cuerpo(system, user, schema, max_tokens, temperatura, historial),
                  "stream": True}
        try:
            async with self._cliente().stream("POST", settings.llm_url, json=cuerpo) as r:
                if r.status_code >= 400:
                    await r.aread()
                    self._revisar(r)
                async for linea in r.aiter_lines():
                    if not linea.startswith("data:"):
                        continue
                    dato = linea[5:].strip()
                    if dato == "[DONE]":
                        break
                    try:
                        evento = json.loads(dato)
                    except json.JSONDecodeError:
                        continue
                    uso = evento.get("usage") or uso
                    trozo = ((evento.get("choices") or [{}])[0].get("delta") or {}).get("content")
                    if not trozo:
                        continue
                    parser.feed(trozo)
                    if (g := parser.grounding()) is not None:
                        yield "grounding", g
                    if texto := parser.utterance_delta():
                        yield "delta", texto
        except httpx.HTTPError as e:
            raise LLMError(f"el gateway no responde: {type(e).__name__}: {e}") from e

        self.last_usage = _uso(uso)
        try:
            obj = schema.model_validate_json(parser.buf)
        except (ValidationError, ValueError):
            obj = None
        yield "final", (obj, self.last_usage)

    async def structured(self, system: str, user: str, schema: type[T],
                         max_tokens: int = 400, temperatura: float = 0.0) -> tuple[T, dict]:
        """Una respuesta validada contra el esquema, y lo que costó en tokens.

        El esquema impuesto garantiza la forma, así que una salida que no valida
        casi siempre es una respuesta cortada por `max_tokens`. Se reintenta una
        vez con el doble de margen; más veces sería alargar un turno que ya
        está tarde.
        """
        for intento, tope in enumerate((max_tokens, max_tokens * 2)):
            try:
                r = await self._cliente().post(
                    settings.llm_url, json=self._cuerpo(system, user, schema, tope, temperatura))
            except httpx.HTTPError as e:
                raise LLMError(f"el gateway no responde: {type(e).__name__}: {e}") from e
            self._revisar(r)

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
