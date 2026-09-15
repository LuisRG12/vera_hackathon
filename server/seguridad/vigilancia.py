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

**Lo que ve un parcial, el turno cerrado no siempre lo confirma.** Con voz
sintética coincidieron siempre: de 69 señales vistas en un parcial, ninguna
faltó en el turno cerrado. Con voz real no. El parcial oyó «Me dio pues un
yeyo» y el turno cerrado lo reescribió como «Medio pues un jejum»: AssemblyAI
vuelve a transcribir el turno al cerrarlo, y esa segunda versión puede ser peor
que la primera. La alerta ya había salido, y se queda. Una alerta no se retira
porque la versión final diga otra cosa, por la misma razón que el motor, ante
la duda, no niega: lo más grave que se vio en un turno, en cualquiera de sus
parciales, es el riesgo de ese turno.

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

from server.seguridad.reglas import ACTION_FOR, detect_red_flags, max_sev, max_severity
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
    # Lo más grave visto en el turno hasta ahora, parciales incluidos. Puede ser
    # mayor que lo que dicen las `senales` de este texto: ver arriba.
    riesgo: str
    nuevas: list[Alerta] = field(default_factory=list)
    # Lo que Vera responde cuando lo decide el código. Solo ante una emergencia
    # nueva: repetirlo en cada parcial sería hablarle encima al paciente.
    respuesta: str | None = None


class Vigilancia:
    def __init__(self) -> None:
        self.alertas: list[Alerta] = []
        self._alertados: set[str] = set()
        self._riesgo_turno: dict[int, str] = {}

    def leer(self, texto: str, orden: int, cerrado: bool) -> Lectura:
        flags = detect_red_flags(texto)
        senales = [Senal(f.name, f.severity, f.match) for f in flags]
        riesgo = max_sev(max_severity(flags), self._riesgo_turno.get(orden, "none"))
        self._riesgo_turno[orden] = riesgo
        lectura = Lectura(senales=senales, riesgo=riesgo)

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

    def alertas_del_turno(self, orden: int) -> list[Alerta]:
        return [a for a in self.alertas if a.orden == orden]
