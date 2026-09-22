"""La agenda de la llamada: qué falta preguntar, y cuándo cerrar.

**Por qué existe.** Sin agenda, Vera solo reaccionaba: contestaba lo que le
decían y esperaba. La llamada no terminaba nunca por sí misma —terminaba por
agotamiento, con un «¿sigue por ahí?» a los veinte segundos de silencio que
sonaba a máquina esperando—. Una enfermera de seguimiento hace lo contrario:
lleva una lista, pregunta lo que falta y, cuando ya cubrió todo, cierra ella.

**El código decide qué preguntar; el modelo decide cómo.** Es el mismo reparto
que el resto del turno: el objetivo lo fija el código y el modelo lo redacta.
Así la agenda no depende de que el modelo se acuerde de lo que ya se habló, y se
prueba sin gastar un turno.

Es una versión deliberadamente pequeña del estado de la llamada del proyecto
original, que llevaba síntomas reportados y negados, día posoperatorio y
procedimiento. Aquí solo hace falta saber qué temas ya salieron.
"""
from __future__ import annotations

import re
import unicodedata

# El orden es el de una llamada de seguimiento: lo que más molesta primero.
# Cada tema se da por hablado cuando el paciente lo menciona, o cuando fue el que
# se le asignó a Vera en un turno —ya lo preguntó—. Lo que Vera dice por su
# cuenta no cuenta: al explicar cómo bañarse nombra «las incisiones», y eso no es
# haberle preguntado cómo las ve.
TEMAS: dict[str, str] = {
    "dolor": r"dolor|duel[eo]|adolorid|pastilla|acetaminof|medicament|analges",
    "herida": r"herid|incisi|puntos|aposito|cicatri|materia|pus\b|supura",
    "fiebre": r"fiebre|calentura|temperatura|escalofri",
    # «comí» sí, «como» no: «como» sale en casi cualquier frase —«¿cómo va?»,
    # «como que me duele»— y daría la alimentación por hablada sin que nadie la
    # hubiera mencionado.
    "digestion": (r"\bcom(er|ida|idas|iendo|i)\b|apetito|nausea|vomit|del cuerpo|"
                  r"deposici|diarrea|estren|gases|evacu"),
}

# Cómo se le pide al modelo que pregunte cada tema. Son preguntas abiertas: las
# reglas de Vera le prohíben presuponer síntomas que el paciente no mencionó.
PREGUNTA: dict[str, str] = {
    "dolor": "cómo va el dolor y si está tomando el medicamento como se lo indicaron",
    "herida": "cómo ve las heridas de la cirugía",
    "fiebre": "si ha tenido fiebre",
    "digestion": "cómo va comiendo y si ha podido hacer del cuerpo con normalidad",
}

PREGUNTA_CIERRE = "si hay algo más que le preocupe o que quiera contarle"


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


class Agenda:
    """Una por llamada."""

    def __init__(self) -> None:
        self.pendientes: list[str] = list(TEMAS)
        # Si Vera ya preguntó «¿hay algo más?». Después de esa pregunta, un «no,
        # nada más, gracias» significa colgar; antes, significa otra cosa.
        self.cierre_preguntado = False

    def anotar(self, texto: str) -> None:
        """Da por hablados los temas que el paciente mencionó."""
        plano = _plano(texto)
        self.pendientes = [t for t in self.pendientes if not re.search(TEMAS[t], plano)]

    def siguiente(self) -> str | None:
        return self.pendientes[0] if self.pendientes else None

    def asignar(self, tema: str) -> None:
        """Vera va a preguntar por este tema en el turno: ya no queda pendiente."""
        if tema in self.pendientes:
            self.pendientes.remove(tema)


# Lo que cabe en una respuesta de cierre. La regla no es «parece una despedida»
# sino «no dice nada más que eso»: **todas** las palabras tienen que estar aquí.
# Así «no, nada más, gracias» cierra y «no, pero me duele la herida» no, aunque
# las dos empiecen igual. Ante la duda, no se cuelga: colgarle a un paciente que
# todavía tenía algo que decir es el error caro.
_VOCABULARIO_CIERRE = {
    "no", "nada", "mas", "eso", "es", "era", "seria", "todo", "gracias", "muchas",
    "mil", "ya", "listo", "bueno", "bien", "esta", "estoy", "estamos", "asi", "si",
    "por", "ahora", "de", "momento", "senora", "senorita", "doctora", "vera", "muy",
    "amable", "tranquila", "tranquilo", "igualmente", "chao", "chau", "adios",
    "hasta", "luego", "pronto", "nos", "vemos", "que", "le", "vaya", "dios", "la",
    "bendiga", "cuidese", "ok", "okay", "vale", "perfecto", "entendido", "claro",
}
_DESPEDIDA = re.compile(r"\b(chao|chau|adios|hasta luego|hasta pronto|nos vemos)\b")


def es_cierre(texto: str, cierre_preguntado: bool) -> bool:
    """Si el paciente está terminando la llamada.

    Hace falta que Vera ya haya preguntado si hay algo más, o que el paciente se
    despida con todas las letras. Un «bien, gracias» a mitad de llamada es la
    respuesta a «¿cómo va el dolor?», no un adiós.

    Quien llama a esto ya comprobó que las reglas no vieron ninguna señal: una
    despedida con un signo de alarma adentro no es una despedida.
    """
    plano = _plano(texto)
    palabras = re.findall(r"[a-z]+", plano)
    if not palabras or not all(p in _VOCABULARIO_CIERRE for p in palabras):
        return False
    return cierre_preguntado or bool(_DESPEDIDA.search(plano))
