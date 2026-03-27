"""
scraper.py — Extrae precio y stock de cualquier URL de tienda.

Estrategia en capas:
  1. Selectores específicos por dominio (Falabella, Paris, Samsung CL, etc.)
  2. Heurística genérica: busca el elemento con precio más destacado (font-size)
  3. Regex sobre el texto completo de la página como último recurso
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resultado del scrape
# ---------------------------------------------------------------------------

@dataclass
class ScrapeResult:
    precio: Optional[float]      # None si no se encontró
    en_stock: bool
    moneda: str = "CLP"
    fuente: str = "desconocido"  # qué selector/método lo encontró


# ---------------------------------------------------------------------------
# Mapa de selectores por dominio
# ---------------------------------------------------------------------------

# Cada entrada: lista de (selector_precio, selector_stock_o_None)
# El selector de stock devuelve un elemento visible cuando HAY stock.
DOMAIN_SELECTORS: dict[str, list[tuple[str, Optional[str]]]] = {
    "falabella.com": [
        ("span.jsx-3373122185.price-container > span", "button[data-add-to-cart]"),
        (".product-price", "button[data-add-to-cart]"),
    ],
    "paris.cl": [
        ("span.price", ".add-to-cart-button"),
        (".product-price__amount", ".add-to-cart-button"),
    ],
    "samsung.com": [
        (".price-area .price", ".add-to-cart"),
        (".pdp-price", None),
    ],
    "cruzverde.cl": [
        ("span.price", "button.add-to-cart"),
        (".product-price", None),
    ],
    "salcobrand.cl": [
        (".product-price", ".btn-add-cart"),
    ],
    "ahumada.cl": [
        (".product-price", ".btn-add-cart"),
    ],
    "ripley.cl": [
        ("span.buy-box__price", ".add-to-cart-button"),
    ],
    "lider.cl": [
        (".price-sales", "[data-testid='add-to-cart']"),
    ],
}

# Selectores genéricos (fallback si el dominio no está mapeado)
GENERIC_PRICE_SELECTORS = [
    "span.price",
    "[class*='price']",
    "[data-testid*='price']",
    "[itemprop='price']",
    "meta[itemprop='price']",
]

GENERIC_STOCK_SELECTORS = [
    "button[class*='add-to-cart']",
    "button[class*='agregar']",
    "[data-testid*='add-to-cart']",
    "button[class*='buy']",
]

OUT_OF_STOCK_TEXTS = [
    "sin stock", "agotado", "no disponible", "out of stock",
    "notify me", "avísame", "próximamente",
]

# ---------------------------------------------------------------------------
# Limpieza de precios
# ---------------------------------------------------------------------------

_PRECIO_RE = re.compile(r"[\$\s\.]*([\d]{1,3}(?:[.\s]?\d{3})*(?:,\d+)?)")

def parse_precio(texto: str) -> Optional[float]:
    """Convierte '$1.299.990' o '1299990' → 1299990.0"""
    texto = texto.strip().replace("\xa0", " ")
    m = _PRECIO_RE.search(texto)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", ".").replace(" ", "")
    try:
        return float(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------

class PriceScraper:
    def __init__(self) -> None:
        self._browser: Optional[Browser] = None
        self._playwright = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        logger.info("Playwright browser iniciado")

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Playwright browser cerrado")

    # ------------------------------------------------------------------

    async def scrape(self, url: str) -> ScrapeResult:
        context = await self._new_context()
        try:
            page = await context.new_page()
            await self._goto(page, url)
            return await self._extract(page, url)
        except Exception as e:
            logger.warning("Error scraping %s: %s", url, e)
            return ScrapeResult(precio=None, en_stock=False, fuente="error")
        finally:
            await context.close()

    # ------------------------------------------------------------------
    # Navegación
    # ------------------------------------------------------------------

    async def _new_context(self) -> BrowserContext:
        return await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="es-CL",
            timezone_id="America/Santiago",
            viewport={"width": 1280, "height": 800},
        )

    async def _goto(self, page: Page, url: str) -> None:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        # Esperar que desaparezcan loaders comunes
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        await page.wait_for_timeout(2_000)

    # ------------------------------------------------------------------
    # Extracción
    # ------------------------------------------------------------------

    async def _extract(self, page: Page, url: str) -> ScrapeResult:
        domain = urlparse(url).hostname or ""
        domain = domain.removeprefix("www.")

        # 1. Selectores específicos del dominio
        for base_domain, pairs in DOMAIN_SELECTORS.items():
            if base_domain in domain:
                for price_sel, stock_sel in pairs:
                    result = await self._try_selectors(page, price_sel, stock_sel, fuente=f"domain:{base_domain}")
                    if result.precio is not None:
                        return result
                break  # dominio encontrado pero ningún selector funcionó → continuar

        # 2. Selectores genéricos
        for sel in GENERIC_PRICE_SELECTORS:
            result = await self._try_selectors(page, sel, None, fuente=f"generic:{sel}")
            if result.precio is not None:
                return result

        # 3. Meta tag og:price o schema.org
        result = await self._try_meta(page)
        if result.precio is not None:
            return result

        # 4. Regex sobre texto completo (último recurso)
        return await self._regex_fallback(page)

    async def _try_selectors(
        self, page: Page, price_sel: str, stock_sel: Optional[str], fuente: str
    ) -> ScrapeResult:
        try:
            # Meta tags usan attribute, no inner_text
            if price_sel.startswith("meta"):
                el = page.locator(price_sel).first
                precio_raw = await el.get_attribute("content", timeout=3_000)
            else:
                el = page.locator(price_sel).first
                precio_raw = await el.inner_text(timeout=3_000)

            precio = parse_precio(precio_raw or "")
            if precio is None:
                return ScrapeResult(precio=None, en_stock=False)

            en_stock = await self._check_stock(page, stock_sel)
            logger.debug("Precio encontrado con '%s': %s", fuente, precio)
            return ScrapeResult(precio=precio, en_stock=en_stock, fuente=fuente)
        except Exception:
            return ScrapeResult(precio=None, en_stock=False)

    async def _try_meta(self, page: Page) -> ScrapeResult:
        for attr_selector in [
            "meta[property='og:price:amount']",
            "meta[name='twitter:data1']",
        ]:
            try:
                el = page.locator(attr_selector).first
                val = await el.get_attribute("content", timeout=2_000)
                precio = parse_precio(val or "")
                if precio:
                    en_stock = await self._check_stock(page, None)
                    return ScrapeResult(precio=precio, en_stock=en_stock, fuente="meta-tag")
            except Exception:
                pass
        return ScrapeResult(precio=None, en_stock=False)

    async def _regex_fallback(self, page: Page) -> ScrapeResult:
        try:
            body = await page.inner_text("body", timeout=5_000)
            precios = _PRECIO_RE.findall(body)
            candidatos = [parse_precio(p) for p in precios if p]
            candidatos = [p for p in candidatos if p and 1_000 < p < 100_000_000]
            if not candidatos:
                return ScrapeResult(precio=None, en_stock=False, fuente="regex-fallback")
            # Tomar la moda o el primero
            precio = max(set(candidatos), key=candidatos.count)
            en_stock = await self._check_stock(page, None)
            logger.debug("Precio por regex: %s", precio)
            return ScrapeResult(precio=precio, en_stock=en_stock, fuente="regex-fallback")
        except Exception as e:
            logger.warning("Regex fallback falló: %s", e)
            return ScrapeResult(precio=None, en_stock=False, fuente="error")

    async def _check_stock(self, page: Page, stock_sel: Optional[str]) -> bool:
        """Devuelve True si hay stock."""
        # 1. Selector específico positivo
        if stock_sel:
            try:
                count = await page.locator(stock_sel).count()
                if count > 0:
                    return True
            except Exception:
                pass

        # 2. Selectores genéricos de botón de compra
        for sel in GENERIC_STOCK_SELECTORS:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2_000):
                    return True
            except Exception:
                pass

        # 3. Texto de sin stock en el body
        try:
            body = (await page.inner_text("body", timeout=3_000)).lower()
            if any(t in body for t in OUT_OF_STOCK_TEXTS):
                return False
        except Exception:
            pass

        return True  # asumir en stock si no hay evidencia contraria
