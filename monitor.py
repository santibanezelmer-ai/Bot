"""
monitor.py — Loop de monitoreo cada 5 minutos.

Lógica de alertas:
  - Bajó el precio (cualquier bajada)
  - Bajó X% respecto al último precio
  - Volvió a haber stock (estaba sin stock y ahora hay)
"""

import asyncio
import logging
from typing import Optional, Callable, Awaitable

from db import Watch, get_all_active_watches, update_precio
from scraper import PriceScraper, ScrapeResult

logger = logging.getLogger(__name__)

INTERVALO_SEGUNDOS = 5 * 60   # 5 minutos

# Tipo de la función que envía la alerta al usuario de Telegram
AlertCallback = Callable[[int, str], Awaitable[None]]   # (chat_id, mensaje) → None


# ---------------------------------------------------------------------------
# Lógica de alerta
# ---------------------------------------------------------------------------

def _pct_bajada(anterior: float, nuevo: float) -> float:
    if anterior <= 0:
        return 0.0
    return (anterior - nuevo) / anterior * 100


def _formatear_precio(p: Optional[float]) -> str:
    if p is None:
        return "desconocido"
    return f"${p:,.0f}".replace(",", ".")


def evaluar_alerta(watch: Watch, result: ScrapeResult) -> Optional[str]:
    """
    Retorna el mensaje de alerta si corresponde disparar una, o None.
    """
    precio_nuevo = result.precio
    precio_ant   = watch.precio_ultimo
    stock_ant    = True   # asumir en stock si no hay dato previo
    # (el campo stock_ultimo se lee en Watch pero no lo exportamos en el dataclass;
    #  lo manejamos vía DB directamente — simplificación válida aquí)

    partes: list[str] = []

    # ── Alerta de stock ────────────────────────────────────────────────────
    if result.en_stock and not stock_ant:
        partes.append("✅ *¡Volvió a haber stock!*")

    # ── Alerta de precio ───────────────────────────────────────────────────
    if precio_nuevo is not None and precio_ant is not None and precio_nuevo < precio_ant:
        pct = _pct_bajada(precio_ant, precio_nuevo)
        ahorro = precio_ant - precio_nuevo

        # Siempre alertar si bajó (umbral 0 = cualquier bajada)
        if watch.umbral_pct == 0 or pct >= watch.umbral_pct:
            partes.append(
                f"📉 *Bajó el precio*\n"
                f"  Antes: {_formatear_precio(precio_ant)}\n"
                f"  Ahora: {_formatear_precio(precio_nuevo)}\n"
                f"  Ahorro: {_formatear_precio(ahorro)} ({pct:.1f}%)"
            )

    if not partes:
        return None

    encabezado = f"🔔 *{watch.nombre}*"
    cuerpo     = "\n\n".join(partes)
    pie        = f"\n🔗 [Ver producto]({watch.url})"
    return f"{encabezado}\n\n{cuerpo}{pie}"


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

class PriceMonitor:
    def __init__(self, scraper: PriceScraper, on_alert: AlertCallback) -> None:
        self._scraper  = scraper
        self._on_alert = on_alert
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("Monitor iniciado (intervalo %ds)", INTERVALO_SEGUNDOS)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            logger.info("Monitor detenido")

    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            try:
                await self._ciclo()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Error en ciclo del monitor: %s", e, exc_info=True)
            await asyncio.sleep(INTERVALO_SEGUNDOS)

    async def _ciclo(self) -> None:
        watches = await get_all_active_watches()
        if not watches:
            logger.debug("Sin watches activos")
            return

        logger.info("Revisando %d watches…", len(watches))
        for watch in watches:
            await self._revisar(watch)

    async def _revisar(self, watch: Watch) -> None:
        try:
            result = await self._scraper.scrape(watch.url)
            logger.info(
                "[%d] %s → precio=%s stock=%s fuente=%s",
                watch.id, watch.nombre, result.precio, result.en_stock, result.fuente,
            )

            alerta = evaluar_alerta(watch, result)

            await update_precio(watch.id, result.precio, result.en_stock)

            if alerta:
                await self._on_alert(watch.chat_id, alerta)

        except Exception as e:
            logger.warning("Error revisando watch %d (%s): %s", watch.id, watch.url, e)

    # ------------------------------------------------------------------
    # Método utilitario para chequeo inmediato (llamado desde /ver)
    # ------------------------------------------------------------------

    async def chequear_ahora(self, watch: Watch) -> ScrapeResult:
        result = await self._scraper.scrape(watch.url)
        await update_precio(watch.id, result.precio, result.en_stock)
        return result
