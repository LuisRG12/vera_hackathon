"""Léxico clínico colombiano: cómo dice un paciente lo que el protocolo nombra.

**Procedencia.** Traído de `server/agent/lexicon.py` en
[vera_voice_agent](https://github.com/LuisRG12/vera_voice_agent) (agosto 2026,
commit 1827f0e). Las notas de cada entrada conservan la llamada o la medición
que la originó allá; algunas nombran piezas de ese proyecto que aquí todavía no
existen —el juez de riesgo, la consola, el índice de hechos clínicos—.

Durante el reto cambió lo que dependía del reconocedor: el bloque de
confusiones, re-medido contra AssemblyAI, y las cifras de `fiebre`, que ahora
llegan en dígitos. Ver `evals/confusiones.py` y docs/bitacora.md.

**Qué es y qué NO es.** Esto es *comprensión del habla del paciente*, no
conocimiento clínico. El contenido clínico —qué umbral, qué signo de alarma,
qué protocolo— vive en los documentos y llega por el Clinical Facts Index. Acá
solo está el puente entre «botando materia» y el concepto `infeccion` que el
documento sí nombra. Por eso el léxico puede crecer sin tocar el conocimiento, y
el conocimiento puede cambiar sin tocar el léxico.

**Por qué es datos y no código.** Antes cada expresión nueva exigía editar una
expresión regular a mano. Cada llamada real encontraba dos o tres formas de decir
las cosas que nadie había anticipado («un poco más fuerte», «botando materia»,
«sin poder respirar»), y arreglarlas requería a alguien cómodo con regex. Acá los
términos son **frases planas**: el motor (`reglas.py`) las compila. Un
clínico puede añadir «me siento aporreado» sin saber programar.

**Convenciones de los términos**
  - Frase plana: se compara con límites de palabra e **insensible a tildes**
    («cesarea» encuentra «cesárea»), porque el STT y quien teclea las omiten.
  - Sufijo `*`: prefijo. `empeor*` cubre empeoró, empeorando, empeorado.
  - `patrones`: escotilla de escape para lo que una lista de frases no expresa
    (cifras, proximidad entre dos palabras). Se usa lo mínimo posible.
  - `confusiones`: palabras que **el reconocedor pone en boca del paciente** y
    que él no dijo. Ver abajo.

**Por qué `confusiones` va aparte de `terminos`.** Los términos son habla del
paciente y los revisa un clínico; una confusión es un defecto del reconocedor y
la revisa quien mira transcripciones. Mezclarlos dejaría «inspeccion» sentado
junto a «botando materia» en una lista que un clínico debe poder leer y ampliar.

`compilar_termino` ya absorbe lo que el reconocedor deforma por **fonética**
—yeísmo, seseo, /s/ aspirada, hache muda—. Lo que no absorbe, porque no es
fonética, es que el modelo de lenguaje de Vosk **cambie una palabra rara por una
común que suena parecido**. Ahí no hay clase de sonido que tolerar: hay un par
observado, y se declara uno a uno con la llamada que lo produjo.

**Las confusiones de Vosk no se heredaron.** Son de un reconocedor concreto, y la
única que había —«inspección» por «infección»— no apareció nunca con AssemblyAI,
ni en el barrido de `evals/confusiones.py` ni con voz real. Conservarla era pagar
su falso positivo sin su beneficio. Las que hay ahora son de AssemblyAI y se
declaran con la transcripción que las mostró. Lo que su formateo deforma —cifras
en dígitos, guiones— no es una confusión de palabra sino de escritura, y se
absorbe en el patrón o en `compilar_termino`.

**Por qué no se corrige el texto con un modelo.** Sería poner un generativo
delante de la capa determinista: las reglas dejarían de leer al paciente para
leer la salida de un modelo, y el mismo que «da sentido» a «he tenido inspección»
puede darle el de «me hicieron una inspección médica» y borrar la alarma. La
garantía de que lo crítico sobrevive aunque el modelo se caiga se perdería
entera, y costaría una invocación más en la ruta crítica de cada turno. Esto hace
lo mismo, sin modelo, y **solo puede producir conceptos que ya están aquí
declarados**: no inventa vocabulario nuevo.

**Criterio de severidad.** Solo va a `critical` lo inequívoco: una expresión que
en boca de un paciente postoperado no puede significar otra cosa. Ante ambigüedad
se baja de nivel, nunca se omite — un falso positivo cuesta una alerta de más; un
falso negativo, un paciente que no fue a urgencias.
"""
from __future__ import annotations

