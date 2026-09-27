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

**El Space es público**, así que lo que cualquiera diga en una llamada que escale
llega al canal. Por eso tres guardas: una alerta por señal en cada llamada (la
repetida es ruido, y el ruido es lo que hace que el equipo deje de mirar), un
tope por hora para todo el proceso, y ninguna mención: un «@channel» o un
«@everyone» dictado al micrófono llega como texto, sin notificar a nadie.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

# Lo que el paciente dijo se recorta: la alerta es para decidir si llamar, no
# para leer la transcripción entera, que ya está en la llamada.
MAX_TEXTO = 300

_COLOR = {"critical": 0xF43F5E, "high": 0xFB923C}
PACIENTE = "Paciente de demostración (plan ficticio)"
PIE = "Vera · seguimiento postoperatorio"


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
        grave = "🚨 EMERGENCIA" if self.severidad == "critical" else "⚠️ Revisar hoy"
        return f"{grave} · {self.concepto.replace('_', ' ').capitalize()}"

    @property
    def detecto(self) -> str:
        return ("el juez de riesgo" if self.origen == "juez"
                else "las reglas, mientras el paciente hablaba" if self.en_parcial
                else "las reglas, al cerrar el turno")


def _recortar(texto: str, n: int) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= n else texto[: n - 1].rstrip() + "…"


def mensaje_discord(aviso: Aviso) -> dict:
    """El cuerpo que recibe un webhook de Discord."""
    return {
        "username": "Vera",
        # Nada de lo que llega aquí notifica a nadie por mención: el texto lo
        # dictó quien estaba en la llamada.
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": aviso.titulo,
            "description": f"«{_recortar(aviso.texto, MAX_TEXTO)}»",
            "color": _COLOR.get(aviso.severidad, _COLOR["high"]),
            "fields": [
                {"name": "Por qué", "value": _recortar(aviso.motivo or "—", 200), "inline": False},
                {"name": "Lo detectó", "value": aviso.detecto, "inline": True},
                {"name": "Llamada", "value": f"{aviso.llamada} · turno {aviso.orden}",
                 "inline": True},
                {"name": "Paciente", "value": PACIENTE, "inline": False},
            ],
            "footer": {"text": PIE},
            "timestamp": datetime.now(UTC).isoformat(),
        }],
    }


def _plano(texto: str) -> dict:
    # Texto plano, no mrkdwn: en Slack, «<!channel>» o «<@U123>» dentro de un
    # mrkdwn notifican a la gente, y lo dictó quien estaba en la llamada.
    return {"type": "plain_text", "text": texto, "emoji": True}


def mensaje_slack(aviso: Aviso) -> dict:
    """El cuerpo que recibe un Incoming Webhook de Slack."""
    dicho = f"«{_recortar(aviso.texto, MAX_TEXTO)}»"
    return {
        # Lo que se ve en la notificación del celular y del escritorio.
        "text": f"{aviso.titulo} — {dicho}",
        "attachments": [{
            "color": f"#{_COLOR.get(aviso.severidad, _COLOR['high']):06X}",
            "blocks": [
                {"type": "header", "text": _plano(_recortar(aviso.titulo, 150))},
                {"type": "section", "text": _plano(dicho)},
                {"type": "section", "fields": [
                    _plano(f"Por qué: {_recortar(aviso.motivo or '—', 200)}"),
                    _plano(f"Lo detectó: {aviso.detecto}"),
                    _plano(f"Llamada: {aviso.llamada} · turno {aviso.orden}"),
                    _plano(f"Paciente: {PACIENTE}"),
                ]},
                {"type": "context", "elements": [_plano(PIE)]},
            ],
        }],
    }


class CanalEquipo:
    """Uno por proceso: el webhook y el tope por hora que comparten las llamadas."""

    def __init__(self, url: str, enviar=None, limite_por_hora: int = 30,
                 reloj=time.monotonic) -> None:
        self._url = url.strip()
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

    async def publicar(self, aviso: Aviso) -> bool | None:
        """True si el canal lo recibió, False si falló, None si no se intentó."""
        if not self.configurado or not self._hay_cupo():
            return None
        try:
            ok = await self._enviar(self._mensaje(aviso))
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
