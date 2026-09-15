"""Capa A de seguridad: detección determinista de red-flags clínicos.

**Procedencia.** Traído de `server/agent/safety_rules.py` en
[vera_voice_agent](https://github.com/LuisRG12/vera_voice_agent) (agosto 2026,
commit 1827f0e). Lo que cambió durante el reto está en el historial de git y en
docs/bitacora.md: la tolerancia al guion que pone el formateo de AssemblyAI.

Este archivo es el **motor**; el vocabulario está en `lexico.py`. La separación
existe porque cada llamada real descubre dos o tres formas nuevas de decir lo
mismo, y ampliar la cobertura no debería exigir saber escribir expresiones
regulares. Acá va la lógica que no cambia: compilación, alcance de la negación y
combinación de severidades.

No depende del LLM: es la red de seguridad que sigue en pie aunque el modelo se
equivoque —o no responda—.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from server.seguridad.lexico import LEXICON

SEVERITY_ORDER = ["none", "low", "moderate", "high", "critical"]

ACTION_FOR = {
    "none": "continue",
    "low": "continue",
    "moderate": "advise",
    "high": "escalate",
    "critical": "emergency",
}


@dataclass
class RuleFlag:
    name: str
    severity: str
    match: str


# ---------------------------------------------------------------- compilación

# Clases de vocal que hacen el emparejamiento insensible a tildes en ambos
# sentidos: el STT y quien teclea las omiten con frecuencia, y un red-flag no
# puede perderse porque el paciente escribió "cesarea" sin tilde.
_VOCALES = {
    "a": "[aáàä]", "e": "[eéèë]", "i": "[iíìï]",
    "o": "[oóòö]", "u": "[uúùü]",
}


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def compilar_termino(termino: str) -> str:
    """Frase plana -> regex, tolerante a cómo transcribe un STT.

    Además de las tildes, se absorben los errores que un reconocedor de voz
    comete de verdad con español colombiano hablado por teléfono: «me sale **pu**
    de la herida» (/s/ aspirada), «no puedo **respira**», «se me abrió la
    **erida**» y «me **desmalle** anoche». Sin estas tolerancias los cuatro daban
    `none`, y dos de ellos eran `critical`.

    Todo lo que decide el agente sale del transcript, no del audio: un red-flag
    no puede perderse porque el reconocedor se comió una consonante.

      - `h` opcional (muda: herida/erida)
      - `ll` ↔ `y` (yeísmo: desmayé/desmalle)
      - `b` ↔ `v` (indistinguibles al oído)
      - `s` y `r` finales de palabra opcionales (aspiración e infinitivos)
      - espacio ↔ guion (el formateo de AssemblyAI: «Coca-Cola»)

    Se hace en el patrón, no normalizando el texto de entrada: así los índices
    de la coincidencia siguen siendo válidos para el análisis de la negación.
    """
    texto = _sin_tildes(termino.lower())
    partes: list[str] = []
    i = 0
    while i < len(texto):
        ch = texto[i]
        siguiente = texto[i + 1] if i + 1 < len(texto) else ""
        fin_de_palabra = siguiente in ("", " ")

        if ch == "l" and siguiente == "l":
            partes.append("(?:ll|y)")
            i += 2
            continue
        if ch == "y":
            partes.append("(?:y|ll)")
        elif ch == "h":
            partes.append("h?")
        elif ch in "bv":
            partes.append("[bv]")
        elif ch == "s" and fin_de_palabra:
            # Final de palabra: la /s/ se aspira (costa) Y además sesea. Las dos
            # tolerancias a la vez, no una u otra: «pus» tiene que seguir
            # encontrándose como «pu», y ese caso se rompió al añadir el seseo
            # sin combinarlo con la opcionalidad.
            partes.append("[csz]?")
        elif ch == "r" and fin_de_palabra:
            partes.append("r?")
        elif ch in "sz" or (ch == "c" and siguiente in ("e", "i")):
            # Seseo: c (ante e/i), s y z suenan igual en todo el español
            # americano, así que el reconocedor elige una de las tres. La prueba
            # con micrófono real lo confirmó: «cipote» salió «zipote» y la
            # detección se perdió. La `c` ante a/o/u/consonante suena /k/ y no
            # entra en el grupo.
            partes.append("[csz]")
        elif ch == "n":
            # La eñe. `_sin_tildes` la descompone en n + tilde y se queda en "n",
            # así que el patrón resultante ya no encontraba la palabra bien
            # escrita: el término «no paro de ir al bano» —tal como está en el
            # léxico— NO detectaba «no paro de ir al baño», que es justo lo que
            # entrega el reconocedor con `smart_format`. La clase acepta las dos
            # grafías en ambos sentidos y hace el término inmune a cómo se
            # teclee. El costo teórico es que «ano» también case «año»: ninguna
            # de esas colisiones existe en el vocabulario clínico.
            partes.append("[nñ]")
        elif ch in _VOCALES:
            partes.append(_VOCALES[ch])
        elif ch == "*":
            partes.append(r"\w*")
        elif ch == " ":
            # Espacio o guion. AssemblyAI formatea el texto y une con guion lo
            # que reconoce como compuesto o marca: «la orina como coca cola»
            # llegó como «Coca-Cola» y la ictericia se perdió (evals/confusiones.py).
            # Es la misma clase de defecto que la tilde —cómo se escribe, no qué
            # se dijo—, y se absorbe igual: en el patrón.
            partes.append(r"[\s-]+")
        else:
            partes.append(re.escape(ch))
        i += 1
    # `h` final opcional: el español no tiene palabras terminadas en h, así que
    # permitirla no cuesta nada y captura una clase real de error. Medido: el
    # reconocedor entregó «push» por «pus» —tirando hacia el inglés— y con
    # confianza 0.99, así que ni un umbral acústico lo habría atrapado. Sin esta
    # tolerancia se perdía la detección de infección.
    return r"\b" + "".join(partes) + r"h?\b"


def _compilar() -> list[tuple[str, str, re.Pattern]]:
    reglas = []
    for concepto, cfg in LEXICON.items():
        alternativas = [compilar_termino(t) for t in cfg.get("terminos", [])]
        # Las confusiones del reconocedor se compilan igual que los términos: son
        # frases planas y necesitan las mismas tolerancias fonéticas encima —lo
        # que el STT ya deformó puede volver a deformarse—. Van en la misma regla
        # porque disparan el mismo concepto con la misma severidad; lo que las
        # mantiene distinguibles es dónde se declaran, no cómo se compilan.
        alternativas += [compilar_termino(t) for t in cfg.get("confusiones", [])]
        alternativas += list(cfg.get("patrones", []))
        if not alternativas:
            continue
        reglas.append((concepto, cfg["severidad"],
                       re.compile("|".join(alternativas), re.I)))
    return reglas


_RULES = _compilar()


# ------------------------------------------------------------------ negación

# "ni" cuenta como negación propia: en "no tengo fiebre ni escalofríos" la
# negación inicial queda demasiado lejos del segundo síntoma y este se detectaba
# como si el paciente lo estuviera reportando. El coordinador negativo mantiene
# el alcance sobre lo que enumera.
#
# "nunca"/"jamás" entraron al arreglar el bloque de angustia: «nunca he tenido
# fiebre» no se reconocía como negación y se contaba como reporte de fiebre —un
# falso positivo que ya existía—. En un desmentido de ideación («nunca he
# pensado en hacerme daño») el mismo hueco levantaría una emergencia.
_NEG = re.compile(r"\b(no|ni|sin|ning[uú]n\w*|nada de|ya no|tampoco|nunca|jam[aá]s)\b", re.I)

# Palabras que pueden ir ENTRE la negación y el síntoma sin que la negación deje
# de aplicarle: auxiliares, artículos y cuantificadores. Cualquier otra cosa
# (un verbo con contenido propio, una coma, un "pero") rompe el alcance.
_BRIDGE_WORD = (
    r"(?:tengo|tuve|he|ha|hab[ií]a|hay|presento|presenta|siento|sentido|tenido|tiene|"
    # **El imperfecto de los mismos verbos que ya están arriba.** No ensancha la
    # clase —son conjugaciones de `tener`, `sentir`, `estar`, `ver`, `notar`,
    # `presentar`, `salir` y `dar`, todos ya aceptados—, y su ausencia costaba
    # caro: «no TENÍA fiebre» no se reconocía como negación, así que la fiebre
    # entraba como síntoma REPORTADO y la llamada escalaba. Se vio por
    # micrófono: el paciente dijo que no tenía fiebre y Vera le contestó que
    # «ha presentado fiebre y otros síntomas» y que necesitaba atención
    # inmediata. El reconocedor entrega el imperfecto constantemente.
    r"ten[ií]a|ten[ií]an|ten[ií]amos|sent[ií]a|estaba|estaban|ve[ií]a|notaba|"
    r"presentaba|sal[ií]a|daba|"
    r"me|se|le|es|est[aá]|estoy|estuve|dado|dio|da|sale|salido|noto|notado|veo|visto|"
    r"de|del|la|el|los|las|un|una|unos|unas|ning[uú]n\w*|nada|much[oa]s?|m[aá]s|nuev[oa]s?|"
    # Cuantificadores de clase. Sin ellos, «no estoy tomando ningún TIPO DE
    # medicamento» quedaba SIN negar —la negación estaba, pero «tipo» rompía el
    # puente—, y el paciente que acababa de decir que no toma nada aparecía en
    # el resumen con la medicación reportada. Es una perífrasis frecuentísima
    # hablando, y no aporta contenido clínico propio.
    r"tipo|tipos|clase|clases|forma|formas|cosa|cosas|"
    # Verbos de volición e intención + "en"/"ganas". Sin ellos, «no tengo ganas
    # de vomitar» y «no quiero hacerme daño» quedaban SIN negar: la negación se
    # encontraba, pero el puente se rompía en la primera palabra que no fuera
    # auxiliar. Son verbos vacíos de contenido clínico —no aportan síntoma—, así
    # que dejarlos pasar no ensancha el alcance de forma peligrosa.
    r"quiero|quisiera|queria|pienso|pensado|pensar|pensando|ganas|en|creo|siquiera)"
)
_BRIDGE = re.compile(rf"^(?:\s*{_BRIDGE_WORD}\b)*\s*$", re.I)

# Una adversativa seguida de afirmación **cancela** la negación anterior: «ayer
# no tenía fiebre PERO hoy SÍ tengo» reporta fiebre, no la niega. La afirmación
# es elíptica —«sí tengo», sin repetir el síntoma—, así que buscar otra
# ocurrencia del término no sirve: hay que mirar la estructura.
#
# Hizo falta al completar los imperfectos en `_BRIDGE_WORD`. Antes «no tenía
# fiebre» no se reconocía como negación en absoluto, así que este caso salía
# bien **por accidente**: el sistema fallaba en la forma de negar más común del
# español hablado y acertaba en la excepción. Al arreglar lo primero quedó al
# descubierto lo segundo, y ningún arnés lo cubría.
#
# El costo asumido: «no he tenido fiebre pero sí dolor» deja de negar la fiebre.
# Es un falso positivo —una alerta de más—, que es la dirección declarada como
# aceptable en este módulo; el dolor iba a disparar por su cuenta de todas formas.
_REVIERTE = re.compile(r"\b(pero|aunque|sin\s+embargo|mas)\b[^.]{0,40}\bs[ií]\b", re.I)


def negado(text: str, start: int) -> bool:
    """¿La negación más cercana antes del síntoma realmente lo niega?

    Una ventana de N caracteres no basta: en "no aguanto el dolor en el pecho"
    el "no" niega el aguante, no el dolor, y en "no me baja la fiebre" niega la
    mejoría —la fiebre persiste—. Aquí la negación solo cuenta si entre ella y el
    síntoma hay únicamente conectores ("no TENGO fiebre").

    Ante la duda se considera NO negado: en seguridad clínica un falso positivo
    cuesta una alerta de más; un falso negativo, un paciente que no fue a
    urgencias.
    """
    window = text[max(0, start - 40):start]
    last = None
    for m in _NEG.finditer(window):
        last = m
    if last is None:
        return False
    if not _BRIDGE.match(window[last.end():]):
        return False
    # Lo que viene DESPUÉS del síntoma también cuenta: ver `_REVIERTE`.
    return not _REVIERTE.search(text[start:])


# ------------------------------------------------------------------ detección

def detect_red_flags(text: str) -> list[RuleFlag]:
    """Todas las ocurrencias, no solo la primera: en "ayer no tenía fiebre, pero
    hoy tengo fiebre de 40" la primera mención está negada y descartaría la regla
    completa, perdiendo la segunda —que es la que importa—."""
    flags: list[RuleFlag] = []
    for name, sev, rx in _RULES:
        for m in rx.finditer(text):
            if not negado(text, m.start()):
                flags.append(RuleFlag(name, sev, m.group(0)))
                break
    return flags


def max_severity(flags: list[RuleFlag]) -> str:
    if not flags:
        return "none"
    return max((f.severity for f in flags), key=SEVERITY_ORDER.index)


def max_sev(a: str, b: str) -> str:
    return a if SEVERITY_ORDER.index(a) >= SEVERITY_ORDER.index(b) else b


def severidad_de(nombre_flag: str) -> str:
    """Severidad declarada para el concepto de una marca detectada.

    Los umbrales extraídos de un documento llegan como `<concepto>_protocolo` y
    traen su severidad de la fuente; para ellos se usa la del concepto base.
    """
    base = (nombre_flag[:-len("_protocolo")]
            if nombre_flag.endswith("_protocolo") else nombre_flag)
    cfg = LEXICON.get(base)
    return cfg["severidad"] if cfg else "high"


def resumen_lexico() -> list[dict]:
    """Léxico cargado, para poder auditarlo desde la consola.

    Un vocabulario que decide escalamientos y que nadie puede inspeccionar es
    tan opaco como el modelo que se quería evitar.
    """
    return [
        {
            "concepto": concepto,
            "severidad": cfg["severidad"],
            "accion": ACTION_FOR[cfg["severidad"]],
            "terminos": list(cfg.get("terminos", [])),
            # Aparte de los términos: quien audita el léxico tiene que poder ver
            # que este concepto también dispara con palabras que el paciente NO
            # dijo, y por qué.
            "confusiones": list(cfg.get("confusiones", [])),
            "patrones": len(cfg.get("patrones", [])),
            "nota": cfg.get("nota", ""),
        }
        for concepto, cfg in LEXICON.items()
    ]
