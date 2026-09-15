"""Lo que produce la seguridad: la valoración del juez y la decisión combinada.

Traído de `server/agent/schemas.py` en vera_voice_agent, con un cambio: los
límites de longitud ya no viven en el esquema.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

RiskLevel = Literal["none", "low", "moderate", "high", "critical"]
ActionType = Literal["continue", "advise", "escalate", "emergency"]


class RiskAssessment(BaseModel):
    """Salida del juez de riesgo.

    `risk` va primero a propósito: es lo único que la decisión necesita, y la
    salida estructurada respeta el orden del esquema, así que llega aunque la
    generación se corte después.

    **Los campos van acotados, pero en código.** En el proyecto original el límite
    estaba en el esquema y el runtime lo imponía: sin él, el modelo copiaba el
    fragmento entero en `evidence` y agotaba sus tokens a mitad del JSON. Claude
    no impone longitudes (ver `server/modelo/llm.py`), así que aquí se recorta al
    validar. Recortar y no rechazar: una justificación larga no puede tumbar la
    valoración del riesgo, que es lo único que importa del turno.
    """

    risk: RiskLevel
    rationale: str = Field(description="Una frase, breve.")
    evidence: list[str] = Field(default_factory=list,
                                description="Hasta 3 frases cortas de lo que dijo el paciente.")

    @field_validator("rationale")
    @classmethod
    def _una_frase(cls, v: str) -> str:
        return v[:240]

    @field_validator("evidence")
    @classmethod
    def _pocas(cls, v: list[str]) -> list[str]:
        return [e[:160] for e in v[:3]]


class SafetyDecision(BaseModel):
    """Decisión de seguridad combinada, con su justificación persistible.

    `source` dice qué capa la decidió (`rules`, `llm`, `both`, `none`). Una
    decisión clínica que no se puede explicar no sirve para auditarla después.
    """

    risk: RiskLevel
    action: ActionType
    rationale: str
    rule_flags: list[str] = Field(default_factory=list)
    source: str
