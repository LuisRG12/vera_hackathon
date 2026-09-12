# Bitácora

Lo que se fue haciendo y decidiendo, con fecha.

---

## 12 de septiembre — el oído

**Elección del reconocedor.** Se conecta AssemblyAI Universal-Streaming al
proyecto. La documentación se contradice sobre la dirección del servicio: la
guía de inicio da una y la página de multilingüe otra. Se probaron ambas contra
la API real y solo responde la primera; la de la página multilingüe está
obsoleta. Se deja declarado el modelo `universal-3-5-pro` por defecto.

**Modelo.** Se define `universal-3-5-pro` por ser el más preciso comparado con
`universal-streaming-multilingual`, y se compara con voz real durante las
pruebas.

**Vocabulario clínico por adelantado.** Se cargan 52 términos —procedimientos,
signos, medicamentos y vocabulario del sistema de salud colombiano— para que el
reconocedor los espere antes de transcribir, en vez de corregir el texto después.

El habla corriente del paciente («botando materia», «me dio un yeyo») no va ahí:
no es vocabulario raro sino léxico clínico colombiano, y se resuelve en la capa
determinista de la etapa 3, donde puede revisarlo alguien con criterio clínico.

**Tamaño del trozo de audio.** El servicio acepta entre 50 y 1000 milisegundos y
cierra la conexión fuera de ese rango; el navegador entrega bloques de 8. Se
acumula el audio en trozos de 100 ms dentro del cliente del servicio, no en el
navegador: la regla es del servicio, así que la conoce quien le habla.

**Corte de turno.** El modo por defecto del servicio comprueba el fin de turno a
los 128 ms de silencio y partía las frases en pedazos. Un paciente recién
operado pausa mucho más que eso — es el mismo hallazgo que ya se había medido
con el reconocedor anterior, donde el umbral tuvo que subir al pasar de audio
sintético a micrófono real. Se sube a `max_accuracy`: 512 ms para la
comprobación y 2560 ms para el corte forzado.

**Cierre por inactividad.** El servicio factura por tiempo de conexión abierta,
no por audio enviado, así que la llamada se cierra sola tras 45 segundos sin
voz. Habrá que revisar ese número cuando exista el diálogo, porque entonces el
silencio del paciente mientras habla el agente es parte normal de la llamada.

**Etapa cerrada.** Con voz real, el turno cierra entre 0,6 y 0,9 segundos
después de dejar de hablar, y aguanta entera una frase con muletilla y
autocorrección a la mitad — «me operaron de apendicitis, cierto, y de la herida
me está saliendo un, saliendo un líquido amarillo»—. Los términos clínicos
cargados se reconocen.
