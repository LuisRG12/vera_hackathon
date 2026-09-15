"""Lectura incremental de la salida estructurada, para hablar mientras se genera.

En una llamada de voz no se puede esperar a que el JSON termine: cada frase se
sintetiza en cuanto está completa. Estas dos piezas convierten un flujo de JSON
parcial en frases hablables.

Son deliberadamente agnósticas del proveedor: comen texto JSON a pedazos, venga
de donde venga. Por eso se trajeron sin cambios de `server/agent/stream_parse.py`
en vera_voice_agent: allá comían la salida de un modelo local, aquí la del
gateway.
"""
from __future__ import annotations

import json

_UTT_KEY = '"utterance"'


class PartialToolJSON:
    """Extrae `utterance` de un JSON que todavía está llegando."""

    def __init__(self) -> None:
        self.buf = ""
        self._grounding_done = False
        self._utt_start = -1
        self._emitted = 0
        self.closed = False

    def feed(self, chunk: str) -> None:
        self.buf += chunk

    def grounding(self) -> dict | None:
        """Los campos anteriores a `utterance`, en cuanto estén completos."""
        if self._grounding_done:
            return None
        i = self.buf.find(_UTT_KEY)
        if i < 0:
            return None
        prefix = self.buf[:i].rstrip().rstrip(",")
        try:
            obj = json.loads(prefix + "}")
        except json.JSONDecodeError:
            return None
        self._grounding_done = True
        return obj

    def utterance_delta(self) -> str:
        """Texto nuevo de `utterance` desde la última llamada."""
        if self.closed:
            return ""
        if self._utt_start < 0:
            i = self.buf.find(_UTT_KEY)
            if i < 0:
                return ""
            colon = self.buf.find(":", i + len(_UTT_KEY))
            if colon < 0:
                return ""
            quote = self.buf.find('"', colon)
            if quote < 0:
                return ""
            self._utt_start = quote + 1

        raw, esc = [], False
        for ch in self.buf[self._utt_start:]:
            if esc:
                raw.append(ch)
                esc = False
                continue
            if ch == "\\":
                raw.append(ch)
                esc = True
                continue
            if ch == '"':
                self.closed = True
                break
            raw.append(ch)

        s = "".join(raw)
        # Se tolera un escape a medias al final del buffer: llega el `\` pero
        # todavía no el carácter que escapa.
        for candidate in (s, s[:-1]):
            try:
                text = json.loads('"' + candidate + '"')
                break
            except json.JSONDecodeError:
                continue
        else:
            return ""

        delta = text[self._emitted:]
        self._emitted = len(text)
        return delta


_ENDERS = ".?!…"


class SentenceSplitter:
    """Acumula fragmentos de texto y entrega frases completas.

    `min_chars` evita mandar al sintetizador un jadeo de tres palabras cuando el
    modelo abre con una interjección corta.
    """

    def __init__(self, min_chars: int = 25) -> None:
        self.pending = ""
        self.min_chars = min_chars

    def _cierra_frase(self, i: int) -> bool:
        """¿El signo en la posición `i` termina una frase de verdad?

        Un punto entre dígitos no termina nada: es un decimal. Se observó en una
        llamada real que la frase «fiebre igual o mayor a 38.5 grados» se partía
        en «…mayor a 38.» y «5 grados», así que el sintetizador decía «treinta y
        ocho punto», hacía una pausa, y seguía con «cinco grados». Un umbral
        clínico leído en dos pedazos es peor que uno leído mal.
        """
        if self.pending[i] != ".":
            return True
        anterior = self.pending[i - 1] if i > 0 else ""
        siguiente = self.pending[i + 1] if i + 1 < len(self.pending) else ""
        # Si todavía no llegó el carácter siguiente, se espera: puede ser un
        # decimal a medio llegar y cortar aquí sería irreversible.
        if not siguiente:
            return False
        return not (anterior.isdigit() and siguiente.isdigit())

    def push(self, delta: str) -> list[str]:
        self.pending += delta
        out: list[str] = []
        while True:
            idx = -1
            for i, ch in enumerate(self.pending):
                if ch in _ENDERS and i + 1 >= self.min_chars and self._cierra_frase(i):
                    idx = i
                    break
            if idx < 0:
                break
            out.append(self.pending[: idx + 1].strip())
            self.pending = self.pending[idx + 1:].lstrip()
        return out

    def flush(self) -> str:
        rest, self.pending = self.pending.strip(), ""
        return rest
