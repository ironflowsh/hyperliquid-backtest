# AGENTS.md

A Hyperliquid backtest template on Ironflow candles and liquidations. One file, `backtest.py`.

## Run and test

- `python backtest.py` runs keyless in about a second: BTC, 5m bars, last 24 hours, two API calls. Python 3.9+, standard library only.
- `python backtest.py --months 2026-09,2026-10` reads hourly bars from the free monthly Parquet files. Needs `pip install pandas pyarrow`, downloads about 15 MB a month.
- `IRONFLOW_KEY` (free from https://ironflow.sh/key) raises the API path from 24 hours to 14 days (`--days 14`).

## Where history comes from

- Hyperliquid's `candleSnapshot` returns at most 5,000 candles per interval. Do not page it for long backtests.
- `GET https://api.ironflow.sh/v1/candles?market=BTC-PERP&interval=5m&from=-24h&limit=1000` pages with `pagination.next_cursor`. Intervals 1m, 5m, 15m, 1h, 4h, 1d. Times are Unix ms.
- Liquidations per bar come from one read-only SQL query: `GET https://api.ironflow.sh/v1/query?sql=...` over `hl.liquidations` (`ts`, `market`, `side`, `notional`). `side = 'sell'` is a liquidated long.
- Keyless: 30 requests a minute, 300 a day, 24 hours of history. A 429 carries `Retry-After`.
- Hourly candles since May 2025: `https://huggingface.co/datasets/ironflow/hyperliquid-candles-1h/resolve/main/data/YYYY-MM.parquet` (`timestamp`, `market`, `coin`, `open`, `high`, `low`, `close`, `volume`, `trades`). Liquidations: `.../hyperliquid-liquidations/resolve/main/data/events/YYYY-MM.parquet` (`timestamp`, `market`, `position_side`, `notional_usd`).

## Changing the strategy

- Edit `signal(bars, i)`. Return 1, -1 or 0 for the position after bar `i` closes, and read only `bars[: i + 1]`: anything later is look-ahead.
- Fills are at the next bar's open, the exit `HOLD` bars later. Keep `FEE` (0.045% taker per side at Hyperliquid's base tier) and `SLIPPAGE` in every result you report.
- Funding is not charged. For holds of days, add it from `GET /v1/funding?market=BTC-PERP` or the free funding files.
- To paper trade a strategy with live prices, use the hyperliquid-trading-bot template: the official Hyperliquid SDK on an Ironflow paper box.

## Reference

- Guide: https://ironflow.sh/guides/hyperliquid-backtesting
- OpenAPI: https://api.ironflow.sh/openapi.yaml. SQL tables: `GET https://api.ironflow.sh/v1/query/schema`.
- MCP server for agents: https://mcp.ironflow.sh/mcp
