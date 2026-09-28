"""El aviso al equipo clínico: cada escalamiento sale también a un canal del equipo.

**Por qué existe.** Vera le dice al paciente «ya estoy avisando a su equipo», y
hasta aquí la alerta solo aparecía en la misma página desde donde se llamaba:
un aviso que no salía de la llamada. El tutorial que AssemblyAI y lablab
publicaron para este hackathon lo dice de su propio SMS de confirmación: si es
simulado, hay que decirlo en voz alta. Ahora sale de verdad, a un canal donde lo
vería la enfermera de seguimiento, y la página solo marca «enviado» cuando el
canal confirmó que lo recibió.

**Slack, y no Discord, en el Space.** Se hizo primero con Discord, y desde el
Space cada aviso murió en `ConnectTimeout`. Medido desde adentro: Hugging Face
no deja salir conexiones a discord.com ni a api.telegram.org, las dos
plataformas de bots, y sí a hooks.slack.com. El dominio viejo de Discord,
discordapp.com, sí pasa, pero usarlo sería esquivar un bloqueo que la plataforma
puso a propósito. El código habla los dos formatos y elige por la URL: Discord
sigue sirviendo donde no esté bloqueado.

**Qué no puede hacer.** No decide nada: avisa de lo que la capa de seguridad ya
decidió, y si el canal no contesta la llamada sigue igual. Tampoco es el canal
de un producto —en producción esto es el tablero de la enfermera o su historia
clínica—: es el más corto que prueba que el aviso sale de la llamada.

**En inglés, salvo lo que dijo el paciente.** El canal lo mira el jurado del
hackathon, que no lee español, así que los rótulos van en inglés. Lo que dijo el
paciente va tal cual, en español —es la evidencia clínica y no se reescribe—, con
la traducción debajo, hecha por el mismo camino que el botón «Translate» de la
página. Si la traducción falla o tarda, el aviso sale sin ella: nunca espera.

**El Space es público**, así que lo que cualquiera diga en una llamada que escale
llega al canal. Por eso tres guardas: una alerta por señal en cada llamada (la
repetida es ruido, y el ruido es lo que hace que el equipo deje de mirar), un
tope por hora para todo el proceso, y ninguna mención: un «@channel» o un
«@everyone» dictado al micrófono llega como texto, sin notificar a nadie.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

# Lo que el paciente dijo se recorta: la alerta es para decidir si llamar, no
# para leer la transcripción entera, que ya está en la llamada.
MAX_TEXTO = 300

_COLOR = {"critical": 0xF43F5E, "high": 0xFB923C}
PACIENTE = "Demo patient (fictional discharge plan)"
PIE = "Vera · post-surgical follow-up"
# Lo que se espera a la traducción antes de mandar el aviso sin ella.
ESPERA_TRADUCCION_S = 4.0

# Los nombres en inglés de los 24 conceptos del léxico: la misma tabla que usa
# la página (web/index.html, `conceptos`), fija y sin modelo.
CONCEPTOS_EN = {
    "dificultad_respiratoria": "shortness of breath", "dolor_toracico": "chest pain",
    "perdida_conciencia": "loss of consciousness", "convulsion": "seizure",
    "ideacion_suicida": "suicidal ideation", "fiebre": "fever", "infeccion": "infection",
    "sangrado_masivo": "heavy bleeding", "dehiscencia": "wound opening",
    "signos_tvp": "signs of DVT", "compromiso_vascular": "impaired circulation",
    "taquicardia": "racing heart", "dolor_brazo_izquierdo": "left arm pain",
    "alteracion_mental": "confusion", "preeclampsia": "pre-eclampsia signs",
    "empeoramiento": "getting worse", "dolor_intenso": "severe pain",
    "vomito_persistente": "persistent vomiting", "distension_abdominal": "abdominal swelling",
    "diarrea": "diarrhea", "ictericia": "jaundice", "estado_general_malo": "feeling very unwell",
    "animo_depresivo": "low mood", "retencion": "urinary retention",
    "lo vio el juez": "flagged by the risk judge",
}

Traductor = Callable[[list[str]], Awaitable[list[str]]]


@dataclass(frozen=True)
class Aviso:
    llamada: str
    concepto: str
    severidad: str
    accion: str
    texto: str
    motivo: str
    origen: str          # reglas | juez
    en_parcial: bool
    orden: int

    @classmethod
    def de_alerta(cls, llamada: str, a: dict) -> Aviso:
        """Desde el mismo mensaje de alerta que recibe la página."""
        return cls(llamada, a["concepto"], a["severidad"], a["accion"], a.get("texto") or "",
                   a.get("coincidencia") or "", a.get("origen") or "reglas",
                   bool(a.get("en_parcial")), int(a.get("orden") or 0))

    @property
    def titulo(self) -> str:
        grave = "🚨 EMERGENCY" if self.severidad == "critical" else "⚠️ Review today"
        nombre = CONCEPTOS_EN.get(self.concepto, self.concepto.replace("_", " "))
        return f"{grave} · {nombre[:1].upper()}{nombre[1:]}"

    @property
    def detecto(self) -> str:
        return ("the risk judge" if self.origen == "juez"
                else "the rules, while the patient was still speaking" if self.en_parcial
                else "the rules, when the turn closed")


def _dicho(aviso: Aviso, en: list[str] | None) -> str:
    texto = f"«{_recortar(aviso.texto, MAX_TEXTO)}»"
    return f"{texto}\n“{_recortar(en[0], MAX_TEXTO)}”" if en and en[0] else texto


def _motivo(aviso: Aviso, en: list[str] | None) -> str:
    motivo = _recortar(aviso.motivo or "—", 200)
    return f"{motivo} — {_recortar(en[1], 200)}" if en and len(en) > 1 and en[1] else motivo


def _recortar(texto: str, n: int) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= n else texto[: n - 1].rstrip() + "…"


def mensaje_discord(aviso: Aviso, en: list[str] | None = None) -> dict:
    """El cuerpo que recibe un webhook de Discord. `en`: lo dicho y el porqué, en inglés."""
    return {
        "username": "Vera",
        # Nada de lo que llega aquí notifica a nadie por mención: el texto lo
        # dictó quien estaba en la llamada.
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": aviso.titulo,
            "description": _dicho(aviso, en),
            "color": _COLOR.get(aviso.severidad, _COLOR["high"]),
            "fields": [
                {"name": "Why", "value": _motivo(aviso, en), "inline": False},
                {"name": "Caught by", "value": aviso.detecto, "inline": True},
                {"name": "Call", "value": f"{aviso.llamada} · turn {aviso.orden}",
                 "inline": True},
                {"name": "Patient", "value": PACIENTE, "inline": False},
            ],
            "footer": {"text": PIE},
            "timestamp": datetime.now(UTC).isoformat(),
        }],
    }


def _plano(texto: str) -> dict:
    # Texto plano, no mrkdwn: en Slack, «<!channel>» o «<@U123>» dentro de un
    # mrkdwn notifican a la gente, y lo dictó quien estaba en la llamada.
    return {"type": "plain_text", "text": texto, "emoji": True}


def mensaje_slack(aviso: Aviso, en: list[str] | None = None) -> dict:
    """El cuerpo de un Incoming Webhook de Slack. `en`: lo dicho y el porqué, en inglés."""
    dicho = _dicho(aviso, en)
    return {
        # Lo que se ve en la notificación del celular y del escritorio.
        "text": f"{aviso.titulo} — «{_recortar(aviso.texto, MAX_TEXTO)}»",
        "attachments": [{
            "color": f"#{_COLOR.get(aviso.severidad, _COLOR['high']):06X}",
            "blocks": [
                {"type": "header", "text": _plano(_recortar(aviso.titulo, 150))},
                {"type": "section", "text": _plano(dicho)},
                {"type": "section", "fields": [
                    _plano(f"Why: {_motivo(aviso, en)}"),
                    _plano(f"Caught by: {aviso.detecto}"),
                    _plano(f"Call: {aviso.llamada} · turn {aviso.orden}"),
                    _plano(f"Patient: {PACIENTE}"),
                ]},
                {"type": "context", "elements": [_plano(PIE)]},
            ],
        }],
    }


class CanalEquipo:
    """Uno por proceso: el webhook y el tope por hora que comparten las llamadas."""

    def __init__(self, url: str, enviar=None, limite_por_hora: int = 30,
                 reloj=time.monotonic, traductor: Traductor | None = None) -> None:
        self._url = url.strip()
        self._traductor = traductor
        host = urlparse(self._url).hostname or ""
        self.nombre = "Slack" if host.endswith("slack.com") else "Discord"
        self._mensaje = mensaje_slack if self.nombre == "Slack" else mensaje_discord
        # `enviar` existe para probarlo sin red.
        self._enviar = enviar or self._enviar_http
        self._limite = limite_por_hora
        self._reloj = reloj
        self._enviados: deque[float] = deque()

    @property
    def configurado(self) -> bool:
        return bool(self._url)

    def _hay_cupo(self) -> bool:
        ahora = self._reloj()
        while self._enviados and ahora - self._enviados[0] > 3600:
            self._enviados.popleft()
        if len(self._enviados) >= self._limite:
            return False
        self._enviados.append(ahora)
        return True

    async def _enviar_http(self, cuerpo: dict) -> bool:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(self._url, json=cuerpo)
        return 200 <= r.status_code < 300

    async def _traducir(self, aviso: Aviso) -> list[str] | None:
        if self._traductor is None:
            return None
        try:
            return await asyncio.wait_for(
                self._traductor([aviso.texto, aviso.motivo or ""]), ESPERA_TRADUCCION_S)
        except Exception as exc:  # noqa: BLE001 — sin traducción, el aviso sale igual
            print(f"[equipo] aviso sin traducción ({type(exc).__name__})", flush=True)
            return None

    async def publicar(self, aviso: Aviso) -> bool | None:
        """True si el canal lo recibió, False si falló, None si no se intentó."""
        if not self.configurado or not self._hay_cupo():
            return None
        en = await self._traducir(aviso)
        try:
            ok = await self._enviar(self._mensaje(aviso, en))
        except Exception as exc:  # noqa: BLE001 — un aviso caído no tumba la llamada
            # Sin la URL en el registro: es el secreto del canal.
            print(f"[equipo] no se pudo avisar a {self.nombre} ({type(exc).__name__})",
                  flush=True)
            return False
        if not ok:
            print(f"[equipo] {self.nombre} rechazó el aviso", flush=True)
        return ok


class AvisosDeLlamada:
    """Los avisos de una llamada: uno por señal, aunque la señal vuelva a salir."""

    def __init__(self, canal: CanalEquipo | None, llamada: str) -> None:
        self._canal = canal
        self.llamada = llamada
        self._avisados: set[str] = set()

    @property
    def activo(self) -> bool:
        return self._canal is not None and self._canal.configurado

    @property
    def canal(self) -> str:
        return self._canal.nombre if self._canal else ""

    async def avisar(self, aviso: Aviso) -> bool | None:
        if not self.activo or aviso.concepto in self._avisados:
            return None
        self._avisados.add(aviso.concepto)
        return await self._canal.publicar(aviso)
