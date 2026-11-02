# tests/test_screener.py
# Tsclp_v2.1_volatile — тесты скринера.
# Обновлено: Screener() без аргументов; AsyncClient подменяется через monkeypatch.

import pytest
from datetime import datetime, timedelta

import screener as screener_module
from screener import Screener
import config


class FakeQuotation:
    def __init__(self, value):
        self.units = int(value)
        self.nano = int((value % 1) * 1e9)


class FakeCandle:
    def __init__(self, high, low, close, volume):
        self.high = FakeQuotation(high)
        self.low = FakeQuotation(low)
        self.close = FakeQuotation(close)
        self.volume = volume
        self.time = datetime.now()


class FakeShare:
    def __init__(self, figi, ticker, name, real_exchange=1, currency="rub",
                 buy_available_flag=True, sell_available_flag=True):
        self.figi = figi
        self.ticker = ticker
        self.name = name
        self.real_exchange = real_exchange
        self.currency = currency
        self.buy_available_flag = buy_available_flag
        self.sell_available_flag = sell_available_flag


class FakeSharesResponse:
    def __init__(self, shares):
        self.instruments = shares


class FakeInstrumentsApi:
    def __init__(self, shares):
        self._shares = shares
        self.calls = 0

    async def shares(self, instrument_status=None):
        self.calls += 1
        return FakeSharesResponse(self._shares)


class FakeAsyncClient:
    """Имитирует AsyncClient: async context manager + get_all_candles."""
    instances = []

    def __init__(self, *args, **kwargs):
        self._shares = []
        self._candles_by_figi = {}
        self.instruments = FakeInstrumentsApi(self._shares)
        self.candle_calls = 0
        FakeAsyncClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def get_all_candles(self, figi, from_, interval):
        self.candle_calls += 1
        for c in self._candles_by_figi.get(figi, []):
            yield c


def _install_fake_client(monkeypatch, shares, candles_by_figi):
    FakeAsyncClient.instances = []

    def _factory(*args, **kwargs):
        inst = FakeAsyncClient()
        inst._shares = shares
        inst.instruments = FakeInstrumentsApi(shares)
        inst._candles_by_figi = candles_by_figi
        return inst

    monkeypatch.setattr(screener_module, "AsyncClient", _factory)
    monkeypatch.setattr(config, "API_CALL_DELAY_SECONDS", 0)
    monkeypatch.setattr(config, "API_RETRY_DELAY_SECONDS", 0)


@pytest.mark.asyncio
async def test_screener_returns_top_n(monkeypatch):
    shares = [
        FakeShare("F1", "AAA", "A"),
        FakeShare("F2", "BBB", "B"),
        FakeShare("F3", "CCC", "C"),
    ]
    candles = {
        "F1": [FakeCandle(110, 90, 100, 1_000_000) for _ in range(30)],
        "F2": [FakeCandle(105, 95, 100, 1_000_000) for _ in range(30)],
        "F3": [FakeCandle(101, 99, 100, 1_000_000) for _ in range(30)],
    }
    _install_fake_client(monkeypatch, shares, candles)
    screener = Screener()
    result = await screener.select_top_volatile()
    assert result is not None
    assert len(result) == 3
    assert result[0]['ticker'] == "AAA"


@pytest.mark.asyncio
async def test_screener_filters_low_turnover(monkeypatch):
    monkeypatch.setattr(config, "MIN_DAILY_TURNOVER_RUB", 50_000_000)
    shares = [FakeShare("F1", "AAA", "A")]
    candles = {"F1": [FakeCandle(110, 90, 100, 1_000_000) for _ in range(30)]}
    _install_fake_client(monkeypatch, shares, candles)
    screener = Screener()
    result = await screener.select_top_volatile()
    assert result is not None
    assert len(result) == 1


@pytest.mark.asyncio
async def test_screener_returns_none_on_empty(monkeypatch):
    _install_fake_client(monkeypatch, [], {})
    screener = Screener()
    result = await screener.select_top_volatile()
    assert result is None


@pytest.mark.asyncio
async def test_screener_cache(monkeypatch):
    shares = [FakeShare("F1", "AAA", "A")]
    candles = {"F1": [FakeCandle(110, 90, 100, 1_000_000) for _ in range(30)]}
    _install_fake_client(monkeypatch, shares, candles)
    screener = Screener()
    r1 = await screener.select_top_volatile()
    calls_after_first = FakeAsyncClient.instances[0].candle_calls
    r2 = await screener.select_top_volatile()
    # Второй вызов — из кэша (новых AsyncClient не создаётся).
    assert len(FakeAsyncClient.instances) == 1
    assert FakeAsyncClient.instances[0].candle_calls == calls_after_first
    assert r1 == r2


@pytest.mark.asyncio
async def test_screener_error_keeps_last_success(monkeypatch):
    """ТЗ п.16: ошибка скринера → возврат последнего успешного списка."""
    shares = [FakeShare("F1", "AAA", "A")]
    candles = {"F1": [FakeCandle(110, 90, 100, 1_000_000) for _ in range(30)]}
    _install_fake_client(monkeypatch, shares, candles)
    screener = Screener()
    first = await screener.select_top_volatile()
    assert first is not None

    # Форсируем новый скан (force=True) и ломаем фабрику клиента.
    def _broken_factory(*args, **kwargs):
        raise RuntimeError("API down")

    monkeypatch.setattr(screener_module, "AsyncClient", _broken_factory)
    second = await screener.select_top_volatile(force=True)
    assert second is not None
    assert second == first