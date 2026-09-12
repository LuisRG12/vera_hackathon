# Bitácora

Lo que se fue haciendo y decidiendo, con fecha.

---

## 12 de septiembre — el oído

**Elección del reconocedor.** Se conecta AssemblyAI Universal-Streaming en lugar
del modelo local que traía el proyecto. La documentación se contradice sobre la
dirección del servicio: la guía de inicio da una y la página de multilingüe otra.
Se probaron ambas contra la API real y solo responde la primera; la de la página
multilingüe está obsoleta.

El modelo por defecto del servicio ya es `universal-3-5-pro`. Se deja declarado
igual en la configuración: un default que cambie sin avisar no debería cambiar
cómo oye el agente.

**Modelo: se decide por precisión.** Hay dos opciones, `universal-3-5-pro` a
US$0,45/hora y `universal-streaming-multilingual` a US$0,15/hora. Con el crédito
gratuito son 111 horas contra 333: las dos sobran de largo para el reto, así que
el precio no es criterio. Se queda `universal-3-5-pro`, que es el más preciso, y
se compara con voz real durante las pruebas.

**Vocabulario clínico por adelantado.** El servicio permite pasarle términos que
debe esperar antes de transcribir. Se cargan 52 —procedimientos, signos,
medicamentos y vocabulario del sistema de salud colombiano—. Es mejor que la
estrategia anterior, que corregía el texto después de transcribirlo: no hay que
adivinar qué deformó el reconocedor, se le dice qué va a oír.

El habla corriente del paciente («botando materia», «me dio un yeyo») no va aquí.
Eso no es vocabulario raro sino léxico clínico colombiano, y se resuelve en la
capa determinista de la etapa 3, donde puede revisarlo alguien con criterio
clínico.

**Primer defecto: la llamada se caía al segundo.** Al probar con micrófono real,
la conexión se cerraba apenas se empezaba a hablar.

El servicio acepta trozos de audio de entre 50 y 1000 milisegundos y cierra la
conexión si recibe algo fuera de ese rango. El navegador entrega bloques de 128
muestras, que a 16 kHz son 8 milisegundos. Cada bloque violaba el mínimo.

Se corrigió acumulando el audio antes de mandarlo, en trozos de 100 ms. La
acumulación se puso en el cliente del servicio y no en el navegador: la regla es
del servicio, así que la conoce quien le habla, y cualquier otro origen de audio
queda cubierto sin repetirla.

**Segundo defecto, el que escondía al primero.** El error llegaba explicado por
el propio servicio y el código lo descartaba en silencio. La llamada moría sin
que nadie supiera por qué. Ahora los errores y los cierres anómalos se recogen y
llegan hasta la pantalla de quien está probando, que no está mirando la consola.

**Nota de entorno.** El intérprete del entorno virtual corre sin problema en esta
máquina, al contrario de lo que decía una nota vieja del proyecto.
