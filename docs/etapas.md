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

## Etapa 1 — El oído

Sustituir el reconocimiento local por AssemblyAI. **Es la compuerta del
proyecto**: si el español en streaming no se comporta, nada de lo demás importa.

- [ ] Conexión WebSocket a Universal-Streaming, PCM 16 kHz mono
- [ ] Token temporal para el navegador (la clave nunca sale del servidor)
- [ ] Parciales y `end_of_turn` llegando y visibles en consola
- [ ] Modelo multilingüe activo, midiendo español colombiano real
- [ ] Keyterms clínicos cargados
- [ ] **Prueba a mano:** hablarle y ver la transcripción correcta en vivo

## Etapa 2 — La cabeza

Sustituir el modelo local por Claude vía el LLM Gateway.

- [ ] Cliente del gateway, compatible con el SDK de OpenAI
- [ ] Structured outputs en lugar de la decodificación con gramática
- [ ] Streaming de la respuesta, para que la voz arranque antes del final
- [ ] **Prueba a mano:** un turno completo de conversación, por texto

## Etapa 3 — La red de seguridad

Reconectar la capa determinista sobre el texto de AssemblyAI.

- [ ] Léxico y motor de reglas corriendo sobre el transcript crudo
- [ ] Bloque de confusiones re-medido contra AssemblyAI, no heredado de Vosk
- [ ] Escalamiento disparando antes de que el modelo opine
- [ ] **Prueba a mano:** decir un signo de alarma y ver la escalada

## Etapa 4 — El conocimiento

- [ ] Corpus de guías clínicas de libre redistribución, seleccionado
- [ ] Índice reconstruido y umbrales verificados
- [ ] Citas resolviendo al documento correcto
- [ ] **Prueba a mano:** preguntar algo del documento y verificar la cita

## Etapa 5 — La llamada completa

- [ ] Bucle full-duplex con barge-in
- [ ] Turn detection semántico afinado
- [ ] Degradación definida si se cae la red a mitad de llamada
- [ ] **Prueba a mano:** una llamada entera de principio a fin

## Etapa 6 — Que el juez pueda tocarlo

- [ ] Docker para Hugging Face Spaces (UID 1000, modelos en build)
- [ ] Desplegado y accesible por URL pública
- [ ] Camino de demo podado a tres pantallas o menos
- [ ] Aviso visible: no es dispositivo médico, no reemplaza atención clínica

## Etapa 7 — La entrega

- [ ] Caso de negocio: usuario concreto, TAM, modelo de ingreso
- [ ] Slides en PDF
- [ ] Video de 5 minutos
- [ ] Formulario de lablab completo
