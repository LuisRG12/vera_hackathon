# Arquitectura

Documento de decisión, escrito antes de empezar a construir. Recoge lo que se
investigó y por qué se eligió cada pieza.

## El bucle

```
navegador ──PCM 16k── servidor ──wss──> AssemblyAI Universal-Streaming
                          │                        │
                          │<────── parciales + end_of_turn
                          │
                          ├─> motor de seguridad determinista  (SIEMPRE, parciales incluidos)
                          │
                          ├─> recuperación híbrida (BM25 + embeddings) sobre los documentos del paciente
                          │
                          ├─> Claude vía LLM Gateway de AssemblyAI (structured outputs)
                          │
                          └─> Cartesia TTS ──audio──> navegador
```

El orden importa: **el motor de seguridad va antes del modelo, no después.**

## Decisiones

### 1. STT: Universal-Streaming multilingüe, no el Voice Agent API

El Voice Agent API empaqueta STT + LLM + TTS + turn detection + tool calling por
US$4,50/hora, soporta español y habla por teléfono vía Twilio. Es el producto
más vistoso del patrocinador y aun así se descarta.

Razón, de su propia documentación:

> «Tools are **LLM-driven, not forced**. There is **no hook that fires on every
> user utterance** regardless of the LLM's decision.»

> «The three artifacts appear once the session completes; the array is empty
> while it is still active.» — los transcripts no están en tiempo real.

En ese modelo, una comprobación de seguridad solo corre si el modelo decide
invocarla. Para un agente clínico eso invierte la garantía que importa: el
modelo pasa a ser el portero de su propia supervisión.

Conduciendo Universal-Streaming directamente, el motor determinista lee cada
palabra del paciente **antes e independientemente** del modelo, y sigue
funcionando si el modelo falla entero.

Detalles de conexión: `wss://streaming.assemblyai.com/v3/ws`, PCM 16 bits mono,
API key en el header `authorization` **sin** prefijo `Bearer`. El navegador
nunca ve la clave: el audio pasa por nuestro servidor, que es quien habla con
AssemblyAI. Un token temporal solo haría falta si el navegador conectara directo.

### 2. LLM: Claude por el LLM Gateway de AssemblyAI

`https://llm-gateway.assemblyai.com/v1/chat/completions`, compatible con el SDK
de OpenAI, con tool calling, structured outputs, streaming y prompt caching.

Se elige el gateway sobre la API de Anthropic directa porque enruta y factura
**Claude a través de AssemblyAI**: una sola credencial para todo el sistema y
quien reproduzca el proyecto necesita una clave, no dos.

**El gateway no está cubierto por el crédito gratuito.** Los US$50 de bienvenida
cubren transcripción —pregrabada y en vivo—, Voice Agent API, Speech
Understanding y Guardrails, pero no el gateway, que exige cuenta con medio de
pago y se factura por tokens desde la primera petición. En cuenta gratuita solo
responde un modelo pequeño, sin salida estructurada ni tool calling, que no
sirve para esta capa.

El reparto de costo queda conveniente: el audio, que es lo caro por hora, sigue
saliendo del crédito gratuito; solo los tokens se facturan.

La decodificación con gramática que se usaba con el modelo local se reemplaza
por **structured outputs**. El formato deja de ser una instrucción desobedecible
sin depender de un runtime local.

### 3. La capa determinista se conserva entera

El léxico clínico colombiano y el motor de reglas se traen del proyecto
original. Traducen cómo habla un paciente —«botando materia», «me dio un yeyo»,
«me fatigo al caminar»— a los conceptos que nombran los documentos, y clasifican
severidad sin invocar ningún modelo.

Lo único que se rehace es lo que dependía del reconocedor. El bloque de
**confusiones** eran defectos de Vosk; se re-midió contra AssemblyAI
(`evals/confusiones.py`) y la única que había no apareció nunca, así que se
retiró. Lo que sí apareció es otra clase de defecto: AssemblyAI oye bien pero
**formatea** —cifras en dígitos, marcas con guion, puntuación—, y dos alarmas se
perdían por cómo quedaban escritas, no por cómo se oyeron. Eso se absorbe en los
patrones, igual que ya se absorbían las tildes.

El motor lee lo que entrega el reconocedor **tal cual**, sin que ningún modelo
lo corrija, y lee también los parciales: la alerta sale mientras el paciente
todavía está hablando. Con voz real eso además salvó una alerta: AssemblyAI
reescribe el turno al cerrarlo, y en una prueba cambió «un yeyo», que había oído
bien, por «un jejum». La alerta ya había salido del parcial y no se retira.

Los **keyterms** de AssemblyAI son el reemplazo moderno de las confusiones donde
aplican: sesgar el reconocedor *antes* de transcribir es mejor que reparar el
texto después. Pero se añaden con evidencia, no por si acaso: «yeyo» entró
porque con voz real llegó dos veces como «jejum», y desde entonces llega bien;
«pus» no, porque su única confusión no se repitió.

