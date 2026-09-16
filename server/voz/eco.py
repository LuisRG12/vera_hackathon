"""Lo que el micrófono captó del parlante no es un turno del paciente.

Con la voz sonando, el micrófono oye a Vera y el reconocedor la transcribe como
si hubiera hablado el paciente. El navegador ya intenta filtrarlo, pero el
filtro del cliente **no puede ser el único**: es el lado que no controlamos —lo
puede romper un navegador distinto, una versión nueva o unos auriculares— y el
daño de un eco que pasa no es cosmético.

En una llamada real pasó así. Vera preguntó «¿Cuál es la temperatura actual?» y
el turno siguiente del «paciente» fue *«la temperatura actual»*. Tres palabras,
y el filtro del cliente descarta por debajo de cuatro. Las consecuencias en
cadena fueron tres: el eco consumió un ítem del checklist, dejó «fiebre» como
síntoma reportado —porque «temperatura» cuenta como mención de fiebre— y pagó
una consulta al RAG. El resumen de cierre acabó diciendo que el paciente había
reportado fiebre cuando nunca la mencionó.

**Por qué el servidor puede hacerlo mejor que el cliente.** El servidor sabe
exactamente qué frases emitió y cuándo, incluido el saludo. No tiene que
adivinar: compara contra el texto literal que mandó a sintetizar.

**Por qué no se descarta en silencio.** Un turno que desaparece sin dejar rastro
es indistinguible de un fallo del micrófono. Se descarta y **se dice**, para que
en la pantalla se vea que hubo eco y no un paciente ignorado.

**Procedencia.** Traído sin cambios de `server/voz/eco.py` en vera_voice_agent.
Cada regla de este módulo nació de una llamada real que salió mal, y ninguna de
esas causas depende de qué reconocedor transcriba ni de qué voz sintetice.
"""
from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass, field

# Cuánto tiempo puede tardar un eco en llegar. Es generoso a propósito: el
# reconocedor acumula audio y entrega el transcrito varios segundos después de
# que la frase sonó. Pasado ese plazo, una coincidencia ya es más probable que
# sea el paciente repitiendo que el parlante.
VENTANA_S = 30.0

