#!/usr/bin/env python3
"""Consulta una causa en el portal de procesos judiciales de la Función Judicial
del Ecuador y devuelve el texto de sus actuaciones.

Uso:
    python consultar_causa.py <numero_de_expediente_21_digitos> [--salida archivo.txt] [--visible]

Usa el Chromium ya instalado en el sistema (no descarga navegadores). La ruta se
detecta automáticamente; puede forzarse con la variable de entorno CHROMIUM_PATH.
"""

import argparse
import glob
import os
import re
import shutil
import sys

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

URL_BUSQUEDA = "https://procesosjudiciales.funcionjudicial.gob.ec/busqueda-filtros"
TIMEOUT_MS = 30_000

NOMBRES_EJECUTABLES = [
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
]
RUTAS_CONOCIDAS = [
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/lib/chromium/chromium",
    "/snap/bin/chromium",
    "/usr/bin/google-chrome",
    "/opt/pw-browsers/chromium",
]


def detectar_chromium():
    """Devuelve la ruta del ejecutable de Chromium instalado en el sistema."""
    ruta = os.environ.get("CHROMIUM_PATH")
    if ruta and os.access(ruta, os.X_OK):
        return ruta

    for nombre in NOMBRES_EJECUTABLES:
        ruta = shutil.which(nombre)
        if ruta:
            return ruta

    candidatos = RUTAS_CONOCIDAS + sorted(
        glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"), reverse=True
    )
    for ruta in candidatos:
        if os.path.isfile(ruta) and os.access(ruta, os.X_OK):
            return ruta

    raise FileNotFoundError(
        "No se encontró Chromium en el sistema. Instálalo o define CHROMIUM_PATH."
    )


def normalizar_numero(numero):
    digitos = re.sub(r"\D", "", numero)
    if len(digitos) != 21:
        raise ValueError(
            f"El número de expediente debe tener 21 dígitos (se recibieron {len(digitos)})."
        )
    return digitos


def esperar_carga(page):
    try:
        page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass


def primer_visible(*localizadores):
    """Devuelve el primer localizador que tenga al menos un elemento visible."""
    for loc in localizadores:
        try:
            loc.first.wait_for(state="visible", timeout=3_000)
            return loc.first
        except PlaywrightTimeoutError:
            continue
    return None


def buscar_causa(page, numero):
    page.goto(URL_BUSQUEDA, timeout=TIMEOUT_MS)
    esperar_carga(page)

    patron_numero = re.compile(r"n[uú]mero.*(causa|proceso|expediente)", re.I)
    campo = primer_visible(
        page.get_by_label(patron_numero),
        page.get_by_placeholder(patron_numero),
        page.locator("input[formcontrolname*='numero' i]"),
        page.locator("input[id*='numero' i], input[name*='numero' i]"),
        page.locator("input[type='text']"),
    )
    if campo is None:
        raise RuntimeError("No se encontró el campo del número de causa en el formulario.")
    campo.fill(numero)

    boton = primer_visible(
        page.get_by_role("button", name=re.compile(r"buscar", re.I)),
        page.locator("button[type='submit']"),
    )
    if boton is None:
        campo.press("Enter")
    else:
        boton.click()
    esperar_carga(page)


def abrir_detalle(page, numero):
    """Abre el detalle (movimientos) de la causa en la lista de resultados."""
    detalle = primer_visible(
        page.get_by_role("link", name=re.compile(r"detalle|movimientos|ver", re.I)),
        page.get_by_role("button", name=re.compile(r"detalle|movimientos|ver", re.I)),
        page.locator("[title*='detalle' i], [aria-label*='detalle' i]"),
        page.get_by_text(numero),
    )
    if detalle is None:
        texto = page.inner_text("body")
        raise RuntimeError(f"No se encontraron resultados para la causa {numero}.\n{texto}")
    detalle.click()
    esperar_carga(page)


def extraer_actuaciones(page):
    """Recorre cada incidente/movimiento, abre sus actuaciones y devuelve el texto."""
    bloques = [page.inner_text("body")]

    patron = re.compile(r"actuaciones", re.I)
    botones = page.get_by_role("button", name=patron)
    if botones.count() == 0:
        botones = page.get_by_role("link", name=patron)
    if botones.count() == 0:
        botones = page.locator("[title*='actuaciones' i], [aria-label*='actuaciones' i]")

    total = botones.count()
    for i in range(total):
        url_detalle = page.url
        botones.nth(i).click()
        esperar_carga(page)
        bloques.append(f"===== Actuaciones {i + 1} de {total} =====\n{page.inner_text('body')}")

        # Volver al listado de movimientos para abrir el siguiente.
        if page.url != url_detalle:
            page.go_back()
        else:
            cerrar = primer_visible(
                page.get_by_role("button", name=re.compile(r"cerrar|regresar|volver", re.I))
            )
            if cerrar is not None:
                cerrar.click()
            else:
                page.keyboard.press("Escape")
        esperar_carga(page)

    return "\n\n".join(bloques)


def consultar_causa(numero, visible=False):
    numero = normalizar_numero(numero)
    ruta_chromium = detectar_chromium()

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=ruta_chromium, headless=not visible)
        try:
            page = browser.new_page()
            page.set_default_timeout(TIMEOUT_MS)
            buscar_causa(page, numero)
            abrir_detalle(page, numero)
            return extraer_actuaciones(page)
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("numero", help="Número de expediente de 21 dígitos")
    parser.add_argument("--salida", help="Guardar el texto extraído en este archivo")
    parser.add_argument("--visible", action="store_true", help="Mostrar la ventana del navegador")
    args = parser.parse_args()

    try:
        texto = consultar_causa(args.numero, visible=args.visible)
    except (ValueError, FileNotFoundError, RuntimeError, PlaywrightError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as f:
            f.write(texto)
    print(texto)


if __name__ == "__main__":
    main()
