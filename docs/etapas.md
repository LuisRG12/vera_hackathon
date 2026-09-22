# Etapas

Construcción por etapas. Cada etapa cierra con algo que corre y se puede probar
a mano; los ajustes que salgan de probarlo son commits propios.

Cierre del reto: **30 de septiembre, 10:00 AM (COT)**.

---

## Etapa 0 — Esqueleto y decisiones · *este commit*

Etapas definidas y arquitectura investigada. Sin código todavía.

- [x] Arquitectura decidida y documentada, con las razones
- [x] Voice Agent API evaluado y descartado, con la razón por escrito
- [x] Licencia, `.gitignore`, procedencia declarada

## Etapa 1 — El oído ✅

Sustituir el reconocimiento local por AssemblyAI. **Es la compuerta del
proyecto**: si el español en streaming no se comporta, nada de lo demás importa.

- [x] Conexión WebSocket a Universal-Streaming, PCM 16 kHz mono
- [x] La clave no sale del servidor: el audio pasa por aquí, así que no hace
      falta token temporal. Haría falta si el navegador conectara directo.
- [x] Parciales y `end_of_turn` llegando a la pantalla
- [x] Modelo activo y medido con voz real en español colombiano
- [x] Keyterms clínicos cargados y reconocidos en prueba
- [x] Fin de turno en `max_accuracy`: aguanta las pausas de un paciente sin
      partirle la frase
- [x] Cierre por inactividad, porque se factura por conexión abierta
- [x] **Prueba a mano:** cierre de turno en 0,6–0,9 s con voz real

## Etapa 2 — La cabeza ✅

Sustituir el modelo local por Claude vía el LLM Gateway.

> Estuvo bloqueada hasta habilitar pago por uso en la cuenta, porque el gateway
> no entra en el crédito gratuito; mientras tanto se adelantó la etapa 3.

- [x] Cliente del gateway, por HTTP directo, con Claude Haiku 4.5
- [x] Structured outputs en lugar de la decodificación con gramática
- [x] Juez de riesgo como segunda capa, combinado con las reglas
      (`evals/juez.py`: 17/17 escalados, 0 alarmas de más en 13 benignas)
- [x] Streaming de la respuesta, para que la voz arranque antes del final: la
      primera frase sale en ~1,5 s y el juez corre en paralelo
- [x] **Prueba a mano:** una conversación por texto que recorre todas las rutas
      —normal, alarma de las reglas, alarma que solo ve el juez, emergencia del
      código y un intento de manipulación—

## Etapa 3 — La red de seguridad ✅

Reconectar la capa determinista sobre el texto de AssemblyAI.

- [x] Léxico y motor de reglas corriendo sobre lo que entrega el reconocedor,
      parciales incluidos
- [x] Bloque de confusiones re-medido contra AssemblyAI, no heredado de Vosk
      (`evals/confusiones.py`: 128/128 frases conservan su decisión)
- [x] Escalamiento disparando antes de que el modelo opine: la alerta sale en
      el parcial, una mediana de 0,85 s antes de que cierre el turno
- [x] **Prueba a mano:** con voz real saltan emergencia, infección y fiebre, y
      la negación no alerta. Encontró tres fallos que la voz sintética no podía
      mostrar —muletillas, «yeyo» y el dolor de pecho oído como «brazo»—, ya
      cubiertos

## Etapa 4 — El conocimiento ✅

Hasta aquí Vera preguntaba, reconocía y escalaba, pero no podía afirmar nada
clínico. Esto es lo que le faltaba para cumplir la promesa del producto
—responder solo con lo que dicen los documentos del paciente, citando cuál—.

- [x] Corpus de guías clínicas de libre redistribución, seleccionado: diecinueve
      temas de salud de MedlinePlus y cuatro páginas del NIDDK, todo dominio
      público en español, más un plan de egreso **ficticio y declarado como tal**
      —ningún corpus público tiene los documentos de un paciente concreto—.
      `conocimiento/fuentes.json` declara fuente y licencia de cada documento, y
      el índice no admite uno que no esté ahí
- [x] Índice reconstruido y umbrales verificados: 133 fragmentos, y el umbral
      medido contra ESTE corpus (`evals/conocimiento.py`: 17 de 21 respondidas,
      0 fugas clínicas). Se entrega construido, para que el despliegue no gaste
      en arrancar lo que puede gastar una vez
