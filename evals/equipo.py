"""Arnés del aviso al equipo clínico (server/equipo.py), sin red.

    uv run python -m evals.equipo

Un Discord de mentira recibe lo que se mandaría. Se comprueba lo que protege al
canal y a la llamada: que sin webhook no se intenta nada, que una señal avisa una
sola vez por llamada, que el tope por hora se respeta, que ninguna mención
notifica a nadie, y que un Discord caído no tumba la llamada.
"""
from __future__ import annotations

import asyncio
import sys

from server.equipo import MAX_TEXTO, Aviso, AvisosDeLlamada, CanalEquipo, mensaje

resultados: list[bool] = []
PASS, FAIL = "  [OK]", "  [FALLA]"


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    resultados.append(ok)
    print(f"{PASS if ok else FAIL} {nombre}" + (f" — {detalle}" if detalle and not ok else ""))


def aviso(concepto="dolor_toracico", severidad="critical", texto="me duele el pecho",
          origen="reglas", en_parcial=True) -> Aviso:
    return Aviso("abc123", concepto, severidad, "emergency", texto, "dolor en el pecho",
                 origen, en_parcial, 3)


class DiscordDeMentira:
    def __init__(self, respuesta=True, falla=None):
        self.recibidos: list[dict] = []
        self.respuesta, self.falla = respuesta, falla

    async def __call__(self, cuerpo: dict) -> bool:
        if self.falla:
            raise self.falla
        self.recibidos.append(cuerpo)
        return self.respuesta


async def main() -> None:
    print("== Sin webhook no se intenta nada ==")
    d = DiscordDeMentira()
    llamada = AvisosDeLlamada(CanalEquipo("", enviar=d), "abc123")
    check("inactivo sin URL", not llamada.activo)
    check("no manda nada", await llamada.avisar(aviso()) is None and not d.recibidos)
    check("sin canal (las pruebas de la llamada) tampoco",
          not AvisosDeLlamada(None, "abc123").activo)

    print("\n== El mensaje ==")
    m = mensaje(aviso(texto="@everyone me duele el pecho " + "x" * 600))
    e = m["embeds"][0]
    check("ninguna mención notifica a nadie", m["allowed_mentions"] == {"parse": []})
    check("lo dicho se recorta", len(e["description"]) <= MAX_TEXTO + 2, str(len(e["description"])))
    check("la emergencia se titula como tal", e["title"].startswith("🚨 EMERGENCIA"), e["title"])
    check("y en rojo", e["color"] == 0xF43F5E)
    alta = mensaje(aviso("fiebre", "high"))["embeds"][0]
    check("una alarma de hoy no se titula emergencia", alta["title"].startswith("⚠️ Revisar hoy"),
          alta["title"])
    check("el concepto se lee como palabras", "Dolor toracico" in e["title"], e["title"])
    campos = {f["name"]: f["value"] for f in e["fields"]}
    check("dice quién lo detectó y cuándo",
          campos["Lo detectó"] == "las reglas, mientras el paciente hablaba", campos["Lo detectó"])
    check("dice que la paciente es de demostración", "ficticio" in campos["Paciente"])
    juez = {f["name"]: f["value"] for f in mensaje(aviso(origen="juez"))["embeds"][0]["fields"]}
    check("el juez se nombra como el juez", juez["Lo detectó"] == "el juez de riesgo")

    print("\n== Una señal, un aviso por llamada ==")
    d = DiscordDeMentira()
    canal = CanalEquipo("https://discord.test/webhook", enviar=d)
    llamada = AvisosDeLlamada(canal, "abc123")
    check("el primero sale y se confirma", await llamada.avisar(aviso()) is True)
    check("la misma señal no se repite", await llamada.avisar(aviso()) is None)
    check("otra señal sí sale", await llamada.avisar(aviso("fiebre", "high")) is True)
    check("dos mensajes en total", len(d.recibidos) == 2, str(len(d.recibidos)))
    otra = AvisosDeLlamada(canal, "def456")
    check("otra llamada avisa la misma señal", await otra.avisar(aviso()) is True)

    print("\n== El tope por hora es de todo el proceso ==")
    ahora = [0.0]
    d = DiscordDeMentira()
    canal = CanalEquipo("https://discord.test/webhook", enviar=d, limite_por_hora=3,
                        reloj=lambda: ahora[0])
    for i in range(3):
        await AvisosDeLlamada(canal, f"l{i}").avisar(aviso())
    check("pasado el tope no sale", await AvisosDeLlamada(canal, "l9").avisar(aviso()) is None
          and len(d.recibidos) == 3, str(len(d.recibidos)))
    ahora[0] = 3601.0
    check("una hora después vuelve a salir",
          await AvisosDeLlamada(canal, "l10").avisar(aviso()) is True)

    print("\n== Un Discord caído no tumba la llamada ==")
    caido = CanalEquipo("https://discord.test/webhook",
                        enviar=DiscordDeMentira(falla=ConnectionError("sin red")))
    check("una excepción queda en False",
          await AvisosDeLlamada(caido, "x").avisar(aviso()) is False)
    rechazo = CanalEquipo("https://discord.test/webhook", enviar=DiscordDeMentira(respuesta=False))
    check("un rechazo queda en False", await AvisosDeLlamada(rechazo, "y").avisar(aviso()) is False)

    print("\n== Desde la alerta que recibe la página ==")
    a = Aviso.de_alerta("abc123", {"type": "alerta", "concepto": "fiebre", "severidad": "high",
                                   "accion": "escalate", "coincidencia": "calentura",
                                   "texto": "tengo calentura", "orden": 2, "en_parcial": False,
                                   "origen": "reglas"})
    check("conserva lo que dijo y por qué",
          a.texto == "tengo calentura" and a.motivo == "calentura")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
    ok = sum(resultados)
    print(f"\nRESULTADO: {ok}/{len(resultados)} comprobaciones del aviso al equipo.")
    raise SystemExit(0 if ok == len(resultados) else 1)
