<p align="center">
  <h1 align="center">TankTok</h1>
  <p align="center">
    <em>Truck stop fuel prices — instant, in Telegram.</em>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/python-3.9+-blue?logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/telegram-bot-26A5E4?logo=telegram&logoColor=white" alt="Telegram">
    <img src="https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white" alt="Docker">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </p>
</p>

---

Send a **ZIP code** or **city name** to the Telegram bot. Get nearby truck stops with real diesel and gas prices — instantly.

## What It Does

TankTok is a Telegram bot built for **truck drivers**. Type a location and get:

- **Nearby truck stops** within 50 miles (Pilot, Flying J, Love's, TA, Petro, and 3,700+ more)
- **Real fuel prices** scraped live from Pilot/Flying J, Love's, and TA/Petro websites
- **WTI oil price** from Yahoo Finance (refreshed every 5 min)
- **Kalshi prediction markets** for energy contracts (optional)

### How It Works

The bot uses a **two-phase response** for speed:

1. **Phase 1 (instant)** — Looks up stations from a local database of 3,759 US truck stops, applies cached Pilot/Flying J prices from memory, and sends the message. User sees results in under 1 second.
2. **Phase 2 (background)** — Fetches Love's and TA/Petro prices in parallel, then edits the message with updated prices.

### Example Response

```
Truck Stops near Dallas, TX

1. Pilot — 2.1 mi
   1234 I-35E S, Dallas, TX 75201
   D: $3.45 | G: $2.89

2. Love's — 4.8 mi
   5678 US-75, Richardson, TX 75080
   D: $3.42 | G: $2.85

3. Flying J — 7.3 mi
   910 I-20 W, Grand Prairie, TX 75051
   D: $3.48 | G: $2.91

WTI: $71.23 (+0.8%)
```

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/lazizbekravshanov/tanktok.git
cd tanktok
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Add your TELEGRAM_BOT_TOKEN (required)
# Add EIA_API_KEY for area average prices (free, recommended)
```

### 3. Run

```bash
python -m app.main
```

## Docker

```bash
docker compose up -d

# Or without Compose
docker build -t tanktok .
docker run --env-file .env tanktok
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `EIA_API_KEY` | Recommended | [U.S. EIA API key](https://www.eia.gov/opendata/register.php) (free) — area average prices |
| `GOOGLE_MAPS_API_KEY` | No | Google Maps — better address resolution |
| `KALSHI_KEY_ID` | No | Kalshi API key for prediction markets |
| `KALSHI_PRIVATE_KEY_PATH` | No | Path to Kalshi RSA private key PEM |
| `KALSHI_USE_WEBSOCKET` | No | `true` for live streaming, `false` for REST polling |
| `NOMINATIM_USER_AGENT` | No | Custom User-Agent for Nominatim geocoding |

## Price Sources

| Chain | Method | Coverage |
|---|---|---|
| **Pilot / Flying J / One9** | Bulk JSON endpoint — all 876+ locations in one call | All US locations |
| **Love's** | Per-store HTML scraping | Stores with ID in OSM data |
| **TA / Petro** | JSON-LD structured data from location pages | Stations with valid URL slug |
| **Area average** | U.S. EIA weekly retail prices by PADD region | All US (requires API key) |

## Architecture

```
app/
├── main.py                    # Entry point + lifecycle hooks
├── config.py                  # Env var configuration
├── handlers.py                # Telegram handlers — two-phase response
├── providers/
│   ├── base.py                # Data models + abstract interfaces
│   ├── geocode_osm.py         # Nominatim geocoding + reverse geocoding
│   ├── geocode_google.py      # Google Maps reverse geocoding
│   ├── pois_truckstops.py     # Local truck stop DB (3,759 locations)
│   ├── prices_pilot.py        # Pilot/FJ/One9 bulk price fetcher
│   ├── prices_loves.py        # Love's per-store price scraper
│   ├── prices_tapetro.py      # TA/Petro JSON-LD price scraper
│   ├── retail_eia.py          # EIA area average prices
│   ├── markets_yfinance.py    # WTI, RBOB, Heating Oil futures
│   ├── prediction_kalshi.py   # Kalshi REST + WebSocket (RSA-PSS auth)
│   ├── prediction_polymarket.py
│   └── prediction_base.py
├── forecasting/
│   └── model.py               # Simple 7-day price forecast
├── storage/
│   └── cache.py               # SQLite cache with TTL + dataclass reconstruction
data/
│   └── truckstops.json        # Pre-built database of 3,759 US truck stops
scripts/
│   ├── build_truckstop_db.py  # Fetch truck stops from OpenStreetMap
│   └── fill_addresses.py      # Batch reverse-geocode missing addresses
tests/
│   ├── test_parsing.py
│   ├── test_providers.py
│   ├── test_cache.py
│   └── test_kalshi.py
```

### Performance

- **Station lookup**: < 50ms (local JSON database with bounding-box pre-filter + haversine)
- **Pilot prices**: Pre-loaded on startup, refreshed every 5 min in background
- **Market data**: Pre-warmed on startup, refreshed every 5 min in background
- **Love's / TA/Petro**: Parallel per-station HTTP with 4-second timeout
- **Geocoding**: Nominatim with 30-day cache (instant on repeat queries)
- **Address coverage**: 77% pre-baked, rest show coordinates

## Tests

```bash
pytest tests/ -v
# 58 tests
```

## License

MIT

---

<p align="center">
  <sub>Built for drivers. Data from Pilot, Love's, TA/Petro, EIA, OpenStreetMap, Yahoo Finance, Kalshi.</sub>
</p>
