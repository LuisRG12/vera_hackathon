# Bitácora

Lo que se fue decidiendo y midiendo, con fecha. Lo que no se pudo confirmar
queda escrito como lo que es.

---

## 12 de septiembre

**El endpoint de streaming: la documentación se contradice y gana la conexión.**

La guía de inicio de streaming da `wss://streaming.assemblyai.com/v3/ws`. La
página de transcripción multilingüe muestra en su ejemplo
`wss://api.assemblyai.com/v2/realtime`. No pueden ser las dos.

Se probaron las cuatro combinaciones contra la API real:

| Prueba | Resultado |
|---|---|
| v3 + `universal-3-5-pro` + `language_codes=es` + `language_detection=true` | `Begin`, mode `balanced` |
| v3 + `universal-streaming-multilingual` | `Begin`, mode `null` |
| v3 sin parámetros de modelo | `Begin` — el modelo por defecto **ya es** `universal-3-5-pro` |
| v2 realtime, el de la doc multilingüe | **HTTP 404** |

Manda **v3**. La URL de la página multilingüe está obsoleta. La versión de API
que reporta el servidor es `2025-05-12`.

Consecuencia para el código: se conecta a v3 y no hace falta pedir
`universal-3-5-pro` explícitamente, aunque se deja explícito igual — un default
que cambie sin avisar no debería cambiar el comportamiento del agente.

**Queda por medir:** `universal-3-5-pro` cuesta US$0,45/hora y
`universal-streaming-multilingual` US$0,15/hora. Con los US$50 gratis son 111 h
contra 333 h: las dos sobran para el reto, así que la decisión se toma por
precisión en español colombiano, no por precio. Se comparan con audio real en
la etapa 1.

**Entorno.** El `python.exe` del venv de uv corre sin problema en esta máquina,
al contrario de lo que decía una nota vieja. `uv 0.12.5` disponible.
