# Caso de negocio

Lo que sigue es la proyección que acompaña la entrega del hackathon. Las cifras
de mercado son órdenes de magnitud con su fuente y su supuesto a la vista; las
de costo están medidas contra el despliegue real.

## El problema

Después de una cirugía, el paciente se va a la casa con un plan de egreso en
papel. Las complicaciones que importan —una herida que se infecta, fiebre, un
dolor en el pecho— aparecen justo en esos días, lejos de la clínica. El
seguimiento que lo detecta es una enfermera llamando por teléfono, y una
enfermera no alcanza a llamar a todos: cada llamada, con su registro, es tiempo
que sale de la atención presencial.

## Quién lo usa y quién lo paga

**Lo paga la clínica o el hospital que opera (la IPS).** Es quien tiene al
paciente, quien escribe su plan de egreso y quien recibe la alerta. Lo usan dos
personas:

- **El paciente**, que contesta una llamada en su español de todos los días y
  recibe respuestas de su propio plan, citadas, en vez de buscarlas en internet.
- **La enfermera de seguimiento**, que deja de hacer las llamadas de rutina y
  recibe solo las que necesitan a una persona: cada alarma llega con lo que dijo
  el paciente y por qué se escaló.

## Qué le vende a la clínica

No se promete bajar los reingresos: la evidencia sobre llamadas de seguimiento y
reingresos es mixta —una revisión de 19 estudios la encontró débil e
inconsistente (Bahr et al., 2014)—. Lo que Vera sí cambia se puede medir:

- **Cobertura.** Todos los pacientes reciben su seguimiento, no los que la
  enfermera alcanzó a llamar.
- **Detección temprana.** Un signo de alarma escala en el momento en que el
  paciente lo dice, con reglas deterministas que no dependen del modelo.
- **Tiempo de enfermería.** Supuesto: tres llamadas por paciente de unos diez
  minutos con su registro son media hora de enfermería por paciente. Una clínica
  que opera 500 pacientes al mes recupera del orden de 250 horas mensuales.
- **Trazabilidad.** Cada respuesta queda con el documento que la respalda y cada
  escalamiento con su motivo.

## Cómo cobra

**Por paciente seguido**: una tarifa fija por cada paciente dado de alta con su
seguimiento completo, de referencia **USD 4**, con tres llamadas en las dos
semanas siguientes a la cirugía. Se compara directamente con lo que le cuesta a
la clínica la media hora de enfermería que reemplaza.

**Costo medido.** Una llamada completa de seguimiento contra el despliegue real
consumió 11.344 tokens de entrada y 737 de salida en el modelo (Claude Haiku 4.5
por el LLM Gateway de AssemblyAI, ≈ USD 0,015), 784 caracteres de voz sintetizada
(Cartesia, ≈ USD 0,04) y unos cinco minutos de reconocimiento de voz
(AssemblyAI Universal-Streaming, USD 0,15 la hora, ≈ USD 0,013). Unos **USD 0,07
por llamada**, USD 0,21 por paciente, más la telefonía cuando las llamadas
salgan a un teléfono real. Antes de la telefonía, el margen bruto queda por encima del 90 %.

## Tamaño del mercado

| | Supuesto | Pacientes al año | Ingreso anual a USD 4 |
|---|---|---|---|
| **TAM — América Latina hispanohablante** | 430,7 M de habitantes (Banco Mundial, 2025) × 5.000 cirugías por 100.000 | ≈ 21,5 M | ≈ USD 86 M |
| **SAM — Colombia** | 53,4 M de habitantes × 5.000 cirugías por 100.000 | ≈ 2,7 M | ≈ USD 10,7 M |
| **SOM — primer año** | Tres clínicas de tamaño medio, 300 pacientes operados al mes cada una | 10.800 | ≈ USD 43.000 |

Las 5.000 cirugías por cada 100.000 habitantes son el mínimo que la Comisión
Lancet de Cirugía Global fija para un sistema quirúrgico que funcione (Meara et
al., 2015); se usan como piso conservador. Colombia reporta muy por encima: el
indicador del Banco Mundial le da 27.385 procedimientos por cada 100.000
habitantes en 2015, aunque cuenta procedimientos menores que no necesitan este
seguimiento.

## Por qué ahora, y por qué en español

Los agentes de voz ya existen en inglés. Lo que no existe es uno que entienda
cómo habla un paciente colombiano por teléfono —«calentura», «maluca», «me dio
un yeyo»— y que no deje la seguridad en manos del modelo. Esas dos cosas son el
núcleo de Vera y no se compran hechas: el léxico clínico colombiano, sus arneses
y la calibración contra un corpus en español son el trabajo que está en este
repositorio.

## Lo que falta para venderlo

- **Telefonía.** Hoy la llamada es desde el navegador; en producción sale a un
  número de teléfono.
- **Integración** con la historia clínica de la IPS (HL7 FHIR) para recibir el
  plan de egreso de cada paciente y devolver las alertas.
- **Un tablero para la enfermera**, con las alertas y la transcripción de cada
  llamada.
- **Un piloto con validación clínica**: el léxico y los umbrales, revisados por
  el equipo de una clínica con sus propios pacientes.
- **Cumplimiento.** Los datos de salud son datos sensibles bajo la Ley 1581 de
  2012, y la prestación a distancia se enmarca en la reglamentación de telesalud
  (Resolución 2654 de 2019). Vera se posiciona como herramienta de seguimiento y
  comunicación: no diagnostica, no prescribe y escala siempre a una persona.

## Fuentes

- Banco Mundial, población total 2025 (SP.POP.TOTL) y procedimientos quirúrgicos
  por 100.000 habitantes (SH.SGR.PROC.P5): https://data.worldbank.org
- Meara JG et al. *Global Surgery 2030*. The Lancet, 2015.
- Bahr SJ et al. *Integrated Literature Review of Postdischarge Telephone Calls*.
  Western Journal of Nursing Research, 2014. https://doi.org/10.1177/0193945913491016
- Precios: AssemblyAI (https://www.assemblyai.com/pricing y la tabla de modelos
  del LLM Gateway) y el plan Pro de Cartesia (100.000 créditos por USD 5, un
  crédito por carácter).
