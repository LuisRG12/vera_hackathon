"""Lo que Vera dice cuando lo decide el código y no el modelo.

Traído de `server/agent/prompts.py` en vera_voice_agent, con el porqué de cada
texto tal como se escribió allá.
"""
from __future__ import annotations

# Ante un signo de alarma CRÍTICO. Texto fijo, y es la corrección de un defecto
# clínico real: en la llamada 87 del proyecto original el paciente dijo «me duele
# el pecho y el dolor me sube hacia el brazo» —cardiaco de manual, la regla
# disparó `critical`— y Vera, que tenía el objetivo correcto («encaminar a
# contactar a su equipo»), respondió *preguntando si el dolor era intenso o
# moderado*.
#
# Ante un crítico no se hace triaje ni se pide precisión: se dirige a urgencias,
# y eso no puede depender de que un modelo elija bien la forma verbal. Lo que se
# sabe de antemano lo escribe el código.
#
# `high` NO entra aquí a propósito. Una herida infectada o una fiebre son
# escalamiento, no emergencia: ahí sigue teniendo sentido conversar y recoger
# datos para el equipo.
EMERGENCIA = (
    "Por lo que me cuenta, esto no puede esperar: comuníquese de inmediato con su "
    "equipo clínico o acuda al servicio de urgencias más cercano. Ya estoy avisando "
    "a su equipo."
)

# La excepción: ideación suicida. Es `critical` como las demás, pero la frase de
# arriba sería exactamente lo que NO se hace: ante ideación, la conducta estándar
# es acompañar, no despedir la llamada con «acuda a urgencias». Una urgencia
# física se resuelve yendo a urgencias; una persona que dice que no quiere seguir
# viviendo necesita que no la despidan.
#
# **Pendiente de validación clínica**, igual que el concepto del léxico: un
# protocolo de ideación de verdad es más que una frase, y aquí lo único que se
# afirma es que despedirla con «acuda a urgencias» era peor.
ACOMPANAR = (
    "Gracias por confiarme algo así de difícil. No lo voy a dejar solo con esto: "
    "ya estoy avisando a su equipo clínico para que lo contacten ahora. "
    "¿Hay alguien que pueda acompañarlo mientras tanto?"
)
