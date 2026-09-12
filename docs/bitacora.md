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

**Primera prueba con voz real.** Transcribe español colombiano correctamente y
reconoce los términos clínicos cargados. Dos cosas por resolver: el corte de
turno parte frases a la mitad de forma inconsistente, y la conexión se factura
por tiempo abierto, así que no puede quedarse viva cuando nadie habla.
