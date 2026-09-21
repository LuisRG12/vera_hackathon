"""Topes de uso: cuántas llamadas a la vez y cuánto dura cada una.

**Por qué existen ahora.** Mientras Vera corría en esta máquina, quien la usaba
era quien pagaba. Desplegada en una URL pública, cualquiera que la abra gasta de
tres cuentas de pago por uso —el reconocedor, el modelo y la voz—, y una pestaña
olvidada con el micrófono abierto es una llamada que no termina. El cierre por
silencio ya cubría la llamada abandonada; no cubría la que sigue hablando, ni
diez personas llamando a la vez.

Traído en espíritu de `server/governance/limits.py` en vera_voice_agent, que ya
los tenía como controles de **producto** y no de laboratorio: un agente clínico
que puede hablar indefinidamente es uno que un día se queda en bucle con un
paciente confundido. De allá se conserva la lección más cara: **el reloj arranca
con el primer turno del paciente, no al abrir la conexión**. Con el reloj
corriendo desde antes, la llamada llegaba muerta —«se nos acabó el tiempo» en el
turno uno— y el límite se aplicaba a una conversación que nunca ocurrió.

Los topes se aplican, no se anuncian: un límite que se avisa y se sigue
aceptando da una falsa sensación de control, que es peor que no tenerlo.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


class Cupo:
    """Llamadas de voz simultáneas. Una por proceso, compartida entre llamadas.

    No es un semáforo de asyncio a propósito: quien llega sin cupo no espera en
    cola —en una llamada, esperar es oír silencio sin saber por qué—, se le dice
    que está ocupado y decide él si vuelve a intentar.
    """

    def __init__(self, maximo: int):
        self.maximo = maximo
        self.en_curso = 0

    def tomar(self) -> bool:
        if self.en_curso >= self.maximo:
            return False
        self.en_curso += 1
        return True

    def soltar(self) -> None:
        self.en_curso = max(0, self.en_curso - 1)


@dataclass
class Presupuesto:
    """Techo de turnos y de duración de una conversación, de voz o de texto."""

    max_turnos: int
    max_segundos: float
    turnos: int = 0
    inicio: float | None = None
    # Para probarlo sin dormir diez minutos.
    reloj: callable = field(default=time.monotonic, repr=False)

    def registrar_turno(self) -> None:
        if self.inicio is None:
            self.inicio = self.reloj()
        self.turnos += 1

    def transcurrido(self) -> float:
        """Segundos de conversación. Cero mientras nadie haya hablado."""
        return 0.0 if self.inicio is None else self.reloj() - self.inicio

    def excedido(self) -> str | None:
        """Qué tope se pasó, o None."""
        if self.turnos > self.max_turnos:
            return "turnos"
        if self.transcurrido() > self.max_segundos:
            return "duracion"
        return None
