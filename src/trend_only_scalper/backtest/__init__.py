"""Lightweight replay/backtest driver -- not a full backtesting engine.

Replays historical M1 OHLCV through the same broker-agnostic strategy loop used by
dry-run/demo, via a SimulatedBroker, producing a trade journal and metrics summary.
"""
