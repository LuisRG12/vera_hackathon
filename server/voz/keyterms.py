"""Vocabulario clínico que se le adelanta al reconocedor.

**Por qué antes y no después.** La versión anterior corregía el texto ya
transcrito: tenía una lista de palabras que el modelo local ponía en boca del
paciente sin que las hubiera dicho. AssemblyAI permite lo contrario —sesgar el
reconocedor *antes* de transcribir— y eso es estrictamente mejor: no hay que
adivinar qué deformó, se le dice qué esperar.

Van solo términos que un modelo general no tiene por qué conocer: nombres de
procedimientos y vocabulario clínico. **No** van palabras comunes; la doc de
AssemblyAI advierte que para esas el modelo ya es competente y meterlas solo
gasta el cupo, que es de 100 términos de 50 caracteres.

Lo que el paciente dice en habla corriente —«botando materia», «me dio un yeyo»—
NO va aquí: eso no es vocabulario raro, es el léxico clínico colombiano y se
resuelve en la capa determinista (etapa 3), que es donde puede revisarlo alguien
con criterio clínico.
"""

CONTEXTO_CLINICO = (
    "Llamada de seguimiento a un paciente recién operado, en español de Colombia. "
    "El paciente describe cómo sigue la herida quirúrgica, el dolor y la fiebre."
)

KEYTERMS: list[str] = [
    # Procedimientos
    "apendicectomía", "colecistectomía", "colectomía", "herniorrafia",
    "reemplazo articular", "artroplastia", "cesárea", "laparoscopia",
    "laparotomía", "histerectomía", "prostatectomía",
    # Herida y curación
    "dehiscencia", "seroma", "hematoma", "eritema", "induración",
    "exudado", "purulento", "secreción", "sutura", "grapas", "drenaje",
    "penrose", "apósito", "curación",
    # Signos y síntomas
    "disnea", "taquicardia", "hipotensión", "ictericia", "escalofrío",
    "distensión abdominal", "náuseas", "emesis", "melena", "hematuria",
    "trombosis venosa profunda", "tromboembolismo",
    # Medicación
    "acetaminofén", "dipirona", "ibuprofeno", "tramadol", "enoxaparina",
    "cefalexina", "ciprofloxacina", "metronidazol", "omeprazol",
    # Sistema de salud colombiano
    "EPS", "IPS", "urgencias", "triage", "remisión", "incapacidad",
]
