<p align="center">
  <h1 align="center">⛽ TankTok</h1>
  <p align="center">
    <em>Real-time fuel prices & nearby stations — right in Telegram.</em>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/python-3.11+-blue?logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/telegram-bot-26A5E4?logo=telegram&logoColor=white" alt="Telegram">
    <img src="https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white" alt="Docker">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </p>
</p>

---

Send a **ZIP code** or **city name** → get gas & diesel prices, nearby stations, energy markets, and a 7-day forecast.

## ✨ Features

| | Feature | Details |
|---|---|---|
| 📊 | **Area prices** | Regular gas + diesel from U.S. EIA (weekly) |
| 📍 | **Nearby stations** | Top 10 fuel stops via OpenStreetMap / Overpass |
| 💰 | **Station prices** | Posted prices when a plugin is configured |
| 📈 | **Market snapshot** | WTI crude, RBOB gasoline, Heating Oil futures |
| 🔮 | **7-day forecast** | Naive model using retail history + futures trend |
| 🎲 | **Prediction markets** | Optional Kalshi / Polymarket connectors |
| 🔌 | **Plugin architecture** | Add price feeds via env vars — zero code changes |

## 🚀 Quick Start

### 1️⃣ Prerequisites

- Python 3.11+
- A [Telegram Bot Token](https://core.telegram.org/bots#botfather) from **@BotFather**
- *(Recommended)* An [EIA API Key](https://www.eia.gov/opendata/register.php) — free

### 2️⃣ Clone & install

```bash
git clone https://github.com/youruser/tanktok.git
cd tanktok
pip install -r requirements.txt
```

### 3️⃣ Configure

```bash
cp .env.example .env
# Edit .env with your tokens
```

### 4️⃣ Run

```bash
python -m app.main
```

## 🐳 Docker

```bash
# Build & run
docker compose up -d

# Or without Compose
docker build -t tanktok .
docker run --env-file .env tanktok
```

## ⚙️ Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `EIA_API_KEY` | 📌 Recommended | U.S. EIA API key (free) |
| `CROWD_API_KEY` | ❌ | Crowd-sourced station price API key |
| `CROWD_API_BASE` | ❌ | Base URL for crowd-sourced API |
| `COMMERCIAL_FEED_KEY` | ❌ | Commercial station price feed key |
| `COMMERCIAL_FEED_BASE` | ❌ | Base URL for commercial feed |
| `KALSHI_API_TOKEN` | ❌ | Kalshi prediction market token |
| `POLYMARKET_API_TOKEN` | ❌ | Polymarket API token |
| `NOMINATIM_USER_AGENT` | ❌ | Custom User-Agent for Nominatim |
| `TANKTOK_DB_PATH` | ❌ | SQLite cache path (default: `tanktok_cache.db`) |

## 💬 Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message + examples |
| `/help` | Usage guide + data sources |
| `/sources` | Show enabled/disabled providers |
| `/setunits` | Unit settings (gallon — more coming) |

## 📱 Example Response

```
⛽ TankTok — Cincinnati, OH, USA

📊 Area Prices — PADD 2
  Regular Gas: $3.287/gal  ▼ $0.012 (weekly)
  Diesel: $3.891/gal  ▲ $0.005 (weekly)
  Source: U.S. EIA (2025-02-24)

📍 Nearby Stations
  1. Shell (0.3 mi)
     123 Main St, Cincinnati OH
     price unavailable
  2. BP (0.7 mi)
     456 Vine St, Cincinnati OH
     price unavailable
  ...

📈 Market Snapshot
  WTI Crude Oil: $72.15  ▼ 1.23%
  RBOB Gasoline: $2.48  ▲ 0.45%
  Heating Oil (ULSD proxy): $2.71  ▼ 0.18%

🔮 7-Day Forecast
  Regular Gasoline: $3.245 – $3.329/gal
  Diesel: $3.841 – $3.941/gal
    Confidence: Medium — based on weekly EIA data + futures

🎲 Prediction Markets
  No matching prediction market contracts found
  for fuel prices today.

🕐 2025-02-25 14:32 UTC
```

## 🏗️ Architecture

```
app/
├── main.py                 # Entry point
├── config.py               # Env var config
├── handlers.py             # Telegram handlers + formatting
├── providers/
│   ├── base.py             # Interfaces & data models
│   ├── geocode_osm.py      # Nominatim geocoding
│   ├── pois_overpass.py     # Overpass fuel station POIs
│   ├── retail_eia.py       # EIA retail prices
│   ├── markets_yfinance.py # yfinance energy futures
│   ├── prediction_base.py  # Disabled prediction stub
│   ├── prediction_kalshi.py
│   └── prediction_polymarket.py
├── forecasting/
│   └── model.py            # Simple price forecast
└── storage/
    └── cache.py            # SQLite cache with TTL
```

### 🔌 Adding a price provider

1. Implement `StationPriceProvider` from `app/providers/base.py`
2. Add env vars for API credentials in `config.py`
3. Wire it into `BotHandlers.__init__()` in `handlers.py`
4. Station prices will show as **"posted"** in responses

## 🧪 Tests

```bash
pytest tests/ -v
```

## 📄 License

MIT

---

<p align="center">
  <sub>Built with 🛢️ by TankTok — data from EIA, OpenStreetMap, Yahoo Finance</sub>
</p>
