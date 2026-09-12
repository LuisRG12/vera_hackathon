# Arquitectura

Documento de decisión, escrito antes de empezar a construir. Recoge lo que se
investigó y por qué se eligió cada pieza.

## El bucle

```
navegador ──PCM 16k── servidor ──wss──> AssemblyAI Universal-Streaming
                          │                        │
                          │<────── parciales + end_of_turn
                          │
                          ├─> motor de seguridad determinista  (SIEMPRE, sobre el texto crudo)
                          │
                          ├─> recuperación híbrida (BM25 + embeddings) sobre guías clínicas
                          │
                          ├─> Claude vía LLM Gateway de AssemblyAI (structured outputs)
                          │
                          └─> Piper TTS ──audio──> navegador
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
nunca ve la clave: se autentica con un token temporal.

### 2. LLM: Claude por el LLM Gateway de AssemblyAI

`https://llm-gateway.assemblyai.com/v1/chat/completions`, compatible con el SDK
de OpenAI, con tool calling, structured outputs, streaming y prompt caching.

Se elige el gateway sobre la API de Anthropic directa porque enruta y factura
**Claude a través de AssemblyAI**: una sola credencial para todo el sistema, los
créditos del reto aplican a ambas capas, y quien reproduzca el proyecto necesita
una clave, no dos.

La decodificación con gramática que se usaba con el modelo local se reemplaza
por **structured outputs**. El formato deja de ser una instrucción desobedecible
sin depender de un runtime local.

### 3. La capa determinista se conserva entera

El léxico clínico colombiano y el motor de reglas no se tocan. Traducen cómo
habla un paciente —«botando materia», «me dio un yeyo», «me fatigo al caminar»—
a los conceptos que nombran los documentos, y clasifican severidad sin invocar
ningún modelo.

Lo único que se rehace es el bloque de **confusiones del reconocedor**: eran
defectos de Vosk. AssemblyAI tendrá otros, y se re-miden contra transcripciones
reales en vez de heredarlos a ciegas.

Su reemplazo moderno, donde aplica, son los **keyterms** de AssemblyAI: sesgar
el reconocedor con vocabulario clínico *antes* de transcribir es mejor que
reparar el texto después.

### 4. Turn detection: el semántico de AssemblyAI, con respaldo propio

El streaming trae fin de turno semántico —decide por el sentido de lo dicho, no
solo por silencio—, mejor que la heurística propia de «texto a media idea». Esa
heurística pasa a respaldo y se mide si sigue aportando.

### 5. TTS: Piper, local

CPU, ~60 MB, costo cero, ya calibrado en español. No hay razón para cambiarlo.

### 6. Conocimiento: guías clínicas de libre redistribución

El índice se reconstruye sobre documentos publicables. Un índice a nivel de
fragmento contiene el texto de sus fuentes: publicar uno construido sobre PDFs
de terceros los redistribuiría bajo una licencia que no es nuestra para otorgar.

Un corpus curado y verificable demuestra mejor que uno grande y opaco: el juez
puede seguir cada cita hasta su fuente.

### 7. Despliegue: Hugging Face Spaces (SDK Docker)

16 GB de RAM en CPU Basic, suficiente para el modelo de embeddings sin
recalibrar los umbrales ya medidos. HTTPS y URL incluidos, despliegue con `git
push`. Restricciones asumidas: contenedor como UID 1000, disco efímero —los
modelos se bajan en build, no en runtime— y suspensión por inactividad.

## Riesgo nuevo: la llamada deja de ser offline

El diseño anterior corría entero sin red. Ahora depende de dos servicios. Hay
que definir y demostrar la degradación: qué hace Vera si la red se cae a mitad
de llamada. La respuesta no puede ser «se cuelga» en un contexto clínico.
