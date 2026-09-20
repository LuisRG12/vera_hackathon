# El corpus de Vera

Los documentos de esta carpeta son lo único que Vera puede afirmar. Fuera de
aquí no hay conocimiento clínico: las instrucciones del turno le prohíben al
modelo hablar del tratamiento, la herida o la medicación sin un fragmento
delante, y la cita se verifica con código contra lo que de verdad se recuperó.

El corpus se entrega versionado, no como un anexo que haya que conseguir aparte,
y se puede reconstruir entero con `uv run scripts/corpus.py`: ese script baja
cada documento de su fuente y lo vuelve a escribir, así que si una fuente cambió,
`git diff` lo dice. `fuentes.json` —que el mismo script genera— declara de dónde
sale cada archivo y bajo qué licencia. **El índice no admite un documento que no
esté declarado ahí.**

## Qué se puede redistribuir y qué no

Un índice a nivel de fragmento contiene el texto de sus fuentes. Publicar uno
construido sobre documentos ajenos los redistribuiría bajo una licencia que no es
nuestra para otorgar, así que la regla es simple: **solo entra lo que se puede
volver a publicar**.

Eso dejó fuera dos fuentes que parecían las más útiles:

- La **enciclopedia médica de MedlinePlus** (`medlineplus.gov/spanish/ency/`),
  que es exactamente donde viven las instrucciones de cuidado de la herida en
  casa —lo que más pregunta un paciente recién operado—. La escribe A.D.A.M. y
  está bajo derechos de autor. La NLM permite enlazarla, no incorporarla.
- Las guías de la **OPS/OMS**, que son CC BY-NC-SA. El «NC» choca con el caso de
  negocio que este mismo proyecto declara en la etapa 7, y el «SA» se contagiaría
  al índice publicado.

Lo que sí entra es obra del gobierno federal de EE. UU. en español, que es de
dominio público y solo pide atribución: los **temas de salud de MedlinePlus**,
que escribe la Biblioteca Nacional de Medicina, y las páginas del **NIDDK**. El
texto se copia tal cual. Ni el script ni nosotros redactamos una sola afirmación
clínica dentro de estos documentos.

De cada tema de MedlinePlus se conserva además su línea de sinónimos —«También se
llama: Calentura, Temperatura alta»— y no por completitud: «calentura» es la
palabra que dice un paciente colombiano por teléfono, y sin ella la parte léxica
de la recuperación no tiene dónde engancharla.

## El documento que ninguna fuente pública puede dar

`plan_de_egreso_paciente_demo.md` es **ficticio** y está escrito para la
demostración. Así se declara en `fuentes.json`, y por eso su licencia es la única
que no dice «dominio público».

No es un atajo, es la forma del producto. Vera promete responder con los
documentos **de ese paciente**, y el plan de egreso de un paciente concreto no
existe en ningún corpus público: en producción lo escribe el hospital que lo
operó. Sin un documento así, la pregunta más común de una llamada de seguimiento
—«¿cuándo me puedo bañar?»— no la responde nadie, porque las guías públicas
remiten justamente a lo que le dijo su cirujano.

Su contenido se mantiene consistente con las fuentes de dominio público de esta
misma carpeta donde ellas dicen algo —el tiempo de recuperación y el cambio en
las deposiciones vienen del NIDDK—, y lo demás es lo que un cirujano
individualiza para su paciente: fechas, plazos y la cita de control. Es material
de demostración y no es consejo clínico para nadie.

## Cómo se citan

La cita viaja con el nombre del archivo y la sección, que es lo que permite
seguirla hasta la fuente. Por eso los encabezados de las páginas del NIDDK se
conservan como secciones markdown: el equipo clínico ve «§¿Qué sucede después de
que se extrae la vesícula biliar?» y encuentra el pasaje en la página real.
