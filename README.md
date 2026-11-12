# Hyperliquid backtest

Backtest a Hyperliquid strategy in plain Python on Ironflow candles and liquidations, with Hyperliquid's fees on every fill. History goes past the 5,000-candle limit of Hyperliquid's own API: hourly bars back to May 2025.

## Run

```bash
python backtest.py
```

Python 3.9 or newer, standard library only, no key. This tests BTC on 5-minute bars over the last 24 hours:

```text
BTC 5m, last 1 d: 289 bars, $117,628,607 liquidated
  11-12 13:10  long    72,387.00 ->  72,556.00  +0.12%
  11-12 14:25  long    71,867.00 ->  71,360.00  -0.82%
trades 4, won 1, return -1.16%, max drawdown 1.16%, buy and hold -4.17%
```

Months of hourly bars from the free monthly files:

```bash
pip install pandas pyarrow
python backtest.py --months 2026-09,2026-10
```

```text
BTC 1h, 2026-09 to 2026-10: 1,464 bars, $1,781,057,880 liquidated
trades 44, won 22, return +7.65%, max drawdown 7.54%, buy and hold -1.32%
```

Other markets and windows: `--coin ETH`, `--interval 1m`, and with a free key from [ironflow.sh/key](https://ironflow.sh/key), `IRONFLOW_KEY=if_your_key python backtest.py --days 14`.

## The data

Each bar has `open`, `high`, `low`, `close`, `volume`, and the USD of longs (`long_liq`) and shorts (`short_liq`) liquidated in it.

| Source | Bars | Reach |
|---|---|---|
| `GET https://api.ironflow.sh/v1/candles` plus one SQL query on `hl.liquidations` | 1m to 4h | 24 hours without a key, 14 days with a free key |
| [Free monthly files](https://huggingface.co/datasets/ironflow/hyperliquid-candles-1h) on Hugging Face (`--months`) | 1h | Every month since May 2025 |

Candles include every fill Ironflow's nodes record, liquidations and auto-deleveraging too.

## The strategy

The default fades liquidation flushes. After a down bar whose long liquidations are in the top 5% of the last 96 bars, it buys at the next open and sells 6 bars later. Short liquidations on an up bar work the same way, short. It is an example, not a strategy to trade.

Write your own in `signal(bars, i)`: return `1` for long, `-1` for short or `0` for none, reading only `bars[: i + 1]`. Costs are set at the top of `backtest.py`: 0.045% taker fee and 1 bp slippage per side. Funding is not charged.

## Next

- Paper trade the same idea with live prices: [hyperliquid-trading-bot](https://github.com/ironflowsh/hyperliquid-trading-bot).
- [How to backtest on Hyperliquid](https://ironflow.sh/guides/hyperliquid-backtesting): data sources, fees and funding.
- [Hyperliquid MCP server](https://docs.ironflow.sh/guides/ai-agents) for Claude, Cursor and other agents.

## License

MIT. The Hugging Face files carry their own license: see each dataset card.
