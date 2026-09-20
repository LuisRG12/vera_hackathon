"""Construye el corpus clínico de Vera a partir de fuentes de dominio público.

    uv run scripts/corpus.py              # descarga lo que haga falta y escribe conocimiento/
    uv run scripts/corpus.py --xml <ruta> # con el XML de MedlinePlus ya descargado

**Por qué hay un script y no solo los archivos.** Los documentos de
`conocimiento/` se entregan versionados —el corpus es parte de la entrega, no un
anexo que haya que conseguir aparte—, pero un corpus que nadie puede reconstruir
es un corpus que hay que creer. Este script baja cada documento de su fuente y lo
vuelve a escribir: si el texto cambió, `git diff` lo dice.

**Qué entra y qué no.** Solo obra del gobierno federal de EE. UU. en español, que
es de dominio público y se puede redistribuir con atribución. Eso deja fuera dos
cosas que parecían las más útiles y no lo eran:

- La **enciclopedia médica de MedlinePlus** (`ency/`), que es justo donde viven
  las instrucciones de cuidado de la herida en casa: la escribe A.D.A.M. y está
  bajo derechos de autor. Un índice a nivel de fragmento contiene el texto de sus
  fuentes, así que indexarla sería redistribuirla.
- Las guías de la **OPS/OMS**, que son CC BY-NC-SA: el «NC» choca con el caso de
  negocio que el proyecto declara, y el «SA» se contagiaría al índice publicado.

Lo que queda son los **temas de salud** de MedlinePlus, que escribe la Biblioteca
Nacional de Medicina, y las páginas del **NIDDK**. El texto se copia tal cual:
este script no redacta una sola afirmación clínica, solo extrae y ordena.

La atribución de cada documento queda en `conocimiento/fuentes.json`, que este
script genera. El índice no admite un documento que no esté declarado ahí.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "conocimiento"
MANIFIESTO = DESTINO / "fuentes.json"

MEDLINEPLUS = ("MedlinePlus en español · Biblioteca Nacional de Medicina de EE. UU. (NIH)")
NIDDK = ("Instituto Nacional de la Diabetes y las Enfermedades Digestivas y "
         "Renales (NIDDK), NIH")
DOMINIO_PUBLICO = "dominio público (obra del gobierno federal de EE. UU.), con atribución"

# Los temas de MedlinePlus que cubren una llamada de seguimiento postoperatorio:
# la cirugía y su recuperación, la herida y sus signos de infección, y los
# síntomas por los que un paciente llama —fiebre, dolor, sangrado, náusea,
# estreñimiento— junto a las complicaciones que hay que reconocer a tiempo.
#
# Se nombran por su título exacto en español porque es la clave del XML. Si
# MedlinePlus renombra un tema, este script falla en voz alta en vez de escribir
# un corpus con un hueco.
TEMAS = [
    "Después de una cirugía",
    "Cirugía",
    "Anestesia",
    "Apendicitis",
    "Enfermedades de la vesícula biliar",
    "Heridas y lesiones",
    "Cicatriz",
    "Infecciones de la piel",
    "Fiebre",
    "Hemorragia",
    "Dolor",
    "Analgésicos",
    "Uso seguro de opioides",
    "Antibióticos",
    "Coágulos sanguíneos",
    "Trombosis venosa profunda",
    "Estreñimiento",
    "Náusea y vómitos",
    "Deshidratación",
]

# Páginas del NIDDK. Los temas de MedlinePlus son panorámicos y estas bajan al
# detalle que un paciente pregunta por teléfono —cuánto tarda en volver a su vida
# normal, qué le va a pasar al intestino sin vesícula—, que es donde el corpus
# tiene que responder o Vera se abstiene.
PAGINAS_NIDDK = [
    ("calculos_biliares_tratamiento.md", "Tratamiento para los cálculos biliares",
     "https://www.niddk.nih.gov/health-information/informacion-de-la-salud/"
     "enfermedades-digestivas/calculos-bilares/tratamiento"),
    ("calculos_biliares_sintomas.md", "Síntomas y causas de los cálculos biliares",
     "https://www.niddk.nih.gov/health-information/informacion-de-la-salud/"
     "enfermedades-digestivas/calculos-bilares/sintomas-causas"),
    ("apendicitis_tratamiento.md", "Tratamiento de la apendicitis",
     "https://www.niddk.nih.gov/health-information/informacion-de-la-salud/"
     "enfermedades-digestivas/apendicitis/tratamiento"),
    ("apendicitis_sintomas.md", "Síntomas y causas de la apendicitis",
     "https://www.niddk.nih.gov/health-information/informacion-de-la-salud/"
     "enfermedades-digestivas/apendicitis/sintomas-causas"),
]

# El plan de egreso del paciente de demostración. No se descarga de ninguna
# parte: es ficticio y se escribe a mano (ver `conocimiento/README.md`). Se
# declara aquí para que el manifiesto lo incluya con su licencia propia y el
# índice lo admita.
FICTICIOS = [
    ("plan_de_egreso_paciente_demo.md",
     "Plan de egreso — paciente de demostración",
     "documento ficticio, escrito para la demostración; no es material clínico real"),
]

_AGENTE = "Mozilla/5.0 (compatible; vera-corpus/1.0)"


def _bajar(url: str) -> bytes:
    pet = urllib.request.Request(url, headers={"User-Agent": _AGENTE})
    with urllib.request.urlopen(pet, timeout=120) as r:  # noqa: S310 — URLs fijas de NIH
        return r.read()


def _archivo(titulo: str) -> str:
    """Nombre de archivo a partir del título. Es lo que el paciente NO oye pero el
    equipo clínico sí ve en la cita, así que se mantiene legible."""
    base = unicodedata.normalize("NFKD", titulo.lower())
    base = "".join(c for c in base if not unicodedata.combining(c))
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return f"{base}.md"


def _juntar_listas(texto: str) -> str:
    """Quita la línea en blanco entre viñetas consecutivas.

    Es cosmético para quien lee el .md y no cambia el troceado, que junta las
    líneas de una sección de todas formas. Pero el corpus se entrega y se revisa
    con `git diff`, y una lista con el doble de líneas se revisa peor."""
    return re.sub(r"\n\n(?=- )", "\n", texto)


# ---------------------------------------------------------------- MedlinePlus

def xml_mas_reciente() -> str:
    """El XML de temas de salud se regenera de martes a sábado; el de hoy puede no
    existir todavía. Se prueban los últimos días hasta dar con uno."""
    for atras in range(0, 8):
        dia = date.today() - timedelta(days=atras)
        url = f"https://medlineplus.gov/xml/mplus_topics_{dia.isoformat()}.xml"
        pet = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _AGENTE})
        try:
            with urllib.request.urlopen(pet, timeout=60) as r:  # noqa: S310
                if r.status == 200:
                    return url
        except Exception:  # noqa: BLE001 — probar el día anterior es la respuesta
            continue
    raise RuntimeError("no hay XML de MedlinePlus en los últimos ocho días")


def _texto_medlineplus(resumen: str) -> str:
    """El resumen de un tema, en texto plano y sin perder sus listas.

    El resumen viene en HTML con enlaces a otros temas. Los enlaces se quitan
    —dentro de un fragmento no significan nada y el sintetizador los leería— pero
    su texto se conserva, porque es parte de la frase.
    """
    t = resumen
    t = re.sub(r"</p>\s*", "\n\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>\s*", "\n- ", t, flags=re.I)
    t = re.sub(r"</(ul|ol)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\xa0]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return _juntar_listas(t).strip()


def extraer_medlineplus(ruta_xml: Path) -> list[dict]:
    raiz = ET.parse(ruta_xml).getroot()
    temas = {t.get("title"): t for t in raiz.findall("health-topic")
             if t.get("language") == "Spanish"}
    escritos = []
    for titulo in TEMAS:
        tema = temas.get(titulo)
        if tema is None:
            raise KeyError(f"MedlinePlus ya no trae el tema «{titulo}»")
        resumen = tema.find("full-summary")
        cuerpo = _texto_medlineplus(resumen.text or "") if resumen is not None else ""
        if len(cuerpo) < 200:
            raise ValueError(f"el tema «{titulo}» vino casi vacío ({len(cuerpo)} caracteres)")

        # Los sinónimos del propio tema. Entran porque son el puente de registro
        # más barato que existe: MedlinePlus llama «Calentura» a la fiebre, que es
        # exactamente la palabra que dice un paciente colombiano por teléfono, y
        # sin ella la parte léxica de la recuperación no tiene dónde engancharla.
        sinonimos = [x.text for x in tema.findall("also-called") if x.text]
        partes = [f"# {titulo}", ""]
        if sinonimos:
            partes += [f"También se llama: {', '.join(sinonimos)}.", ""]
        partes.append(cuerpo)

        nombre = _archivo(titulo)
        (DESTINO / nombre).write_text("\n".join(partes) + "\n", encoding="utf-8")
        escritos.append({"archivo": nombre, "titulo": titulo, "fuente": MEDLINEPLUS,
                         "url": tema.get("url"), "licencia": DOMINIO_PUBLICO})
        print(f"  [ok] {nombre} ({len(cuerpo)} caracteres)")
    return escritos


# ----------------------------------------------------------------------- NIH

def _texto_nih(pagina: bytes) -> str:
    """El cuerpo de una página del NIDDK, con sus encabezados.

    Los encabezados se conservan como secciones markdown a propósito: `section`
    viaja hasta la cita, así que el equipo clínico ve «§¿Qué sucede después de que
    se extrae la vesícula biliar?» y encuentra el pasaje en la página real.
    """
    crudo = pagina.decode("utf-8", errors="replace")
    cuerpo = re.search(r"<main.*?</main>", crudo, re.S) or re.search(
        r"<article.*?</article>", crudo, re.S)
    t = cuerpo.group(0) if cuerpo else crudo
    t = re.sub(r"<(script|style|nav|aside|figure|form|header|footer)\b.*?</\1>", " ", t,
               flags=re.S | re.I)
    # La miga de pan y el selector de idioma son listas de navegación dentro de
    # <main>: se recortan por su marca, que es el título en <h1>.
    t = re.sub(r"<h([2-6])[^>]*>(.*?)</h\1>", r"\n\n## \2\n", t, flags=re.S | re.I)
    t = re.sub(r"<li[^>]*>", "\n- ", t, flags=re.I)
    t = re.sub(r"</(p|div|tr|h1)>|<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\xa0]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return _juntar_listas(t).strip()


def _podar_navegacion(texto: str) -> str:
    """Todo lo anterior a la primera sección es miga de pan y selector de idioma.

    Se corta por la primera sección y no por el título de la página, que era lo
    obvio y no funciona: el título aparece antes dentro de la miga de pan, así
    que la poda no recortaba nada y el corpus se llevaba el menú entero.
    """
    i = texto.find("\n## ")
    return texto[i:].strip() if i > 0 else texto


def extraer_nih() -> list[dict]:
    escritos = []
    for nombre, titulo, url in PAGINAS_NIDDK:
        cuerpo = _podar_navegacion(_texto_nih(_bajar(url)))
        if len(cuerpo) < 500:
            raise ValueError(f"«{titulo}» vino casi vacío ({len(cuerpo)} caracteres)")
        (DESTINO / nombre).write_text(f"# {titulo}\n\n{cuerpo}\n", encoding="utf-8")
        escritos.append({"archivo": nombre, "titulo": titulo, "fuente": NIDDK,
                         "url": url, "licencia": DOMINIO_PUBLICO})
        print(f"  [ok] {nombre} ({len(cuerpo)} caracteres)")
    return escritos


# ---------------------------------------------------------------- manifiesto

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser(description="Construye el corpus de dominio público.")
    ap.add_argument("--xml", help="XML de temas de salud de MedlinePlus ya descargado")
    args = ap.parse_args()

    DESTINO.mkdir(exist_ok=True)

    if args.xml:
        ruta_xml = Path(args.xml)
    else:
        url = xml_mas_reciente()
        print(f"[..] bajando {url}")
        ruta_xml = DESTINO / "_mplus_topics.xml"
        ruta_xml.write_bytes(_bajar(url))

    print("[..] MedlinePlus")
    documentos = extraer_medlineplus(ruta_xml)
    print("[..] NIDDK")
    documentos += extraer_nih()

    for nombre, titulo, licencia in FICTICIOS:
        if not (DESTINO / nombre).exists():
            print(f"  [!] falta {nombre}; se declara igual en el manifiesto")
        documentos.append({"archivo": nombre, "titulo": titulo,
                           "fuente": "escrito para este proyecto", "url": None,
                           "licencia": licencia})
        print(f"  [ok] {nombre} (ficticio)")

    MANIFIESTO.write_text(json.dumps(
        {"consultado": date.today().isoformat(), "documentos": documentos},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.xml and ruta_xml.exists():
        ruta_xml.unlink()  # 30 MB que no van al repo: lo que se entrega es el corpus

    print(f"\n{len(documentos)} documentos en {DESTINO}")
    print(f"manifiesto: {MANIFIESTO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
