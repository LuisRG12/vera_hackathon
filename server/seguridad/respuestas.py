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
    "Gracias por confiarme algo así de difícil. No va a estar sin apoyo con esto: "
    "ya estoy avisando a su equipo clínico para que se comuniquen con usted ahora. "
    "¿Hay alguien que pueda estar con usted mientras tanto?"
)

# Cuando el paciente pregunta por un medicamento que no está en su plan. Ver
# server/dialogo/medicamentos.py: con una regla escrita que se lo prohibía, el
# modelo igual avaló el ibuprofeno. No dice «no lo tome» ni «tómelo»: dice quién
# decide, y que no se decide solo.
MEDICAMENTO_AJENO = (
    "Eso tiene que decidirlo su equipo clínico: no tome, no cambie ni deje ningún "
    "medicamento por su cuenta, aunque sea de venta libre. Si la molestia no cede con "
    "lo que le indicaron, comuníquese con ellos."
)
# La misma, cuando en la llamada ya hay un signo de alarma: «tengo fiebre de 39,
# ¿me tomo un ibuprofeno?». La de arriba solo manda a llamar si la molestia no
# cede, y con una alarma delante eso se queda corto.
MEDICAMENTO_AJENO_CON_ALARMA = (
    "Eso tiene que decidirlo su equipo clínico: no tome, no cambie ni deje ningún "
    "medicamento por su cuenta, aunque sea de venta libre. Y lo que me cuenta hay que "
    "revisarlo hoy mismo: comuníquese hoy con su equipo."
)


# ------------------------------------------------------------------------------
# Cómo termina una llamada según lo que pasó en ella.
#
# La despedida normal —«que siga mejorando, y si algo cambia, comuníquese con su
# equipo»— se decía también después de una emergencia, y probándolo por voz sonó
# justo así: tras «esto no puede esperar, acuda a urgencias», Vera se despidió
# como si nada. Y el silencio que siguió a la emergencia recibió «¿me escucha? Si
# hay algo más que le preocupe, aquí estoy», que invita a seguir conversando a
# alguien que debería estar pidiendo ayuda. La despedida tiene que llevar lo más
# importante que se dijo en la llamada, no una cortesía que lo contradiga.

# Después de un signo de alarma. La conversación puede seguir —una herida con
# materia es para hoy, no para ya—, pero la despedida lo recuerda.
CIERRE_ALARMA = (
    "Con gusto. Por lo que me contó, recuerde comunicarse hoy mismo con su equipo "
    "clínico. Que se mejore. Hasta luego."
)

# Después de una emergencia, al despedirse o al primer silencio. Repite la
# instrucción y cuelga: retener con un asistente virtual a alguien que tiene que
# estar llamando a urgencias es demorarlo.
CIERRE_EMERGENCIA = (
    "Por lo que me contó, no espere más: comuníquese ya con su equipo clínico o "
    "acuda al servicio de urgencias más cercano. Voy a colgar para que pueda hacerlo."
)

# Después de ideación, lo contrario: no se cuelga rápido. El primer silencio
# recibe compañía, y solo el segundo cierra, con la misma pauta de `ACOMPANAR`.
# **Pendientes de validación clínica**, como `ACOMPANAR`: un protocolo de
# ideación de verdad es más que dos frases, y lo único que se afirma aquí es que
# colgar al primer silencio o despedirse con «que siga mejorando» era peor.
RETOMAR_ACOMPANAR = (
    "Sigo aquí con usted. ¿Hay alguien que pueda estar con usted en este momento?"
)
CIERRE_ACOMPANAR = (
    "Gracias por contarme. Su equipo clínico ya está avisado para comunicarse con "
    "usted. Si siente que puede hacerse daño, llame de inmediato a la línea de "
    "emergencias o pida a alguien de confianza que esté con usted."
)
