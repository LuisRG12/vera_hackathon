"""Vigilancia de una llamada: el motor determinista leyendo todo lo que se oye.

Una por llamada. Recibe cada mensaje del reconocedor —parciales incluidos— y
decide, sin invocar ningún modelo, si hay que alertar. Es lo que hace verdad la
frase de la arquitectura: el motor lee cada palabra del paciente antes e
independientemente del modelo.

**Por qué también los parciales.** El reconocedor entrega el turno por tramos
mientras el paciente habla, y el turno cerrado llega cuando ya se calló. Leer
solo el cerrado haría esperar la alerta a que termine la frase entera, y un
paciente sin aire no la termina rápido. Medido con `evals/confusiones.py` sobre
128 frases: la señal ya estaba en un parcial una mediana de 0,85 s antes del
cierre, y hasta 3,2 s en las frases largas.

Leerlos no mete alertas falsas, y también está medido: de 69 señales vistas en
un parcial, ninguna dejó de estar en el turno cerrado. Tiene sentido que así
sea: la negación mira hacia atrás, así que ya está escrita cuando aparece el
síntoma, y lo que llega después solo puede *cancelar* una negación —«no tenía
fiebre, pero hoy sí»—, nunca crearla.

**Una alerta por concepto y por llamada.** Un paciente con fiebre la nombra
varias veces, y cada parcial la vuelve a traer. Al equipo clínico le sirve saber
que apareció, cuándo y con qué palabras, no recibir veinte avisos iguales. Un
concepto nuevo sí alerta aunque ya haya otra alerta abierta: la disnea después de
la fiebre no es la misma noticia.

**Qué alerta.** `high` escala al equipo clínico; `critical` es emergencia. Lo
`moderate` queda en el turno pero no alerta: un «me duele durísimo» amerita
consejo, y convertirlo en aviso sería la inflación de alarma que hace que el
equipo deje de mirar.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from server.seguridad.reglas import ACTION_FOR, detect_red_flags, max_severity
from server.seguridad.respuestas import ACOMPANAR, EMERGENCIA

ACCIONES_QUE_ALERTAN = ("escalate", "emergency")


@dataclass(frozen=True)
class Senal:
    concepto: str
    severidad: str
    # Las palabras exactas que dispararon. Una alerta que no puede decir por qué
    # saltó no se puede auditar, y el equipo clínico tiene que poder juzgarla.
    coincidencia: str

    @property
    def accion(self) -> str:
        return ACTION_FOR[self.severidad]


@dataclass(frozen=True)
class Alerta:
    senal: Senal
    texto: str
    orden: int
    en_parcial: bool


@dataclass
class Lectura:
    senales: list[Senal]
    riesgo: str
    nuevas: list[Alerta] = field(default_factory=list)
    # Lo que Vera responde cuando lo decide el código. Solo ante una emergencia
    # nueva: repetirlo en cada parcial sería hablarle encima al paciente.
    respuesta: str | None = None


class Vigilancia:
    def __init__(self) -> None:
        self.alertas: list[Alerta] = []
        self._alertados: set[str] = set()

    def leer(self, texto: str, orden: int, cerrado: bool) -> Lectura:
        flags = detect_red_flags(texto)
        senales = [Senal(f.name, f.severity, f.match) for f in flags]
        lectura = Lectura(senales=senales, riesgo=max_severity(flags))

        for s in senales:
            if s.accion not in ACCIONES_QUE_ALERTAN or s.concepto in self._alertados:
                continue
            self._alertados.add(s.concepto)
            alerta = Alerta(s, texto, orden, en_parcial=not cerrado)
            self.alertas.append(alerta)
            lectura.nuevas.append(alerta)

        if any(a.senal.severidad == "critical" for a in lectura.nuevas):
            ideacion = any(s.concepto == "ideacion_suicida" for s in senales)
            lectura.respuesta = ACOMPANAR if ideacion else EMERGENCIA
        return lectura
