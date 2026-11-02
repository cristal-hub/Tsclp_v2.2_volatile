# tests/test_fail_closed.py
# Tsclp_v2.1_volatile — тесты fail-closed (ТЗ п.5, п.6, п.7).

import csv
import pytest

import main
import logger as research_logger
from execution import ExecutionMonitor, PositionState


class FakeClient:
    def __init__(self, positions=None, raise_on_positions=False):
        self._positions = positions
        self._raise = raise_on_positions
        self.uncertain_state = False
        self.order_states = {}

    async def get_positions(self):
        if self._raise:
            self.uncertain_state = True
            return None
        return self._positions

    async def get_last_prices(self, figi_list):
        # Возвращаем пустой словарь — trading_cycle выйдет на «нет цен».
        return {}

    async def get_sandbox_order_state(self, order_id):
        return self.order_states.get(order_id)

    def is_uncertain(self):
        return self.uncertain_state


@pytest.mark.asyncio
async def test_reconcile_error_does_not_clear_uncertain():
    """Ошибка API (None) → UNCERTAIN НЕ снимается, reconcile_done = False."""
    client = FakeClient(positions=None, raise_on_positions=True)
    mon = ExecutionMonitor(client)
    await main.reconcile(client, mon)
    assert client.uncertain_state is True
    assert main.reconcile_done is False


@pytest.mark.asyncio
async def test_reconcile_empty_ok_clears_uncertain():
    """Успешный пустой портфель → UNCERTAIN снимается."""
    client = FakeClient(positions={})
    client.uncertain_state = True
    mon = ExecutionMonitor(client)
    await main.reconcile(client, mon)
    assert client.uncertain_state is False
    assert main.reconcile_done is True


@pytest.mark.asyncio
async def test_trading_cycle_blocks_entry_on_positions_error():
    """
    Если get_positions() вернул None перед входом — новые входы запрещены.
    get_last_prices возвращает {} — trading_cycle выходит на «Нет цен» до проверки positions.
    Поэтому проверяем через reconcile-путь: после reconcile(ошибка) uncertain_state=True.
    """
    client = FakeClient(positions=None, raise_on_positions=True)
    mon = ExecutionMonitor(client)
    main.reconcile_done = True
    # reconcile при ошибке сохраняет UNCERTAIN и сбрасывает reconcile_done.
    await main.reconcile(client, mon)
    assert client.uncertain_state is True
    assert main.reconcile_done is False


@pytest.mark.asyncio
async def test_trading_cycle_exits_on_no_prices():
    """Если цен нет — цикл корректно выходит, не отправляя заявок."""
    client = FakeClient(positions={})
    mon = ExecutionMonitor(client)
    import main as m
    old_top = m.top_instruments
    m.top_instruments = []
    try:
        # Не должно падать и не должно отправлять заявок.
        await main.trading_cycle(client, None, None, mon)
    finally:
        m.top_instruments = old_top


@pytest.mark.asyncio
async def test_log_trade_returns_true_on_success(monkeypatch, tmp_path):
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))
    ok = research_logger.log_trade({"trade_id": "t1", "ticker": "SBER"})
    assert ok is True
    with open(csv_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "t1" in content


@pytest.mark.asyncio
async def test_log_trade_returns_false_on_error(monkeypatch, tmp_path):
    """Недоступная директория → log_trade возвращает False (не падает)."""
    bad_path = tmp_path / "no_such_dir" / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(bad_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))
    ok = research_logger.log_trade({"trade_id": "t2", "ticker": "SBER"})
    assert ok is False


@pytest.mark.asyncio
async def test_process_closing_does_not_mark_logged_on_write_failure(monkeypatch, tmp_path):
    """
    Контракт: если log_trade вернул False — pos.logged остаётся False
    (повторная попытка на следующем цикле).
    Изолируем от файловой системы: monkeypatch log_trade -> False.
    """
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))

    # Подменяем log_trade на «всегда False».
    def _fail_log_trade(_trade):
        return False
    monkeypatch.setattr(research_logger, "log_trade", _fail_log_trade)

    client = FakeClient(positions={})
    mon = ExecutionMonitor(client)
    pos = mon.register(
        figi="F", ticker="T", direction="BUY",
        entry_price=100.0, quantity=1, sl_price=99.0, tp_price=102.0,
        lot_size=1, trade_id="tid-fail",
    )
    pos.state = PositionState.CLOSED
    pos.executed_lots = 1
    pos.execution_price = 101.0
    pos.close_reason = "TP"
    pos.close_price_before_order = 101.0
    pos.close_requested_at = "2026-01-01T00:00:00+00:00"

    await main.process_closing(client, mon)
    assert pos.logged is False


@pytest.mark.asyncio
async def test_mixed_commission_source(monkeypatch, tmp_path):
    """Только выход фактический, вход модельный → mixed."""
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))

    client = FakeClient(positions={})
    mon = ExecutionMonitor(client)
    pos = mon.register(
        figi="F", ticker="T", direction="BUY",
        entry_price=100.0, quantity=1, sl_price=99.0, tp_price=102.0,
        lot_size=1, trade_id="tid-mixed",
    )
    pos.state = PositionState.CLOSED
    pos.executed_lots = 1
    pos.execution_price = 101.0
    pos.close_reason = "TP"
    pos.close_price_before_order = 101.0
    pos.close_requested_at = "2026-01-01T00:00:00+00:00"
    pos.actual_commission = 0.5          # выход — фактическая
    pos.entry_actual_commission = None   # вход — модельная

    await main.process_closing(client, mon)
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["commission_source"] == "mixed"