- [x] Citas resolviendo al documento correcto: las deriva el código de lo que de
      verdad se le mostró al modelo, no de lo que el modelo declara
      (`evals/citas.py`: 18/18). Lo que no se le mostró, no es una cita
- [x] El corpus citable se acota al procedimiento del paciente: a una paciente de
      vesícula no se le responde con la guía de apendicitis
- [x] **Prueba a mano:** conversación completa con el modelo y el índice reales
      (`uv run scripts/ensayo.py`), que recorre una pregunta respondida por el
      plan del paciente, otra por una guía pública, una que el corpus no responde,
      un signo de alarma, un intento de manipulación y una emergencia

## Etapa 5 — La llamada completa ✅

> Se hizo antes que la etapa 4: el reto es de voz, y juntar el oído, la cabeza y la
> voz antes que el conocimiento saca a la luz los problemas de latencia y de
> turnos, que son los que condicionan todo lo demás. El conocimiento entra
> después sin rediseñar nada.

- [x] Bucle full-duplex con barge-in: el paciente la calla en cuanto toma la
      palabra, y lo que ya estaba sonando en el navegador se tira
- [x] Filtro de eco, para que el micrófono oyéndola a ella no sea un turno del
      paciente —ni una alarma—
- [x] Turn detection semántico: `max_accuracy` aguanta las pausas del paciente,
      y la interrupción se dispara con dos palabras suyas
- [x] Degradación definida: sin oído lo dice y cierra, sin voz sigue por texto y
      reintenta, sin modelo responde el respaldo escrito por el código
- [x] **Prueba a mano:** llamada entera por voz, con alarma, interrupción,
      silencio y parlantes

## Etapa 6 — Que el juez pueda tocarlo ✅

- [x] Docker para Hugging Face Spaces (UID 1000, modelos en build): el modelo de
      embeddings y el índice se hornean en la imagen, así que el Space arranca
      sin bajar dos gigas. Al Space va una instantánea sin historia
      (`scripts/desplegar.py`), porque Hugging Face rechaza cualquier binario en
      la historia de un push y el índice lo es
- [x] Desplegado y accesible por URL pública:
      **https://clrestrepo12-vera.hf.space**. Probado por texto desde afuera:
      cita el plan del paciente, se abstiene sin evidencia, sirve los documentos
- [x] **Prueba a mano:** una llamada por voz contra la URL pública. Funcionó, y
      mostró tres cosas que se corrigieron: Vera solo reaccionaba y la llamada
      terminaba por silencio; una frase guía disparaba una falsa alarma; y la
      primera respuesta tardó tres segundos
- [x] Agenda de seguimiento y cierre: Vera pregunta por el dolor, la herida, la
      fiebre y la alimentación sin que se lo pidan, y cuando ya cubrió todo
      pregunta si hay algo más; un «no, nada más» se despide y cuelga, nunca
      encima de una alarma (`evals/turno.py`: 58/58)
- [x] Página rediseñada para quien evalúa: el orbe sigue la voz real, las cifras
      son mediciones del proyecto y cada frase guía dice qué ruta toma
- [x] Camino de demo podado a una pantalla: quién es la paciente, cinco frases
      que recorren las rutas que importan —cada una dice qué ruta toma—, y la
      conversación con la fuente de cada respuesta enlazada al documento
- [x] Aviso visible: no es dispositivo médico, no reemplaza atención clínica, y
      la paciente y su plan de egreso son ficticios
- [x] Topes de uso para la URL pública: dos llamadas a la vez, treinta turnos y
      diez minutos por conversación (`evals/limites.py`: 11/11). Sin ellos,
      cualquiera que abra la URL gasta de tres cuentas de pago por uso
- [x] Interfaz en inglés para quien evalúa y no habla español. La conversación
      sigue en español —traducirla apagaría el léxico colombiano, que es el
      diferenciador—; lo que cambia es la pantalla, y la transcripción se traduce
      a demanda con una sola petición

## Etapa 7 — La entrega

- [ ] Caso de negocio: usuario concreto, TAM, modelo de ingreso
- [ ] Slides en PDF
- [ ] Video de 5 minutos
- [ ] Formulario de lablab completo
