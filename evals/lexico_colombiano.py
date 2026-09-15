"""Cobertura del léxico colombiano en la capa determinista (0 tokens).

  uv run python -m evals.lexico_colombiano

Vera atiende pacientes colombianos, y un paciente no dice «presento secreción
purulenta»: dice «está botando materia». Cada llamada real de la etapa de pruebas
descubrió dos o tres formas de decir las cosas que ninguna regla cubría —«un poco
más fuerte», «sin poder respirar», «botando materia»—, y cada una era un
escalamiento que no ocurría.

Este arnés mide las dos direcciones, porque ampliar vocabulario es justo lo que
introduce falsos positivos:

  - **POSITIVOS**: habla real que DEBE detectarse. Son compuerta.
  - **NEGATIVOS**: frases parecidas que NO deben disparar. También son compuerta:
    un agente que escala con todo se vuelve ruido y el equipo clínico lo apaga.
"""
from __future__ import annotations

import sys

from server.seguridad.reglas import (
    SEVERITY_ORDER,
    detect_red_flags,
    max_severity,
    resumen_lexico,
)

PASS, FAIL = "  [OK]", "  [FALLA]"

# (frase del paciente, concepto que debe aparecer, severidad mínima)
POSITIVOS = [
    # --- respiración ---
    ("no me entra el aire", "dificultad_respiratoria", "critical"),
    ("me quedo sin aire con solo caminar", "dificultad_respiratoria", "critical"),
    ("me fatigo al caminar hasta el baño", "dificultad_respiratoria", "critical"),
    ("siento que me asfixio", "dificultad_respiratoria", "critical"),
    ("me agito mucho al caminar", "dificultad_respiratoria", "critical"),
    ("me cuesta trabajo respirar", "dificultad_respiratoria", "critical"),

    # --- pecho ---
    ("siento el pecho apretado", "dolor_toracico", "critical"),
    ("tengo un peso en el pecho", "dolor_toracico", "critical"),
    ("una punzada en el pecho", "dolor_toracico", "critical"),

    # --- desmayo (colombianismos) ---
    ("me dio un yeyo en el baño", "perdida_conciencia", "critical"),
    ("me dio la pálida y me senté", "perdida_conciencia", "critical"),
    ("le dio un patatús", "perdida_conciencia", "critical"),
    ("vi todo negro", "perdida_conciencia", "critical"),
    ("se me fue la vista un momento", "perdida_conciencia", "critical"),
    ("perdí el sentido", "perdida_conciencia", "critical"),

    # --- fiebre (incluye 'quebranto' y 'destemplado') ---
    ("estoy destemplada desde anoche", "fiebre", "high"),
    ("ando con quebranto", "fiebre", "high"),
    ("estoy que ardo", "fiebre", "high"),
    ("me subió la temperatura", "fiebre", "high"),
    ("ando con escalofríos", "fiebre", "high"),
    ("tengo 39.5 grados", "fiebre", "high"),
    ("estoy hirviendo", "fiebre", "high"),
    ("tengo calentura", "fiebre", "high"),

    # --- infección ('materia' es el término clave) ---
    ("está botando materia", "infeccion", "high"),
    ("le sale materia por la herida", "infeccion", "high"),
    ("la herida huele maluco", "infeccion", "high"),
    ("sale un líquido amarillo espeso", "infeccion", "high"),
    ("se me puso roja la herida", "infeccion", "high"),
    ("la herida está caliente", "infeccion", "high"),
    ("creo que se me infectó", "infeccion", "high"),
    # Aquí estaban «he tenido inspección» y «sí yo diría que inspección»: lo que
    # Vosk entregaba cuando el paciente decía «infección». Con AssemblyAI esa
    # confusión no aparece (evals/confusiones.py) y se retiró del léxico.
    # La contraparte de la negación en imperfecto: una adversativa con
    # afirmación la cancela. La afirmación es elíptica —«sí tengo», sin repetir
    # el síntoma—, así que no basta con buscar otra ocurrencia del término.
    # Antes de reconocer el imperfecto esto salía bien por accidente.
    ("ayer no tenía fiebre pero hoy sí tengo", "fiebre", "high"),
    ("no tenía fiebre pero ahora sí", "fiebre", "high"),

    # --- sangrado ('cuajarones' = coágulos) ---
    ("estoy botando cuajarones", "sangrado_masivo", "high"),
    ("estoy chorreando sangre", "sangrado_masivo", "high"),
    ("sangra harto", "sangrado_masivo", "high"),
    ("ya empapé el apósito", "sangrado_masivo", "high"),
    ("no para de sangrar", "sangrado_masivo", "high"),

    # --- dehiscencia ---
    ("se me soltó un punto", "dehiscencia", "high"),
    ("se me descosió la herida", "dehiscencia", "high"),
    ("se reventaron los puntos", "dehiscencia", "high"),

    # --- empeoramiento (el hallazgo de la llamada real) ---
    ("hoy está un poco más fuerte", "empeoramiento", "moderate"),
    ("no se me quita", "empeoramiento", "moderate"),
    ("cada vez más", "empeoramiento", "moderate"),
    ("en vez de mejorar va peor", "empeoramiento", "moderate"),
    ("no me ha bajado", "empeoramiento", "moderate"),
    ("está peor que anoche", "empeoramiento", "moderate"),
    ("el dolor no cede", "empeoramiento", "moderate"),

    # --- dolor intenso (intensificadores colombianos) ---
    ("tengo un dolor tenaz", "dolor_intenso", "moderate"),
    ("me duele durísimo", "dolor_intenso", "moderate"),
    ("me duele un resto", "dolor_intenso", "moderate"),
    ("no aguanto el dolor", "dolor_intenso", "moderate"),
    ("es un dolor berraco", "dolor_intenso", "moderate"),

    # --- vómito ('trasbocar') ---
    ("estoy trasbocando todo", "vomito_persistente", "moderate"),
    ("devuelvo todo lo que como", "vomito_persistente", "moderate"),
    ("tengo el estómago revuelto", "vomito_persistente", "moderate"),
    ("tengo muchas náuseas", "vomito_persistente", "moderate"),

    # --- ictericia ---
    ("tengo los ojos amarillos", "ictericia", "moderate"),
    ("la orina como coca cola", "ictericia", "moderate"),
    ("estoy amarilla", "ictericia", "moderate"),

    # --- malestar general ---
    ("me siento muy maluca", "estado_general_malo", "moderate"),
    ("estoy descompuesto", "estado_general_malo", "moderate"),
    ("me siento aporreado", "estado_general_malo", "moderate"),
    ("no tengo fuerzas", "estado_general_malo", "moderate"),

    # --- retención ('obrar' = defecar) ---
    ("no he podido obrar", "retencion", "moderate"),
    ("no he expulsado gases", "retencion", "moderate"),
    ("no puedo orinar", "retencion", "moderate"),
    ("me arde al orinar", "retencion", "moderate"),

    # --- sin tildes: el STT y quien teclea las omiten ---
    ("no puedo respirar", "dificultad_respiratoria", "critical"),
    ("me desmaye esta manana", "perdida_conciencia", "critical"),
    ("tengo una infeccion en la herida", "infeccion", "high"),
    ("vision borrosa desde ayer", "preeclampsia", "high"),

    # --- tal como las entrega AssemblyAI (evals/confusiones.py) ---
    # Formatea: cifras en dígitos, mayúsculas, guiones, punto final. Las dos
    # primeras perdían la alarma entera antes de ajustar el léxico.
    ("Tengo 39 de temperatura.", "fiebre", "high"),
    ("La orina como Coca-Cola.", "ictericia", "moderate"),
    ("La temperatura me llegó a 40.", "fiebre", "high"),
    ("El termómetro marcó 39,5.", "fiebre", "high"),
    ("Tengo 38 y medio de temperatura.", "fiebre", "high"),
    # «yeyo» llegó como «yello»: lo absorbe la tolerancia al yeísmo que ya
    # traía el motor. Se fija aquí para que siga siendo cierto.
    ("Me dio un yello en el baño.", "perdida_conciencia", "critical"),

    # --- con voz real, 13 de septiembre: tal cual quedaron en el registro ---
    # La muletilla partía el término: «botando como materia» no disparaba.
    ("También te comento que, que la herida está botando como materia.",
     "infeccion", "high"),
    # «me dio un yeyo», dos veces, oído como la palabra portuguesa «jejum».
    ("Y hace como 2 minutos que me levanté al baño, medio en jejum.",
     "perdida_conciencia", "critical"),
    ("Medio pues un jejum, si no entendiste.", "perdida_conciencia", "critical"),
    # Estas ya salían bien, y se fijan para que sigan saliendo.
    ("Y te digo que me sale pus de la herida.", "infeccion", "high"),
    ("He tenido infección.", "infeccion", "high"),
    ("También te cuento que tengo 39 de temperatura.", "fiebre", "high"),
    # Dijo «me duele el pecho, ¿cierto? y se me pasa al brazo izquierdo»; llegó
    # «brazo» por «pecho» y partido en dos turnos. Ninguno de los dos alertaba.
    ("Y se me pasa como, se me pasa como al lado izquierdo el brazo.",
     "dolor_brazo_izquierdo", "high"),
    ("me duele el brazo izquierdo", "dolor_brazo_izquierdo", "high"),
    ("el dolor se me corre hacia el brazo izquierdo", "dolor_toracico", "critical"),
    # La segunda llamada, con los arreglos ya puestos.
    ("Hola, Vera. Te cuento que la herida me está botando como materia.",
     "infeccion", "high"),
    ("Cuando iba para el baño me dio un yeyo.", "perdida_conciencia", "critical"),
    ("Y pues en ese momento tengo dolor en el pecho, no sé si eso puede ser grave.",
     "dolor_toracico", "critical"),
]

