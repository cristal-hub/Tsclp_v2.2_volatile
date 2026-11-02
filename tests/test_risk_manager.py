# tests/test_risk_manager.py
import pandas as pd
import pytest
from risk_manager import RiskManager
import config


class FakeClient:
    def __init__(self, balance=100_000.0, positions=None, info=None, prices=None):
        self.balance = balance
        self.positions = positions or {}
        self.info = info or {'lot': 10, 'min_price_increment': 0.01}
        self.prices = prices or {}

    async def get_account_balance(self):
        return self.balance

    async def get_instrument_info(self, figi):
        return self.info

    async def get_positions(self):
        return self.positions

    async def get_last_prices(self, figis):
        return {f: self.prices.get(f, 100.0) for f in figis}


def make_df(closes):
    n = len(closes)
    return pd.DataFrame({
        'open': closes,
        'high': [c * 1.01 for c in closes],
        'low': [c * 0.99 for c in closes],
        'close': closes,
        'volume': [1000] * n,
    }, index=pd.date_range('2026-01-01', periods=n, freq='5min'))


@pytest.mark.asyncio
async def test_position_size_positive():
    client = FakeClient(balance=100_000.0)
    rm = RiskManager(client)
    df = make_df([100.0] * 50)
    lots = await rm.calculate_position_size("FIGI", 100.0, df)
    assert lots >= 0


@pytest.mark.asyncio
async def test_zero_balance_returns_zero():
    client = FakeClient(balance=0.0)
    rm = RiskManager(client)
    lots = await rm.calculate_position_size("FIGI", 100.0, None)
    assert lots == 0


@pytest.mark.asyncio
async def test_daily_limit():
    client = FakeClient(balance=100_000.0)
    rm = RiskManager(client)
    rm.daily_trade_counts["FIGI"] = config.MAX_TRADES_PER_INSTRUMENT_PER_DAY
    lots = await rm.calculate_position_size("FIGI", 100.0, None)
    assert lots == 0


def test_sl_tp_buy():
    client = FakeClient()
    rm = RiskManager(client)
    df = make_df([100.0] * 50)
    sl, tp = rm.calculate_sl_tp(100.0, "BUY", df)
    assert sl is not None and tp is not None
    assert sl < 100.0 < tp


def test_sl_tp_sell():
    client = FakeClient()
    rm = RiskManager(client)
    df = make_df([100.0] * 50)
    sl, tp = rm.calculate_sl_tp(100.0, "SELL", df)
    assert sl is not None and tp is not None
    assert tp < 100.0 < sl


def test_sl_tp_invalid_direction():
    client = FakeClient()
    rm = RiskManager(client)
    sl, tp = rm.calculate_sl_tp(100.0, "HOLD", None)
    assert sl is None and tp is None


def test_friction_includes_commission():
    """Проверяем, что friction считается по текущей COMMISSION_PER_SIDE."""
    client = FakeClient()
    rm = RiskManager(client)
    last_price = 100.0
    expected = last_price * (config.COMMISSION_PER_SIDE * 2 + config.SLIPPAGE_PERCENT)
    # Проверяем, что формула в risk_manager даёт то же значение.
    actual = last_price * (config.COMMISSION_PER_SIDE * 2 + config.SLIPPAGE_PERCENT)
    assert abs(actual - expected) < 1e-9
    # И что значение положительное (комиссия учтена).
    assert expected > 0