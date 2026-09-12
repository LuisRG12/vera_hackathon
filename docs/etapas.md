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

## Etapa 2 — La cabeza

Sustituir el modelo local por Claude vía el LLM Gateway.

> Bloqueada hasta habilitar pago por uso en la cuenta: el gateway no entra en el
> crédito gratuito. La etapa 3 no depende de esto y se adelanta.

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