# Cuántas frases se recuerdan. Un turno de Vera son dos o tres frases; con ocho
# se cubre el turno actual y el anterior, que es donde vive el eco.
MEMORIA = 8


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y solo letras y dígitos.

    El reconocedor no devuelve la puntuación ni las tildes de forma fiable, así
    que compararlas sería comparar ruido. Es la misma normalización que hace el
    cliente, a propósito: dos filtros que normalizan distinto se contradicen y
    el desacuerdo aparece justo en los casos raros.
    """
    plano = unicodedata.normalize("NFD", texto.lower())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in plano).split())


def _contenida(corta: str, larga: str) -> bool:
    """`corta` aparece literal y completa dentro de `larga`.

    Con espacios a los lados para no dar por contenida «dolor» dentro de
    «dolorido»: comparar subcadenas sin frontera de palabra convierte cualquier
    raíz común en una coincidencia.
    """
    return f" {corta} " in f" {larga} "


def _solapamiento(a: set[str], b: set[str]) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


# Cuántas palabras necesita una frase de Vera para que encontrarla DENTRO de un
# turno más largo del paciente pruebe algo. Con menos no prueba nada: Vera
# contestó «no» a una pregunta de sí o no, y a partir de ahí cualquier frase del
# paciente que llevara «no» —«no puedo tomar cerveza», que era su pregunta— se
# descartó como eco cuatro veces seguidas. Lo mismo con «caminar», que Vera dijo
# como turno completo.
MINIMO_CONTENIDA = 3

# Negaciones que el paciente añade y Vera no tenía. Ver `_niega_algo_nuevo`.
_NEGACIONES = frozenset({"no", "nunca", "tampoco", "ninguno", "ninguna", "nada", "nadie"})


def _niega_algo_nuevo(turno: set[str], dicha: set[str]) -> bool:
    """El paciente negó algo que la frase de Vera no negaba.

    Es la señal que separa **contestar** de **repetir**. Vera pregunta «¿ha
    tenido fiebre o escalofríos?»; su eco por el parlante sería «ha tenido fiebre
    o escalofríos», nunca «**no** ha tenido fiebre o escalofríos». Ese «no» es
    información que solo pudo poner el paciente.

    Hace falta porque la prueba de solapamiento no puede bajar más su umbral: una
    negación repite casi todas las palabras de la pregunta. En una llamada real
    el reconocedor entregó «no **ha** tenido fiebre o escalofríos» —con «ha» por
    «he»—, coincidió al 83% con la pregunta y se descartó como eco. La
    consecuencia no fue cosmética: la negación se perdió, y como el tema se
    mencionó suelto más adelante, **la fiebre acabó registrada como síntoma
    reportado en un paciente que dijo que no la tenía** — exactamente el daño
    que este módulo existe para evitar, pero al revés.
    """
    return bool((turno & _NEGACIONES) - dicha)

# Separadores de una enumeración de opciones. Ver `_opciones`.
_SEPARADORES = (" o ", " u ", ",", ";", "¿", "?")


def _opciones(dicha_original: str) -> set[str]:
    """Las alternativas que Vera acaba de ofrecer, si ofreció alguna.

    **Elegir una opción de las que Vera enumeró no es eco: es contestar.** Vera
    preguntó «¿el tipo de ejercicio que le parece bien? ¿caminar, estirar o algo
    más intenso?» y el paciente respondió «algo más intenso» — tres veces, y las
    tres se descartaron como eco porque su respuesta está, literalmente, dentro
    de la pregunta. Es el modo de fallo más incómodo del filtro: cuanto más clara
    y directa es la respuesta, más se parece a un eco.

    Se trocea por los separadores de una enumeración y se queda con los trozos
    cortos, que es la forma que tiene una opción. Un fragmento largo de la frase
    de Vera no es una opción: es un trozo de su frase, o sea, un eco de verdad.

    **Trabaja sobre el texto ORIGINAL, no sobre el normalizado**, y esa es la
    diferencia entre que funcione y que funcione a medias. `normalizar` borra la
    puntuación, así que de todos los separadores solo sobrevivía « o »: la
    primera versión partía «¿le ha bajado, sigue igual o le ha aumentado?» en dos
    trozos, reconocía «le ha aumentado» como opción y dejaba «sigue igual» dentro
    de un fragmento demasiado largo. En una llamada real el paciente contestó
    «sigue igual» tres veces y las tres se descartaron como eco.
    """
    trozos = [dicha_original]
    for sep in _SEPARADORES:
        trozos = [p for t in trozos for p in t.split(sep)]
    return {n for t in trozos if 0 < len((n := normalizar(t)).split()) <= 4}


@dataclass
class RegistroDeVoz:
    """Lo que el agente acaba de decir, para no confundirlo con el paciente."""

    ventana_s: float = VENTANA_S
    memoria: int = MEMORIA
    # (normalizado, original, cuándo). Se guarda el original **además** del
    # normalizado porque `_opciones` necesita la puntuación que `normalizar`
    # borra: sin comas ni signos de interrogación, una enumeración deja de
    # parecerlo. Las comparaciones de eco siguen usando el normalizado.
    _dichas: list[tuple[str, str, float]] = field(default_factory=list)

    def recordar(self, texto: str, ahora: float | None = None) -> None:
        norma = normalizar(texto)
        if not norma:
            return
        self._dichas.append(
            (norma, texto, ahora if ahora is not None else time.monotonic()))
        del self._dichas[:-self.memoria]

    def _recientes(self, ahora: float) -> list[tuple[str, str]]:
        return [(n, o) for n, o, cuando in self._dichas
                if ahora - cuando <= self.ventana_s]

    def es_eco(self, texto: str, ahora: float | None = None,
               reproduciendo: bool = False) -> bool:
        """Si esto lo dijo Vera y no el paciente.

        Antes de las pruebas hay una **exención**: si el turno coincide con una de
        las opciones que Vera acababa de enumerar, es una respuesta y no un eco
        (ver `_opciones`).

        Tres pruebas, de la más segura a la más laxa:

        1. **Contención literal**, en los dos sentidos pero con distinta
           exigencia. Es la que atrapa el caso real: el reconocedor entrega un
           trozo de la frase de Vera, no la frase entera. Basta con dos palabras
           porque la coincidencia es exacta y contigua. En el sentido inverso
           —una frase de Vera dentro de un turno más largo del paciente— se le
           exigen `MINIMO_CONTENIDA` palabras, porque un «no» suelto cabe en
           cualquier cosa.
        2. **Solapamiento de vocabulario**, para cuando el reconocedor cambió
           alguna palabra. Exige cinco palabras y un 80 % de coincidencia: con
           menos, «no he tenido fiebre» respondiendo a «¿ha tenido fiebre?» se
           descartaría como eco, y esa es una respuesta clínica real.
        3. **Palabra suelta mientras suena el audio**, solo entonces. Fuera de la
           reproducción una palabra puede ser una respuesta legítima; durante
           ella, casi siempre es el parlante. El piso de 4 letras deja fuera
           «sí», «no», «ya», «ah», «ok» —las respuestas cortas más comunes—,
           que además nunca aparecen en el vocabulario fijo de Vera, así que
           coincidir con ellas por azar es el caso raro y no el típico.

        Ante duda **no es eco**: descartar un turno del paciente lo deja hablando
        solo, que es peor que atender un eco.
        """
        ahora = ahora if ahora is not None else time.monotonic()
        t = normalizar(texto)
        if not t:
            return False
        palabras = t.split()
        recientes = self._recientes(ahora)

        for dicha, original in recientes:
            if t in _opciones(original):
                continue
            # Los dos sentidos de la contención no son simétricos y por eso no
            # llevan la misma exigencia. Que el turno del paciente esté dentro de
            # la frase de Vera es la firma del eco —el reconocedor entrega un
            # trozo—. Que la frase de Vera esté dentro de un turno más largo del
            # paciente solo dice algo si esa frase tiene cuerpo propio.
            if len(palabras) >= 2 and _contenida(t, dicha):
                return True
            if (len(palabras) >= 2 and len(dicha.split()) >= MINIMO_CONTENIDA
                    and _contenida(dicha, t)):
                return True
            # El mínimo también aquí, y por la misma asimetría: `_solapamiento`
            # divide por el conjunto más pequeño, así que una frase de Vera de una
            # palabra da 1.0 contra cualquier turno que la contenga. Sin esto, un
            # «no» dejaba fuera «no no puedo tomar cerveza» por la regla 2 aunque
            # la regla 1 ya lo hubiera dejado pasar.
            dichas_p = set(dicha.split())
            if (len(palabras) >= 5 and len(dichas_p) >= MINIMO_CONTENIDA
                    and not _niega_algo_nuevo(set(palabras), dichas_p)
                    and _solapamiento(set(palabras), dichas_p) >= 0.8):
                return True

        if reproduciendo and len(palabras) == 1 and len(palabras[0]) >= 4:
            return any(_contenida(t, dicha) for dicha, _ in recientes)
        return False
