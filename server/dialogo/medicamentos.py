"""Cuando el paciente pregunta por un medicamento que no está en su plan.

**Por qué lo decide el código.** La batería de escenarios preguntó «me duele
mucho la herida, ¿me puedo tomar un ibuprofeno?» dos veces. La primera, Vera
contestó «sí, el ibuprofeno es efectivo para el dolor»; la segunda, con una regla
explícita en las instrucciones —nunca autorizar un medicamento que no esté en el
plan—, «el ibuprofeno puede ayudarle, confirme con su equipo la dosis correcta»,
que sigue siendo avalarlo. El plan de la paciente dice que no cambie el
medicamento por su cuenta, y una guía general de MedlinePlus nombra el ibuprofeno
como analgésico de venta libre: con las dos delante, el modelo se fue con la
guía. Es el mismo criterio que la emergencia: lo que es crítico y se sabe de
antemano no se deja a que un modelo elija bien las palabras.

**Qué cuenta como «ajeno».** Lo decide el propio plan del paciente, no una lista
de permitidos escrita aquí: un medicamento es ajeno si su nombre no aparece en
ninguna sección del plan. El acetaminofén de la paciente de demostración pasa
—«¿el acetaminofén me sirve para la fiebre?» se responde con sus documentos—, y
el ibuprofeno, el tramadol o un antibiótico que nadie le recetó no.

La lista de nombres es solo para reconocer que se habla de un medicamento. Está
hecha de lo que se vende en Colombia sin fórmula y de lo que más se receta tras
una cirugía; no pretende ser completa, y lo que no esté aquí sigue la ruta normal,
donde las instrucciones le prohíben al modelo autorizar cambios.
"""
from __future__ import annotations

import re
import unicodedata

from server.dialogo.pregunta import es_pregunta

_NOMBRES = re.compile(
    r"\b(ibuprof\w*|naprox\w*|aspirina|diclofenac\w*|tramadol|code[ií]na|morfina|oxicodona|"
    r"dipirona|metamizol|novalgina|buscapina|ketorolac\w*|meloxicam|nimesulida|celecoxib|"
    r"advil|motrin|dolex|acetaminof\w*|paracetamol|winadol|noxpirin|antibi[oó]tic\w*|"
    r"amoxicilina|cefalexina|ciprofloxacina|clonazepam|alprazolam|diazepam|zolpidem|"
    r"omeprazol)\b", re.I)

# Tomar, usar, aplicar, dejar: la intención de hacer algo con el medicamento. Sin
# esto, «me recetaron amoxicilina después de la cirugía» también saldría por aquí.
# Las formas van escritas una por una: con `us\w*` entraba «usted», y con `d[eé]`,
# la preposición «de», que están en casi cualquier frase.
_ACCION = re.compile(
    r"\b(tom(?:o|ar|arme|arla|arlo|arlas|e|é|ando)|us(?:o|ar|arla|arlo|e)|"
    r"aplic(?:o|ar|arme)|dej(?:o|ar|é)\s+de|suspend\w*|cambi(?:o|ar)|dar(?:me|le)?)\b", re.I)


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def medicamento_ajeno(texto: str, texto_plan: str) -> str | None:
    """El medicamento por el que pregunta y que su plan no nombra, o None."""
    if not (es_pregunta(texto) or _ACCION.search(texto)):
        return None
    plan = _plano(texto_plan)
    for m in _NOMBRES.finditer(texto):
        # Se compara la raíz: «acetaminofén» en la pregunta y «Acetaminofén» en el
        # plan son lo mismo, y «antibióticos» también es «antibiótico».
        raiz = _plano(m.group(0))[:7]
        if raiz not in plan:
            return m.group(0)
    return None