Y la capa determinista tiene un límite que conviene decir en voz alta: unas
reglas no entienden todo lo que un paciente dice con muletillas, repeticiones y
frases partidas. Con voz real, un dolor de pecho que se corría al brazo quedó sin
alerta porque el reconocedor escribió «brazo» por «pecho» y la frase llegó en dos
turnos. Se cubrió ese caso, pero la respuesta de fondo es la de siempre: dos
capas, y el escalamiento es el máximo de las dos.

### 4. Turn detection: el semántico de AssemblyAI, con respaldo propio

El streaming trae fin de turno semántico —decide por el sentido de lo dicho, no
solo por silencio—, mejor que la heurística propia de «texto a media idea». Esa
heurística pasa a respaldo y se mide si sigue aportando.

### 5. TTS: Cartesia, en la nube

Se decidió Piper —local, CPU, ~60 MB, costo cero, ya calibrado en español— y la
etapa de la llamada lo cambió, por una razón que no se veía desde aquí: Piper no
tiene una voz colombiana, y el acento importa en una llamada clínica a un
paciente que acaba de salir de cirugía. AssemblyAI no vende síntesis por separado
—su voz solo existe dentro del Voice Agent API, que este proyecto descartó— y el
camino que documenta para agentes propios empareja su reconocedor con Cartesia,
donde sí hay voces colombianas nativas. Se oyeron las cuatro con el saludo y con
el mensaje de emergencia; se eligió Mariana, de tono calmado.

Lo que se paga: la síntesis deja de ser gratis y deja de ser offline. Lo que se
compra, además del acento: el primer trozo de audio llega en dos décimas de
segundo. Y lo que dice el código —el saludo, la emergencia, los respaldos— se
sintetiza al arrancar y queda guardado, así que suena aunque Cartesia se caiga,
que es justo cuando más falta hace. Ver la bitácora del 15 al 20 de septiembre.

### 6. Conocimiento: guías clínicas de libre redistribución

El índice se reconstruye sobre documentos publicables. Un índice a nivel de
fragmento contiene el texto de sus fuentes: publicar uno construido sobre PDFs
de terceros los redistribuiría bajo una licencia que no es nuestra para otorgar.

Un corpus curado y verificable demuestra mejor que uno grande y opaco: el juez
puede seguir cada cita hasta su fuente.

Lo que salió de aplicar esa regla (ver `conocimiento/README.md` y la bitácora
del 20 de septiembre): entran los **temas de salud de MedlinePlus** y las
páginas del **NIDDK**, que son obra federal de EE. UU. en español y por tanto de
dominio público; queda fuera la enciclopedia médica de MedlinePlus, que es de
A.D.A.M., y quedan fuera las guías de la OPS/OMS, cuyo CC BY-NC-SA se contagiaría
al índice publicado y choca con el caso de negocio de la etapa 7.

El corpus tiene **dos capas**, y no por comodidad. Vera promete responder con los
documentos *de ese paciente*, y el plan de egreso de un paciente concreto no
existe en ningún corpus público: en producción lo escribe el hospital que lo
operó. La demo lleva uno **ficticio y declarado como tal** en el manifiesto, y es
también lo que fija de qué cirugía es la llamada. Todo lo demás son guías de
referencia, citadas textualmente.

**Lo que no es de la cirugía del paciente no existe para su llamada.** No se
filtra al final: se excluye al abrir el índice, así que ni siquiera cuenta en las
estadísticas de BM25. Los protocolos postoperatorios comparten casi todo el
vocabulario, así que sin esto la guía de otra cirugía gana el top-k de cualquier
pregunta —a una paciente de vesícula se le respondió con la sección «Absceso» de
la guía de apendicitis—. Es seguridad clínica, no precisión.

**Y la cita la deriva el código**, no la declara el modelo: está medido que la
salida estructurada garantiza la forma de la cita y no su verdad. Lo que el
modelo diga se resuelve contra los fragmentos que de verdad se le mostraron en
ese turno; lo que no vio, no es una cita.

### 7. Despliegue: Hugging Face Spaces (SDK Docker)

16 GB de RAM en CPU Basic, suficiente para el modelo de embeddings sin
recalibrar los umbrales ya medidos. HTTPS y URL incluidos, despliegue con `git
push`. Restricciones asumidas: contenedor como UID 1000, disco efímero —los
modelos se bajan en build, no en runtime— y suspensión por inactividad.

## Riesgo nuevo: la llamada deja de ser offline

El diseño anterior corría entero sin red. Ahora depende de dos servicios. Hay
que definir y demostrar la degradación: qué hace Vera si la red se cae a mitad
de llamada. La respuesta no puede ser «se cuelga» en un contexto clínico.
