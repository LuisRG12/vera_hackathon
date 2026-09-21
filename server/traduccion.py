"""La transcripción de la llamada, en inglés, para quien evalúa y no habla español.

**Lo único del sistema que habla inglés, y solo en pantalla.** La conversación
sigue en español de principio a fin: lo que distingue a Vera es que entiende
cómo habla un paciente en Colombia —«está botando materia», «me dio un yeyo»—, y
un modo inglés obligaría a traducir justo esa capa. Un léxico en inglés hecho a
las carreras daría una demo con el diferenciador apagado, y el juez probaría una
versión peor del producto. Lo que necesita quien evalúa no es hablarle en inglés
sino entender lo que está viendo.

Por eso esto no toca el motor ni la llamada. Se pide a demanda, con un botón, y
cuesta una petición al gateway por transcripción, no una por turno: cero
latencia en la conversación.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from server.modelo.llm import StructuredLLM

SISTEMA = """You translate a transcript of a post-surgical follow-up phone call from Colombian Spanish into English, for a reader who does not speak Spanish.

- Translate every line, in the same order. Return exactly one translation per input line.
- Be faithful: do not add, soften, summarize or explain. Keep the clinical meaning exact.
- Colombian colloquialisms keep their clinical meaning: "botando materia" is discharging pus, "calentura" is fever, "maluco" is feeling unwell, "hacer del cuerpo" is having a bowel movement, "yeyo" is a fainting spell.
- Keep document names and section names unchanged."""

# Una llamada de diez minutos no pasa de unas sesenta líneas. El tope evita que
# el endpoint sirva para traducir cualquier otra cosa a cuenta del proyecto.
MAX_LINEAS = 80
MAX_CARACTERES = 600


class Traduccion(BaseModel):
    lineas: list[str] = Field(description="One English translation per input line, same order.")


async def traducir(llm: StructuredLLM, lineas: list[str]) -> list[str]:
    """Una traducción por línea. Si el modelo devuelve otra cantidad, se ajusta en
    código: el esquema no puede imponer el número de elementos (el gateway acepta
    `maxItems` sin cumplirlo, ver `server/modelo/llm.py`)."""
    lineas = [ln[:MAX_CARACTERES] for ln in lineas[:MAX_LINEAS]]
    if not lineas:
        return []
    numeradas = "\n".join(f"{i}. {ln}" for i, ln in enumerate(lineas, start=1))
    resultado, _ = await llm.structured(SISTEMA, numeradas, Traduccion,
                                        max_tokens=60 * len(lineas) + 200)
    salida = [t.split(". ", 1)[1] if t[:1].isdigit() and ". " in t[:5] else t
              for t in resultado.lineas]
    return (salida + [""] * len(lineas))[:len(lineas)]
