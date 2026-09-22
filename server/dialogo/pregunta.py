"""Si el turno del paciente es una pregunta que espera una respuesta con contenido.

Traído de `server/agent/dialogue.py` en vera_voice_agent.

**Para qué se usa.** Abre dos puertas: la recuperación de contexto clínico y la
respuesta de abstención. Recuperar cuesta —el embedding de la consulta va en la
ruta crítica del turno, antes de que el modelo pueda empezar—, y sobre todo
entrega contexto clínico donde no venía a cuento. En una llamada real del
proyecto original, a un «espere ya voy» el agente respondió sobre
fotodocumentación de la válvula ileocecal: correcto según el corpus y absurdo
según la conversación.

**Por qué el reconocedor obliga a mirar más que el signo.** AssemblyAI entrega
muchas preguntas sin «¿» y a veces sin «?», así que un detector basado en la
puntuación se pierde la mitad. Lo que queda es reconocer las formas: el
interrogativo al principio, el interrogativo seguido de verbo, y el clítico de
permiso —«me puedo», «le toca»— que en esta conversación casi siempre pregunta.
"""
from __future__ import annotations

import re

# Hasta tres muletillas encadenadas antes del interrogativo: así empieza media
# conversación telefónica («y bueno, entonces cuándo me puedo bañar»).
#
# Tras la muletilla NO entra el «que» sin tilde, y es lo que separa esta rama de
# la del interrogativo pelado: «ahora QUE lo pienso» es una conjunción, no una
# pregunta, y con el «que» sin tilde la muletilla convertía media conversación en
# preguntas. Con tilde sí —«entonces QUÉ hago»—, y la forma sin tilde la recoge
# igual la rama de interrogativo + verbo, que exige el verbo detrás.
_MULETILLA = r"(?:(?:y|o|pero|bueno|listo|ah|entonces|ahora|oiga|oye|vera)[\s,]+){1,3}"
_INTERROGATIVO = (r"qu[eé]|qui[eé]n|c[oó]mo|cu[aá]ndo|cu[aá]nto|cu[aá]l|d[oó]nde|"
                  r"por\s+qu[eé]|puedo|debo|tengo\s+que|es\s+normal|hay\s+que")

_PREGUNTA = re.compile(
    r"\?"
    rf"|^\s*(?:{_INTERROGATIVO})\b"
    rf"|^\s*{_MULETILLA}"
    r"(?:qué|qui[eé]n|c[oó]mo|cu[aá]ndo|cu[aá]nto|cu[aá]l|d[oó]nde|por\s+qu[eé]|"
    r"puedo|debo|tengo\s+que|es\s+normal|hay\s+que)\b"
    r"|\b(?:qu[eé]|qui[eé]n|c[oó]mo|cu[aá]ndo|cu[aá]nto|cu[aá]l|d[oó]nde)\s+"
    r"(?:me|te|se|le|nos|lo|la)?\s*"
    r"(?:puedo|debo|es|eres|son|ser[ií]a|tengo|hago|hacer|hay|va|van|dura|"
    r"pasa|sirve|significa)\b"
    r"|\b(?:me|se|le|te|nos)\s+(?:puedo|puede|pueda|podr[ií]a|debo|debe|"
    r"deber[ií]a|toca)\b"
    r"|\b(?:verdad\s+que|de\s+verdad|en\s+serio|es\s+cierto|"
    r"t[uú]\s+eres|usted\s+es)\b"
    # «Cada cuánto» es pregunta casi siempre, esté donde esté en la frase. Se vio
    # en una llamada: «el dolor creo que ha estado estable cada cuanto me tomo la
    # pastilla para el dolor» llegó sin signo y con el interrogativo a la mitad,
    # así que no contó como pregunta, Vera no buscó en los documentos y no la
    # contestó hasta que la paciente se quejó en el turno siguiente.
    r"|\bcada\s+cu[aá]nto\b"
    # Los verbos de una llamada de seguimiento detrás de un interrogativo, pero
    # solo **con tilde**. El reconocedor escribe «cuándo» cuando se pregunta y
    # «cuando» cuando es conjunción: así «¿cuándo me quitan los puntos?» cuenta
    # como pregunta y «cuando me tomo la pastilla me da sueño» no.
    r"|\b(?:qué|cómo|cuándo|cuánto|cuántas|cuántos|cuál|dónde)\s+(?:me|te|se|le|nos|lo|la)?\s*"
    r"(?:tomo|toma|tomar|quito|quitan|quitar|cambio|cambian|cambiar|baño|bañar|"
    r"vuelvo|vuelve|volver|empiezo|dejo|dejar)\b",
    re.I)

# Fórmulas de cortesía y de canal que llevan signo de interrogación pero no
# preguntan nada clínico: «hola, ¿cómo está?», «¿me escucha?», «¿aló?».
#
# No pretende distinguir un saludo de una pregunta real en general —eso necesita
# más que una expresión regular—. Es una lista corta y cerrada de fórmulas que en
# una llamada de seguimiento no pueden significar otra cosa. Cualquier pregunta
# que además diga algo se sale de la lista y cuenta como pregunta.
_CORTESIA = re.compile(
    r"^[\s¿]*(?:(?:hola|buenas|buenos\s+d[ií]as|buenas\s+(?:tardes|noches)|al[oó]|"
    r"s[ií]|ya|aj[aá])[\s,.!¿?]*)*"
    r"(?:¿?\s*(?:c[oó]mo\s+(?:est[aá]s?|le\s+va|va|vas)|qu[eé]\s+tal|"
    r"me\s+(?:escucha|oye)s?|est[aá]s?\s+ah[ií]|hay\s+alguien|qui[eé]n\s+habla|"
    r"con\s+qui[eé]n\s+hablo)\s*\??)?[\s,.!¿?]*$",
    re.I)


def es_cortesia(texto: str) -> bool:
    """Saludo o fórmula de canal, aunque venga con signo de interrogación."""
    return bool(_CORTESIA.match(texto.strip()))


def es_pregunta(texto: str) -> bool:
    t = texto.strip()
    if es_cortesia(t):
        return False
    return bool(_PREGUNTA.search(t))
