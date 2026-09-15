"""Casos base de la capa determinista de alarmas.

    uv run python -m evals.alarmas_base

Esta capa es la que sostiene el escalamiento cuando el modelo falla o no
responde, así que se prueba sola y sin invocarlo: corre en milisegundos y da
siempre lo mismo.
"""
from __future__ import annotations

import sys

from server.seguridad.reglas import detect_red_flags, max_severity

# (texto, severidad_esperada, flag_esperado_o_None)
CASES = [
    ("tengo fiebre de 39 grados", "high", "fiebre"),
    ("no tengo fiebre, me siento bien", "none", None),
    ("de repente no puedo respirar bien", "critical", "dificultad_respiratoria"),
    ("me duele mucho el pecho", "critical", "dolor_toracico"),
    ("sale pus con mal olor de la herida", "high", "infeccion"),
    ("estoy sangrando mucho, ya empapé una toalla", "high", "sangrado_masivo"),
    ("tengo la pantorrilla hinchada y me duele", "high", "signos_tvp"),
    ("me duele un poquito la herida", "none", None),
    ("todo bien, muchas gracias", "none", None),
    ("me desmayé esta mañana", "critical", "perdida_conciencia"),
]

# ASCII a propósito: la consola de Windows (cp1252) no imprime todo Unicode.
PASS, FAIL = "  [OK]", "  [FALLA]"


def main() -> int:
    passed = failed = 0
    for text, expect_sev, expect_flag in CASES:
        flags = detect_red_flags(text)
        sev = max_severity(flags)
        names = {f.name for f in flags}
        ok = sev == expect_sev and (expect_flag is None or expect_flag in names)
        line = PASS if ok else FAIL
        print(f"{line} sev={sev:9s} flags={sorted(names)!s:32s} | {text}")
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"\nResultado capa A: {passed}/{passed + failed} pasaron.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