# concepto -> {severidad, terminos, patrones, nota}
LEXICON: dict[str, dict] = {

    # ---------------------------------------------------------------- críticos
    "dificultad_respiratoria": {
        "severidad": "critical",
        "terminos": [
            "no puedo respirar", "no logro respirar", "sin poder respirar",
            "me cuesta respirar", "me cuesta trabajo respirar", "cuesta respirar",
            "dificultad para respirar", "dificultad al respirar",
            "falta de aire", "me falta el aire", "no me entra el aire",
            "me ahogo", "ahogad*", "me asfixio", "asfixiad*",
            "me quedo sin aire", "me agito mucho", "me agito al caminar",
            "respiro con dificultad", "no alcanzo el aire",
            # Colombia: "fatiga" referida al esfuerzo respiratorio.
            "me fatigo al caminar", "me fatigo mucho", "fatiga al respirar",
        ],
        "nota": "En la costa colombiana 'fatiga' sola puede significar náusea, "
                "así que solo se aceptan las formas ligadas a respirar o caminar.",
    },
    "dolor_toracico": {
        "severidad": "critical",
        "terminos": [
            "dolor en el pecho", "dolor de pecho", "dolor toracico",
            "opresion en el pecho", "presion en el pecho", "peso en el pecho",
            "me aprieta el pecho", "siento el pecho apretado",
            "me duele el pecho", "punzada en el pecho", "ardor en el pecho",
        ],
        "patrones": [
            r"(duele|molesta|aprieta|opri)\w*.{0,15}pecho",
            r"pecho.{0,12}(apretad|oprimid|duele|pesad)",
            # Irradiación. Un paciente nunca dice «irradia»: dice que el dolor
            # «se le corre», «le sube» o «le baja» hacia el brazo, el cuello o
            # la quijada. Se exige un destino de territorio cardiaco —brazo,
            # cuello, mandíbula, hombro— porque «el dolor me baja por la pierna»
            # es ciática, no isquemia.
            # «me duele» es más frecuente que «el dolor»: exigir el sustantivo
            # dejaba fuera «me duele y me sube hacia la quijada».
            r"(dolor|duele|molest\w*)[^.]{0,25}"
            r"(se (me |le )?corre|se (me |le )?pasa|(me |le )?sube|(me |le )?baja|irradia)"
            r"[^.]{0,20}(brazo|cuello|mand[ií]bula|quijada|hombro)",
        ],
    },
    "perdida_conciencia": {
        "severidad": "critical",
        "terminos": [
            "me desmay*", "desmayo", "desmayad*",
            "perdi el conocimiento", "perdi el sentido", "me desvaneci",
            "me fui de para atras", "me cai al piso",
            # Colombianismos confirmados: patatús, yeyo, la pálida.
            "patatus", "me dio un yeyo", "un yeyo", "me dio la palida",
            "se me fue la vista", "vi todo negro", "todo se puso oscuro",
            "quede inconsciente", "no me acuerdo de nada",
            # Reportado por un tercero: quien contesta la llamada puede ser un
            # familiar, y entonces el signo no se narra en primera persona.
            "no despierta", "no se despierta", "no reacciona",
            "no lo puedo despertar", "no la puedo despertar",
        ],
        # La primera confusión de AssemblyAI, oída con voz real el 13 de
        # septiembre: «me dio un yeyo» llegó dos veces como «jejum» —palabra
        # portuguesa—: «medio en jejum» y «Medio pues un jejum». En la segunda,
        # el parcial había oído bien y el turno cerrado lo reescribió. Nadie dice
        # «jejum» en una llamada en español, así que el costo es nulo.
        "confusiones": ["jejum"],
        "nota": "patatús / yeyo / la pálida: desvanecimiento en habla popular "
                "colombiana. Confirmado en diccionarios de colombianismos.",
    },
    "convulsion": {
        "severidad": "critical",
        "terminos": ["convulsi*", "ataque epileptico", "me dio un ataque",
                     "temblores incontrolables", "espasmos por todo el cuerpo"],
    },
    "ideacion_suicida": {
        "severidad": "critical",
        "terminos": [
            "no quiero seguir viviendo", "no quiero vivir mas",
            "ya no quiero vivir", "para que sigo viviendo", "para que sigo aqui",
            "no le veo sentido a la vida", "no le encuentro sentido a la vida",
            "quiero acabar con todo", "acabar con mi vida", "quitarme la vida",
            "hacerme daño", "hacerle daño al bebe", "hacerle daño a mi bebe",
            "estarian mejor sin mi", "estaria mejor sin mi",
            "no quiero despertar", "ojala no despertara",
            "quiero desaparecer", "me quiero desaparecer",
        ],
        "patrones": [
            # «Me quiero morir» es la trampa del español colombiano: casi siempre
            # es hipérbole («me quiero morir del dolor», «de la pena», «de la
            # risa»). Se acepta la frase SOLO cuando no viene seguida de aquello
            # de lo que uno se muere. Cuando sí lo trae, el motivo real —el
            # dolor— ya escala por su propio concepto, así que excluirla aquí no
            # deja al paciente sin red.
            r"(me quiero morir|quiero morirme|deseo morirme)"
            r"(?![^.]{0,30}(dolor|pena|verg|risa|hambre|susto|calor|fr[ií]o|sue[ñn]o|aburri))",
        ],
        "nota": "Ampliación deliberada del alcance más allá de la complicación "
                "física. La depresión posparto y la ideación tras cirugía mayor "
                "son riesgo real —el protocolo de cesárea entra aquí— y son "
                "señal de TEXTO, citable y auditable: es la versión defendible "
                "de «detectar sentimiento», que por prosodia no se podría "
                "sustentar ante un comité clínico. Ningún documento semilla la "
                "cubre todavía, pero la acción correcta —entregar a un humano— "
                "no necesita documento que la fundamente. **Requiere validación "
                "del equipo clínico y un protocolo propio antes de producción**: "
                "ante ideación, la conducta estándar es acompañar, no despedir "
                "la llamada con «acuda a urgencias».",
    },

    # ------------------------------------------------------------------- altos
    "fiebre": {
        "severidad": "high",
        "terminos": [
            "fiebre", "fiebron", "calentura", "destemplad*", "destemplanza",
            "quebranto", "estoy quebrantad*",
            "temperatura alta", "me subio la temperatura", "ardiendo en fiebre",
            "estoy hirviendo", "estoy que ardo", "el cuerpo caliente",
            "escalofrio*", "escalofrios", "tiritando", "temblando de frio",
            "me da frio y calor", "sudo frio", "sudoracion",
        ],
        "patrones": [
            # Cifras: los umbrales reales los pone el documento (Clinical Facts
            # Index). Esto cubre las formas escritas y habladas más comunes.
            # Con decimales: "39.5 grados" no disparaba porque el patrón exigía
            # que "grados" siguiera inmediatamente a la parte entera.
            #
            # AssemblyAI escribe las cifras en dígitos —Vosk las entregaba en
            # letras— y así «tengo treinta y nueve de temperatura» llegó como
            # «Tengo 39 de temperatura.»: fiebre alta que pasaba de `high` a
            # `none`. Medido con evals/confusiones.py. Por eso la cifra en dígitos
            # acepta ahora las mismas colas que la cifra en letras.
            r"(38[.,][5-9]|38\s+y\s+medio|39([.,]\d)?|4[01]([.,]\d)?)\s*(grados|°|de\s+(temperatura|fiebre))",
            # La cifra DESPUÉS de nombrar la temperatura o el termómetro: «la
            # temperatura me llegó a 40», «el termómetro marcó 39,5». Ninguna de
            # las dos disparaba, ni escrita ni transcrita. Se excluyen las
            # unidades que no son de temperatura para que «la temperatura bien,
            # tengo 40 años» no sea una fiebre.
            r"(temperatura|term[oó]metro)[^.]{0,20}"
            r"\b(38[.,][5-9]|38\s+y\s+medio|39([.,]\d)?|4[01]([.,]\d)?|treinta y (ocho y medio|nueve)|cuarenta)\b"
            r"(?!\s*(a[ñn]os|kilos|minutos|horas|d[ií]as|semanas|mil|pesos))",
            r"(treinta y (ocho y medio|nueve)|cuarenta)\s*(grados|de\s+(temperatura|fiebre))",
        ],
        "nota": "'quebranto' en Colombia = destemplanza, temperatura elevada sin "
                "llegar a fiebre franca. Se trata como fiebre: la capa B matiza.",
    },
    "infeccion": {
        "severidad": "high",
        "terminos": [
            "pus", "supura*", "purulent*", "infectad*", "infeccion",
            "mal olor", "huele feo", "huele maluco", "hediond*", "olor feo",
            "secrecion", "esta botando materia", "botando materia",
            "sale materia", "echando materia", "materia por la herida",
            "liquido amarillo", "liquido verdoso", "liquido feo",
            "la herida caliente", "herida roja", "se puso roja la herida",
            "enrojecimiento", "esta enrojecid*", "rojo alrededor",
            "la herida podrida", "se me infecto",
        ],
        "patrones": [
            r"l[ií]quido\b[^.]{0,25}(amarill|verdos|purulent|feo|espes)",
            r"(herida|incisi[oó]n|puntos)[^.]{0,20}(caliente|roj|hinchad|dur[ao]\b)",
            # "se me puso roja la herida": el pronombre intercalado rompía la
            # frase literal, y las combinaciones (puso/volvió/se ha puesto ×
            # rojo/caliente/hinchado) son demasiadas para enumerarlas.
            r"se (me |le )?(puso|ha puesto|volvi[oó]|torn[oó])\s+(muy\s+)?(roj|caliente|hinchad|dur[ao]\b|morad)",
        ],
        # Aquí estuvo `"confusiones": ["inspeccion", "inspeccionado"]`. En la
        # llamada 83 del proyecto original el paciente dijo «he tenido infección»
        # tres veces y Vosk entregó «inspección» las tres —su modelo de lenguaje
        # prefiriendo la palabra frecuente—, y el turno salió con riesgo `none`.
        # Con AssemblyAI esa frase llega bien («He tenido infección.») y la
        # confusión no apareció en ninguna de las 128 frases del barrido. Se
        # retira: su costo era una alerta cada vez que un paciente contara que le
        # hicieron una inspección, y ya no compra nada.
        "nota": "'materia' = pus en habla coloquial colombiana. Es el término "
                "que más aparece y no estaba cubierto.",
    },
    "sangrado_masivo": {
        "severidad": "high",
        "terminos": [
            "hemorragia", "mucha sangre", "demasiada sangre",
            "botando sangre", "echando sangre", "chorreando sangre",
            "no para de sangrar", "no cesa el sangrado", "sangra mucho",
            "un chorro de sangre", "empapad* de sangre",
            # Colombia: "cuajarones" = coágulos.
            "cuajarones", "coagulos grandes", "cuajos de sangre",
        ],
        "patrones": [
            r"sangr\w*.{0,25}(mucho|abundante|empapa|no para|no cesa|chorro|harto|un resto)",
            r"empap\w*\s+(una\s+|la\s+|el\s+)?(toalla|compresa|pa[ñn]al|ap[oó]sito|gasa)",
        ],
        "nota": "'cuajarones' es como se dicen los coágulos en Colombia.",
    },
    "dehiscencia": {
        "severidad": "high",
        "terminos": [
            "se me abrio la herida", "se abrio la herida", "herida abierta",
            "se me abrieron los puntos", "se soltaron los puntos",
            "se me solto un punto", "se reventaron los puntos",
            "se me descosio", "se me revento la herida",
            "se ven los puntos", "se salieron los puntos",
        ],
        "patrones": [r"se (me )?(abri[oó]|revent[oó]|solt[oó]|descosi[oó])[^.]{0,20}(herida|punto|incisi[oó]n)"],
    },
    "signos_tvp": {
        "severidad": "high",
        "terminos": [
            "pantorrilla hinchada", "pierna hinchada", "pierna inflamada",
            "la pantorrilla dura", "coagulo", "trombosis",
            "una pierna mas gorda", "me duele la pantorrilla",
        ],
        "patrones": [r"(pantorrilla|pierna)\s+\w*\s*(hinchad|inflamad|dur[ao]|caliente)",
                     r"dolor.{0,20}pantorrilla"],
    },
    "compromiso_vascular": {
        "severidad": "high",
        "terminos": [
            "se me durmio la pierna", "la pierna dormida", "no siento la pierna",
            "la pierna helada", "la pierna fria", "el pie frio", "el pie helado",
            "los dedos morados", "la pierna morada", "el pie morado",
            "se me puso morad*", "la pierna palida", "no siento el pie",
            "sin pulso", "la mano dormida y fria",
        ],
        "patrones": [
            r"(pierna|pie|mano|dedos?|brazo)[^.]{0,25}(helad|fri[ao]|morad|dormid|sin sensibilidad)",
            r"(no siento|no me responde)[^.]{0,15}(la pierna|el pie|la mano|los dedos)",
        ],
        "nota": "Hallazgo del arnés de jerga sin cobertura: «se me durmió la "
                "pierna y la tengo helada» daba `none` en reglas y `moderate` en "
                "el juez — **sin protección de ninguna capa**. Una extremidad "
                "fría e insensible en un postoperatorio es compromiso vascular y "
                "puede costar el miembro. Ni el léxico ni el protocolo semilla "
                "lo nombraban: queda como pregunta para el equipo clínico.",
    },
    "taquicardia": {
        "severidad": "high",
        "terminos": [
            "taquicardia", "palpitaciones", "palpitacion",
            "el corazon me late muy rapido", "el corazon muy rapido",
            "se me acelera el corazon", "el corazon acelerado",
            "el corazon a mil", "se me sale el corazon",
            "siento el corazon en la garganta", "el pulso muy rapido",
            "el corazon me late durisimo", "el corazon disparado",
        ],
        "patrones": [
            # Anclados a corazón/pulso a propósito: «me late la herida muy
            # fuerte» es una herida pulsátil —otro concepto—, y etiquetarla como
            # taquicardia rompería la trazabilidad que la consola muestra.
            r"coraz[oó]n[^.]{0,25}(muy r[aá]pid|acelerad|a mil|desbocad|disparad|durisim)",
            r"se (me |le )?(acelera|dispara|sale)[^.]{0,15}coraz[oó]n",
            r"pulso[^.]{0,20}(muy r[aá]pid|acelerad|disparad)",
        ],
        "nota": "Vacío real: no había ningún concepto cardiaco. La taquicardia "
                "de aparición nueva en un postoperatorio es signo cardinal de "
                "tromboembolismo, choque hemorrágico y sepsis —y en el choque "
                "aparece ANTES que la disnea, que sí estaba cubierta como "
                "`critical`. Se marca `high` y no `critical` porque un paciente "
                "nervioso también dice «el corazón a mil»: escala a un humano "
                "sin declarar emergencia. **Pendiente de confirmar con el equipo "
                "clínico**, igual que `compromiso_vascular`.",
    },
    "dolor_brazo_izquierdo": {
        "severidad": "high",
        "terminos": [
            "me duele el brazo izquierdo", "dolor en el brazo izquierdo",
        ],
        "patrones": [
            # Algo que se corre hacia el brazo izquierdo, aunque la frase no diga
            # qué. Se exige la preposición de destino —al, hacia, hasta— porque
            # «se me pasa» también es «se me quita», y «se me pasa con la
            # pastilla» no va hacia ninguna parte.
            r"se (me |le )?(corre|pasa|extiende|riega|sube|baja)[^.]{0,15}\b(al|a|hacia|hasta|por)\b"
            r"[^.]{0,25}(brazo izquierdo|izquierdo,? el brazo)",
            r"(dolor|duele|molest\w*)[^.]{0,40}(brazo izquierdo|izquierdo,? el brazo)",
            r"brazo izquierdo[^.]{0,20}(duele|dolor|molest)",
        ],
        "nota": "Voz real, 13 de septiembre: el paciente dijo «me duele el pecho, "
                "¿cierto? y se me pasa al brazo izquierdo». AssemblyAI escribió "
                "«brazo» por «pecho» y el «¿cierto?» partió la frase en dos turnos: "
                "«Te cuento que me duele el brazo, ¿cierto?» y «Y se me pasa como, "
                "se me pasa como al lado izquierdo el brazo.». El patrón de "
                "irradiación de `dolor_toracico` no alcanzó a verlo en ninguno de "
                "los dos, y la emergencia más clara de la llamada salió sin alerta. "
                "Se marca `high`, no `critical`: sin nombrar el pecho es ambiguo "
                "—el brazo del suero también duele—, y ante ambigüedad se baja de "
                "nivel sin omitir. Con «pecho» dicho y bien transcrito sigue "
                "saltando `dolor_toracico` como emergencia. **Pendiente de "
                "confirmar con el equipo clínico.**",
    },
    "alteracion_mental": {
        "severidad": "high",
        "terminos": [
            "esta desorientad*", "muy desorientad*", "anda desorientad*",
            "dice incoherencias", "habla incoherencias", "dice cosas raras",
            "esta delirando", "delirando", "no me reconoce",
            "no reconoce a nadie", "no sabe donde esta", "no sabe ni donde esta",
            "esta como ido", "esta como perdid*", "muy somnolient*",
            "no hila las frases",
        ],
        "patrones": [
            r"(habla|dice)[^.]{0,15}(incoherenc|cosas raras|sin sentido|bobadas)",
            r"no (me |lo |la )?(reconoce|ubica)[^.]{0,18}(a nadie|d[oó]nde|qui[eé]n)",
        ],
        "nota": "Delirium postoperatorio: manifestación temprana de sepsis e "
                "hipoxia, y a menudo lo reporta un familiar, no el paciente. Se "
                "excluye a propósito el «estoy confundido» en primera persona: "
                "en una llamada de voz eso casi siempre significa «no entendí "
                "la pregunta», y convertirlo en escalamiento sería inflación de "
                "alarma —el defecto que la política de repregunta evita—.",
    },
    "preeclampsia": {
        "severidad": "high",
        "terminos": [
            "vision borrosa", "veo borroso", "veo lucecitas", "veo puntos negros",
            "dolor de cabeza muy fuerte", "dolor de cabeza intenso",
            "cabeza que me revienta", "hinchazon en la cara",
            "se me hincharon las manos", "se me hincho la cara",
        ],
        "patrones": [r"hinchaz[oó]n[^.]{0,20}(cara|manos|pies)"],
    },

    # -------------------------------------------------------------- moderados
    "empeoramiento": {
        "severidad": "moderate",
        "terminos": [
            "mas fuerte", "mas intens*", "mas duro", "mas maluco",
            "peor que ayer", "peor que antes", "peor que anoche",
            "esta peor", "he empeorado", "empeor*",
            "va en aumento", "cada dia mas", "cada vez mas",
            "en vez de mejorar", "en lugar de mejorar",
            "no ha mejorado", "no me ha mejorado", "no mejora",
            "no me baja", "no cede", "no se me quita", "sigue igual",
            "no ha bajado", "va aumentando",
        ],
        "patrones": [
            r"(dolor|molest\w+|intensidad|hinchaz[oó]n)\s+(me\s+|se\s+)?(ha\s+)?aument\w*",
            r"aument[oó]\s+(el |la )?(dolor|molest\w+|intensidad)",
            # Familia "no + verbo de mejoría", que en la práctica tiene muchas
            # conjugaciones: no baja / no me ha bajado / no ha mejorado / no cede
            # / no se me quita. Enumerarlas una por una era interminable.
            r"\bno\s+(se\s+me\s+|se\s+|me\s+|le\s+)?(ha\s+|han\s+)?(baj|mejor|ced|quit|calm|disminu)\w*",
        ],
        "nota": "El protocolo de colecistectomía pide reportar el dolor que "
                "aumenta después del tercer día. Un paciente lo dice así.",
    },
    "dolor_intenso": {
        "severidad": "moderate",
        "terminos": [
            "no aguanto el dolor", "no soporto el dolor", "dolor insoportable",
            "dolor horrible", "dolor terrible", "me esta matando el dolor",
            "un dolor tenaz", "dolor tenaz", "dolor berraco", "dolor verraco",
            "me duele durisimo", "me duele muchisimo", "me duele un resto",
            "me duele harto", "dolor muy fuerte", "dolor fuertisimo",
            "no me deja dormir el dolor", "me retuerzo del dolor",
            # Costa: "cipote" y "tronco de" son intensificadores de tamaño.
            "un cipote dolor", "cipote dolor", "un tronco de dolor",
            "me duele un mundo", "me duele a morir", "un dolor maluco",
            # La contraparte de la hipérbole excluida en `ideacion_suicida`: al
            # descartarla allí, «me quiero morir del dolor» se quedaba en `none`
            # —ni ideación ni dolor—, que es peor que cualquiera de las dos
            # lecturas. Aquí recupera su sentido literal: dolor intenso.
            "me quiero morir del dolor", "me muero del dolor",
            "estoy que me muero del dolor",
        ],
        "patrones": [
            r"dolor.{0,25}(insoportable|muy fuerte|intenso|tenaz|durisimo|berraco)",
            # La escala hablada: el reconocedor no siempre entrega dígitos, y
            # «un nueve sobre diez» quedaba fuera aunque es la forma en que se
            # responde a «del uno al diez, ¿cuánto le duele?».
            r"dolor[^.]{0,20}(9|10|nueve|diez)\s*(de|sobre|/|entre)\s*(10|diez)",
            r"\b(9|10|nueve|diez)\s*(de|sobre|/|entre)\s*(10|diez)[^.]{0,20}dolor",
            # «cipote» como intensificador, tolerando el seseo y que el
            # reconocedor lo trunque o lo parta. Medido: «un cipote dolor» llegó
            # como «un zipot de dolor», y la frase literal ya no casaba. Se pide
            # que «dolor» aparezca cerca para que el intensificador solo cuente
            # cuando califica al dolor.
            r"[csz]ipot\w*[^.]{0,12}dolor|dolor[^.]{0,12}[csz]ipot\w*",
        ],
        "nota": "Intensificadores colombianos: tenaz, berraco, durísimo, "
                "un resto, harto.",
    },
    "vomito_persistente": {
        "severidad": "moderate",
        "terminos": [
            "vomit*", "vomito", "estoy vomitando", "devuelvo todo",
            # Colombia: "trasbocar" = vomitar. En la costa, "trafocar".
            "trasboc*", "trafoc*", "arroje todo", "guacare*",
            "no puedo retener", "no puedo comer", "no me queda nada adentro",
            "tengo asco", "nauseas", "ganas de vomitar",
            "el estomago revuelto", "estomago revuelto",
        ],
        "nota": "'trasbocar' (interior) y 'trafocar' (costa) son los verbos más "
                "comunes para vomitar en Colombia.",
    },
    "distension_abdominal": {
        "severidad": "moderate",
        "terminos": [
            # Costa: "aventao" = con el abdomen distendido.
            "aventad*", "aventao", "estoy aventad*", "muy aventad*",
            "el estomago inflado", "estomago muy inflado", "la barriga inflada",
            "el estomago hinchado", "la barriga dura", "el estomago duro",
            "distension", "distendid*", "muy lleno de gases",
        ],
        "patrones": [
            # Los términos de arriba son frases contiguas —«la barriga dura»— y
            # cualquier palabra intercalada las rompe. Medido: «tengo la barriga
            # hinchada y dura», «me duele mucho la barriga y está dura» y «tengo
            # la barriga hinchada y no boto gases» daban `none`, y la tercera
            # está en `RESPONDIBLES` de la calibración: el RAG la contestaba y
            # ningún signo quedaba registrado. Es la misma forma que ya usa
            # `signos_tvp` para «pantorrilla … hinchada».
            r"(barriga|estomago|est[oó]mago|abdomen|vientre)[^.]{0,25}"
            r"(hinchad|inflad|dur[ao]\b|distendid|apretad|tensa)",
            r"(hinchad|inflad|distendid)[^.]{0,20}(barriga|estomago|est[oó]mago|abdomen)",
            # No expulsar gases es el otro miembro del par que nombra el
            # protocolo, y el paciente lo dice en negativo.
            # Tolerante a la perífrasis: «no boto», «no he podido botar», «no
            # puedo echar». Enumerar las conjugaciones una a una las deja fuera
            # justo cuando el paciente usa la forma compuesta, que es la común.
            r"no\s+(?:\w+\s+){0,3}(?:bot|ech|expuls|sac)\w*\s+(?:los\s+)?gases",
            r"no\s+(me\s+)?(salen|sale|pasan)\s+(los\s+)?gases",
        ],
        "nota": "El protocolo de colecistectomía nombra la distensión junto al "
                "dolor abdominal intenso. Se marca `moderate` a propósito: los "
                "gases solos son esperables en un postoperatorio, y la alarma "
                "del documento es la combinación —que sale sola por el máximo "
                "entre capas cuando además hay dolor.",
    },
    "diarrea": {
        "severidad": "moderate",
        "terminos": [
            "diarrea", "cagalera", "estoy suelt*", "suelto del estomago",
            "descompuesto del estomago", "no paro de ir al bano",
            "voy mucho al bano", "deposiciones liquidas",
        ],
        "nota": "'cagalera' es el término coloquial más frecuente, incluido en "
                "diccionarios de costeñismos.",
    },
    "ictericia": {
        "severidad": "moderate",
        "terminos": [
            "ictericia", "amarillo los ojos", "los ojos amarillos",
            "la piel amarilla", "me puse amarill*", "estoy amarill*",
            "orina oscura", "orina como coca cola", "orina color cafe",
            "la orina muy oscura", "heces blancas", "el popo blanco",
            "la materia fecal blanca", "deposiciones blancas",
        ],
        "patrones": [r"amarill\w*.{0,20}(piel|ojos|cuerpo)",
                     r"orina.{0,15}(oscura|caf[eé]\b|marr[oó]n)"],
    },
    "estado_general_malo": {
        "severidad": "moderate",
        "terminos": [
            # Confirmados como colombianismos de malestar general.
            "estoy maluc*", "me siento maluc*", "muy maluc*",
            "estoy descompuest*", "me siento descompuest*",
            "estoy achacad*", "me siento aporread*", "estoy amolad*",
            "sin fuerzas", "no tengo fuerzas", "no me puedo parar",
            "estoy hecho nada", "me siento pesim*", "muy decaid*",
            "no doy mas", "estoy muy debil",
            "alicaid*", "estoy alicaid*", "sin animo", "sin ganas de nada",
            # "cuerpo cortado" = malestar tipo febril. Lo encontró el bloque de
            # jerga sin cobertura: el juez lo calificaba `low` y quedaba sin red.
            "el cuerpo cortado", "cuerpo cortado", "todo el cuerpo me duele",
            "quebrantad* del cuerpo", "me siento resfriad*",
        ],
        "nota": "maluco / descompuesto: indispuesto, enfermo. Confirmado en "
                "diccionarios de colombianismos.",
    },
    "animo_depresivo": {
        "severidad": "moderate",
        "terminos": [
            "no paro de llorar", "no dejo de llorar", "lloro todo el dia",
            "lloro por todo", "me la paso llorando",
            "estoy muy triste", "muy deprimid*", "estoy deprimid*",
            "me siento deprimid*", "estoy desesperad*", "me siento desesperad*",
            "no le veo salida", "siento que no puedo mas", "no puedo con esto",
            "me siento inutil", "me siento un estorbo", "no quiero ver a nadie",
            "me siento muy sol*",
            # Posparto: el vínculo con el bebé es el eje del tamizaje.
            "no siento nada por el bebe", "siento que soy mala madre",
            "siento que no sirvo como madre",
        ],
        "nota": "Se marca `moderate` a propósito, no `high`. La tristeza "
                "posoperatoria y la melancolía posparto son frecuentes y "
                "esperables: convertir cada llanto en escalamiento sería "
                "inflación de alarma, el defecto que la política de repregunta "
                "existe para evitar. Queda registrada y visible en la consola, y "
                "sube sola por el máximo entre capas cuando aparece junto a algo "
                "más. Se excluyó «no quiero cargar al bebé»: tras una cesárea "
                "eso casi siempre es la herida, no el vínculo. Ver "
                "`ideacion_suicida` para el umbral crítico.",
    },
    "retencion": {
        "severidad": "moderate",
        "terminos": [
            # Colombia: "obrar" = defecar. Es el eufemismo habitual.
            "no he podido obrar", "no he obrado", "no puedo obrar",
            "no he hecho del cuerpo", "no he podido ir al bano",
            "no he expulsado gases", "no he echado gases",
            "estoy lleno de gases", "el estomago muy inflado",
            "no he podido orinar", "no puedo orinar", "no me sale la orina",
            "ardor al orinar", "me arde al orinar",
        ],
        "nota": "'obrar' = defecar en el habla clínica popular colombiana.",
    },
}
