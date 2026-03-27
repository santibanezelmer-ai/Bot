# 🤖 Price Monitor Bot

Bot de Telegram que monitorea precios y stock en tiendas chilenas (y cualquier otra).

## Tiendas soportadas

| Tienda | Selectores específicos |
|---|---|
| Falabella | ✅ |
| Paris | ✅ |
| Samsung Chile | ✅ |
| Cruz Verde | ✅ |
| Salcobrand | ✅ |
| Ahumada | ✅ |
| Ripley | ✅ |
| Lider | ✅ |
| Cualquier otra | ✅ (heurística genérica) |

## Instalación

```bash
pip install -r requirements.txt
playwright install chromium
```

## Configuración

```bash
export TELEGRAM_BOT_TOKEN="tu_token_aqui"
```

## Uso

```bash
python bot.py
```

## Comandos del bot

| Comando | Descripción |
|---|---|
| `/agregar <url>` | Agrega producto a monitorear |
| `/agregar <url> nombre` | Con nombre personalizado |
| `/agregar <url> nombre 10` | Alerta solo si baja ≥10% |
| `/lista` | Ver todos tus productos |
| `/ver <id>` | Consultar precio ahora mismo |
| `/historial <id>` | Últimos 10 precios registrados |
| `/borrar <id>` | Dejar de monitorear |

## Alertas automáticas

El bot revisa todos los productos cada **5 minutos** y envía una alerta si:
- 📉 Bajó el precio (o bajó más del umbral configurado)
- ✅ Volvió a haber stock

## Estructura

```
price_bot/
├── bot.py          # Entry point y handlers de Telegram
├── monitor.py      # Loop de monitoreo + lógica de alertas
├── scraper.py      # Playwright + selectores por tienda
├── db.py           # SQLite con aiosqlite
└── requirements.txt
```

## Agregar una nueva tienda

En `scraper.py`, añadir al diccionario `DOMAIN_SELECTORS`:

```python
"nuevatienda.cl": [
    ("selector.precio", "button.comprar"),
],
```