# Frases que NO deben disparar nada. Un agente que escala con todo es ruido.
NEGATIVOS = [
    # Aquí estaba «no he tenido ninguna inspección», la contrapartida negada de
    # la confusión de Vosk que se retiró del léxico.
    #
    # Las cifras en dígitos, que ahora disparan fiebre, no pueden disparar con
    # cualquier número ni saltarse la negación.
    ("La temperatura bien, tengo 40 años.", "la cifra es la edad"),
    ("No tengo 39 de temperatura.", "cifra de fiebre, negada"),
    ("La temperatura me bajó a 36.", "temperatura normal"),
    # Voz real: la negación con muletillas alrededor. Si «como» ya no parte un
    # término, tampoco puede partir la negación y volverla un síntoma.
    ("Pero ahora pues ya no tengo fiebre ni escalofríos.", "negación, voz real"),
    ("No tengo como fiebre.", "muletilla entre la negación y el síntoma"),
    ("Ya no tengo pues fiebre.", "muletilla entre la negación y el síntoma"),
    # «Se me pasa» también es «se me quita»: sin destino no es irradiación.
    ("el dolor se me pasa con la pastilla", "«se me pasa» de mejoría"),
    ("tengo el brazo izquierdo bien", "el brazo, sin dolor"),
    # El imperfecto es la forma más común de negar hablando, y no se reconocía:
    # «no tenía fiebre» entraba como fiebre REPORTADA y la llamada escalaba. Se
    # vio por micrófono — el paciente dijo que no y Vera le contestó que «ha
    # presentado fiebre» y que necesitaba atención inmediata.
    ("no tenía fiebre", "negación en imperfecto"),
    ("no sentía dolor en la herida", "negación en imperfecto, otro verbo"),
    ("no veía sangrado", "negación en imperfecto, otro verbo"),
    ("todo bien, muchas gracias", "saludo normal"),
    ("me duele un poquito la herida", "molestia leve esperable"),
    ("el dolor va cediendo cada día", "mejoría, no empeoramiento"),
    ("ya no me duele nada", "negación explícita"),
    ("me siento mejor que ayer", "mejoría — no confundir con 'peor que ayer'"),
    ("no tengo fiebre ni escalofríos", "negación de fiebre"),
    ("la herida está limpia y seca", "estado normal de la herida"),
    ("no he vomitado nada", "negación de vómito"),
    ("no hay sangrado", "negación de sangrado"),
    ("ya pude obrar normal", "función recuperada — no es retención"),
    ("puedo respirar bien", "sin disnea"),
    ("estoy caminando todos los días sin problema", "actividad normal"),
    ("me tomé las pastas a la hora", "adherencia a la medicación"),
    ("la cicatriz se ve bien", "evolución normal"),
    ("¿cuándo puedo volver a manejar?", "pregunta administrativa"),
    ("dormí bien anoche", "sin síntomas"),
]


