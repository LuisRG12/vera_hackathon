"""Pruebas ADVERSARIALES de la capa determinista de alarmas.

A diferencia de `alarmas_base.py` —que verifica los casos para los que se
escribieron las reglas— este arnés está diseñado para ROMPERLAS. Cada caso nace
de una hipótesis sobre cómo puede fallar el detector con habla real de un
paciente colombiano.

  uv run python -m evals.alarmas_adversariales

Criterio rector: **un falso negativo es incomparablemente peor que un falso
positivo**. Un falso positivo genera una alerta de más; un falso negativo deja a
un paciente sin ir a urgencias. Por eso los casos FN son compuerta (exit 1) y
los FP se reportan como observación.
"""
from __future__ import annotations

from server.seguridad.reglas import SEVERITY_ORDER, detect_red_flags, max_severity

# (texto, severidad_mínima_esperada, hipótesis)
# Casos donde NO detectar es un fallo de seguridad del paciente.
FALSE_NEGATIVE_CASES = [
    # -- Negación que pertenece a OTRA cláusula de la frase --
    ("no aguanto el dolor en el pecho", "critical",
     "negación previa ajena ('no aguanto') anula un red-flag crítico"),
    ("ya no puedo más, me falta el aire", "critical",
     "'ya no' de queja general anula dificultad respiratoria"),
    ("no me baja la fiebre", "high",
     "'no me baja' = la fiebre PERSISTE, pero el 'no' la niega"),
    ("no he tomado nada y tengo fiebre de 39 grados", "high",
     "negación de la medicación anula el reporte de fiebre"),
    ("no se me quita el dolor y me salió pus en la herida", "high",
     "negación de mejoría anula un signo de infección"),

    # -- Solo se evalúa la PRIMERA ocurrencia de cada regla --
    ("ayer no tenía fiebre, pero hoy tengo fiebre de 40", "high",
     "la 1ª mención está negada y descarta la regla completa"),

    # -- Habla real: cifras en letras --
    ("tengo treinta y nueve de temperatura", "high",
     "los pacientes dicen la cifra en letras, no en dígitos"),
    ("la fiebre me subió a treinta y nueve y medio", "high",
     "cifra en letras (aquí 'fiebre' debería salvarlo)"),

    # -- Coloquialismo colombiano --
    ("estoy botando materia por la herida", "high",
     "'materia' = pus en habla coloquial colombiana"),
    ("la herida está botando un líquido feo y amarillo", "high",
     "descripción sin la palabra técnica"),

    # -- Signo descrito sin la palabra clave de la regla --
    ("me empapo una toalla higiénica cada hora", "high",
     "criterio textual del protocolo de cesárea, sin la palabra 'sangre'"),
    ("se me abrió la herida y se ven los puntos por dentro", "high",
     "dehiscencia: no existe regla que la cubra"),

    # -- Variantes de disnea y empeoramiento (llamada real del 26-jul) --
    ("sigo sin poder respirar bien", "critical",
     "'sin poder respirar' no estaba: solo 'no puedo respirar'"),
    ("me cuesta respirar", "critical",
     "forma habitual de reportar disnea"),
    ("pues hoy está un poco más fuerte", "moderate",
     "empeoramiento del dolor sin la palabra 'dolor' ni 'intenso'; el protocolo "
     "de colecistectomía pide reportar el dolor que aumenta"),
    ("el dolor no ha mejorado, sigue igual de fuerte", "moderate",
     "ausencia de mejoría = criterio del protocolo"),
]

# Casos donde detectar de más es ruido operativo (se reportan, no son compuerta).
FALSE_POSITIVE_CASES = [
    ("mi hija tiene fiebre, ¿me puedo contagiar en el postoperatorio?", "none",
     "la fiebre es de un tercero, no del paciente"),
    ("¿es normal un poco de secreción transparente los primeros días?", "none",
     "pregunta informativa sobre secreción serosa normal"),
    ("¿qué hago si me llega a dar fiebre más adelante?", "none",
     "pregunta hipotética a futuro, no un síntoma actual"),
    ("el médico me dijo que si hay pus llame de inmediato", "none",
     "cita de una instrucción, no un reporte de síntoma"),
]

PASS, FAIL, WARN = "  [OK]", "  [FALLA]", "  [AVISO]"


def _ge(actual: str, expected: str) -> bool:
    return SEVERITY_ORDER.index(actual) >= SEVERITY_ORDER.index(expected)


def main() -> int:
    fn_failed: list[tuple[str, str, str]] = []
    fp_noted: list[tuple[str, str, str]] = []

    print("== FALSOS NEGATIVOS (compuerta: no detectar es fallo de seguridad) ==")
    for text, expected, hypothesis in FALSE_NEGATIVE_CASES:
        flags = detect_red_flags(text)
        sev = max_severity(flags)
        ok = _ge(sev, expected)
        names = sorted(f.name for f in flags)
        print(f"{PASS if ok else FAIL} esperado>={expected:8s} obtenido={sev:8s} "
              f"flags={names!s:28s} | {text}")
        if not ok:
            print(f"         hipótesis confirmada: {hypothesis}")
            fn_failed.append((text, expected, hypothesis))

    print("\n== FALSOS POSITIVOS (observación: ruido operativo, no compuerta) ==")
    for text, expected, hypothesis in FALSE_POSITIVE_CASES:
        flags = detect_red_flags(text)
        sev = max_severity(flags)
        ok = sev == expected
        names = sorted(f.name for f in flags)
        print(f"{PASS if ok else WARN} esperado={expected:8s} obtenido={sev:8s} "
              f"flags={names!s:28s} | {text}")
        if not ok:
            print(f"         {hypothesis}")
            fp_noted.append((text, sev, hypothesis))

    total_fn = len(FALSE_NEGATIVE_CASES)
    print(f"\nFalsos negativos: {total_fn - len(fn_failed)}/{total_fn} detectados correctamente.")
    print(f"Falsos positivos: {len(fp_noted)}/{len(FALSE_POSITIVE_CASES)} casos con ruido.")

    if fn_failed:
        print("\n*** La capa determinista NO escalaría estos reportes ***")
        for text, expected, _ in fn_failed:
            print(f"    - \"{text}\"  (debía ser {expected})")
        print("\n    Un fallo aquí solo es un fallo del SISTEMA si el juez tampoco lo")
        print("    detecta: la decisión final es el máximo de las dos capas.")
    return 1 if fn_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