def _ge(actual: str, minimo: str) -> bool:
    return SEVERITY_ORDER.index(actual) >= SEVERITY_ORDER.index(minimo)


def main() -> int:
    fallos_pos: list[str] = []
    fallos_neg: list[str] = []

    print(f"== POSITIVOS: habla colombiana real ({len(POSITIVOS)} casos) ==")
    for frase, concepto, sev_min in POSITIVOS:
        flags = detect_red_flags(frase)
        nombres = {f.name for f in flags}
        sev = max_severity(flags)
        ok = concepto in nombres and _ge(sev, sev_min)
        if not ok:
            print(f"{FAIL} esperaba {concepto}/{sev_min}, obtuvo {sorted(nombres) or 'nada'}"
                  f"/{sev} | «{frase}»")
            fallos_pos.append(frase)

    print(f"       {len(POSITIVOS) - len(fallos_pos)}/{len(POSITIVOS)} detectados")

    print(f"\n== NEGATIVOS: no deben disparar ({len(NEGATIVOS)} casos) ==")
    for frase, motivo in NEGATIVOS:
        flags = detect_red_flags(frase)
        if flags:
            print(f"{FAIL} disparó {sorted(f.name for f in flags)} | «{frase}» ({motivo})")
            fallos_neg.append(frase)

    print(f"       {len(NEGATIVOS) - len(fallos_neg)}/{len(NEGATIVOS)} silenciosos")

    print("\n== Léxico cargado ==")
    total_terminos = 0
    for c in resumen_lexico():
        total_terminos += len(c["terminos"])
        print(f"  {c['severidad']:9s} {c['concepto']:26s} "
              f"{len(c['terminos']):3d} términos + {c['patrones']} patrones")
    print(f"  {'':9s} {'TOTAL':26s} {total_terminos:3d} términos")

    total = len(POSITIVOS) + len(NEGATIVOS)
    ok = total - len(fallos_pos) - len(fallos_neg)
    print(f"\nRESULTADO: {ok}/{total} comprobaciones de léxico.")
    if fallos_pos:
        print("*** Habla real que NO se detecta: cada una es un escalamiento perdido. ***")
    if fallos_neg:
        print("*** Falsos positivos: el ruido apaga la confianza en las alertas. ***")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
