# tests/test_main_helpers.py
# Tsclp_v2.1_volatile — тесты вспомогательных функций main.py.
# Без реального API, без заявок. Моки + tmp_path для CSV.

import csv
import os
import pytest

import main
import logger as research_logger
from execution import ExecutionMonitor, PositionState


# --- Моки ---

class FakeClient:
    def __init__(self, positions=None):
        self.positions = positions if positions is not None else {}
        self.uncertain_state = False
        self.order_states = {}

    async def get_positions(self):
        return self.positions

    async def get_sandbox_order_state(self, order_id):
        return self.order_states.get(order_id)

    def is_uncertain(self):
        return self.uncertain_state


def register_buy(mon, figi="FIGI", ticker="SBER", entry=100.0, qty=1, sl=99.0, tp=102.0, lot=1):
    return mon.register(
        figi=figi, ticker=ticker, direction="BUY",
        entry_price=entry, quantity=qty, sl_price=sl, tp_price=tp,
        lot_size=lot, trade_id="tid-" + figi,
    )


# --- _trade_id_already_logged ---

def test_trade_id_not_logged_empty_file(monkeypatch, tmp_path):
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))
    research_logger._ensure_files()
    assert main._trade_id_already_logged("abc") is False


def test_trade_id_logged_present(monkeypatch, tmp_path):
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))
    research_logger._ensure_files()
    research_logger.log_trade({"trade_id": "abc", "ticker": "SBER"})
    assert main._trade_id_already_logged("abc") is True
    assert main._trade_id_already_logged("xyz") is False


def test_trade_id_empty_returns_false():
    assert main._trade_id_already_logged("") is False


# --- reconcile ---

@pytest.mark.asyncio
async def test_reconcile_skips_currency(monkeypatch):
    client = FakeClient(positions={"RUB000UTSTOM": 100000.0})
    mon = ExecutionMonitor(client)
    await main.reconcile(client, mon)
    assert main.reconcile_done is True
    assert client.uncertain_state is False


@pytest.mark.asyncio
async def test_reconcile_empty_portfolio_clears_uncertain(monkeypatch):
    client = FakeClient(positions={})
    client.uncertain_state = True
    mon = ExecutionMonitor(client)
    await main.reconcile(client, mon)
    assert main.reconcile_done is True
    assert client.uncertain_state is False


@pytest.mark.asyncio
async def test_reconcile_unknown_position_sets_uncertain(monkeypatch):
    client = FakeClient(positions={"BBG004730N88": 1.0})
    mon = ExecutionMonitor(client)
    await main.reconcile(client, mon)
    assert client.uncertain_state is True
    assert main.reconcile_done is False


@pytest.mark.asyncio
async def test_reconcile_known_position_ok(monkeypatch):
    client = FakeClient(positions={"FIGI": 1.0})
    mon = ExecutionMonitor(client)
    register_buy(mon)
    await main.reconcile(client, mon)
    assert client.uncertain_state is False
    assert main.reconcile_done is True


@pytest.mark.asyncio
async def test_reconcile_closed_but_present_is_unknown(monkeypatch):
    client = FakeClient(positions={"FIGI": 1.0})
    mon = ExecutionMonitor(client)
    pos = register_buy(mon)
    pos.state = PositionState.CLOSED
    await main.reconcile(client, mon)
    assert client.uncertain_state is True
    assert main.reconcile_done is False


@pytest.mark.asyncio
async def test_reconcile_does_not_duplicate_local(monkeypatch):
    client = FakeClient(positions={"FIGI": 1.0})
    mon = ExecutionMonitor(client)
    register_buy(mon)
    n_before = len(mon.positions)
    await main.reconcile(client, mon)
    await main.reconcile(client, mon)
    assert len(mon.positions) == n_before


# --- process_closing ---

def _setup_closed_pos(mon, actual_commission=None, entry_actual_commission=None,
                      entry_price=100.0, exit_price=105.0, qty=1, lot=1,
                      close_price_before_order=None):
    pos = register_buy(mon, qty=qty, entry=entry_price, lot=lot)
    pos.state = PositionState.CLOSED
    pos.executed_lots = qty
    pos.execution_price = exit_price
    pos.close_reason = "TP"
    pos.close_price_before_order = (
        close_price_before_order if close_price_before_order is not None else exit_price
    )
    pos.close_requested_at = "2026-01-01T00:00:00+00:00"
    pos.actual_commission = actual_commission
    pos.entry_actual_commission = entry_actual_commission
    return pos


def _read_last_trade(csv_path):
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        rows = list(reader)
    return rows[-1] if rows else None


def _read_all_trades(csv_path):
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        return list(reader)


@pytest.mark.asyncio
async def test_process_closing_model_commission(monkeypatch, tmp_path):
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))

    client = FakeClient()
    mon = ExecutionMonitor(client)
    _setup_closed_pos(mon, actual_commission=None, entry_actual_commission=None)

    await main.process_closing(client, mon)
    row = _read_last_trade(csv_path)
    assert row is not None
    assert row["commission_source"] == "model"
    assert float(row["commission_rub"]) > 0
    assert float(row["entry_commission_rub"]) > 0
    assert float(row["exit_commission_rub"]) > 0


@pytest.mark.asyncio
async def test_process_closing_actual_commission(monkeypatch, tmp_path):
    """Обе комиссии фактические → commission_source = actual."""
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))

    client = FakeClient()
    mon = ExecutionMonitor(client)
    _setup_closed_pos(mon, actual_commission=0.5, entry_actual_commission=0.5)

    await main.process_closing(client, mon)
    row = _read_last_trade(csv_path)
    assert row is not None
    assert row["commission_source"] == "actual"
    assert float(row["actual_commission_rub"]) == pytest.approx(0.5)
    assert float(row["commission_rub"]) == pytest.approx(1.0)
    assert float(row["entry_commission_rub"]) == pytest.approx(0.5)
    assert float(row["exit_commission_rub"]) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_process_closing_mixed_commission_is_model(monkeypatch, tmp_path):
    """Только выход фактический, вход модельный → commission_source = model (смешанный)."""
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))

    client = FakeClient()
    mon = ExecutionMonitor(client)
    _setup_closed_pos(mon, actual_commission=0.5, entry_actual_commission=None)

    await main.process_closing(client, mon)
    row = _read_last_trade(csv_path)
    assert row is not None
    assert row["commission_source"] == "model"
    assert float(row["actual_commission_rub"]) == pytest.approx(0.5)
    assert float(row["exit_commission_rub"]) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_process_closing_entry_and_exit_sum_to_total(monkeypatch, tmp_path):
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))

    client = FakeClient()
    mon = ExecutionMonitor(client)
    _setup_closed_pos(mon, actual_commission=1.2, entry_actual_commission=0.8)

    await main.process_closing(client, mon)
    row = _read_last_trade(csv_path)
    entry_c = float(row["entry_commission_rub"])
    exit_c = float(row["exit_commission_rub"])
    total_c = float(row["commission_rub"])
    assert entry_c + exit_c == pytest.approx(total_c)
    assert entry_c == pytest.approx(0.8)
    assert exit_c == pytest.approx(1.2)


@pytest.mark.asyncio
async def test_process_closing_slippage_percent(monkeypatch, tmp_path):
    """slippage_percent = total_slippage / turnover * 100."""
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))

    client = FakeClient()
    mon = ExecutionMonitor(client)
    # Вход: price_before_order=99, entry=100 → slippage входа = (100-99)*10 = +10
    # Выход: close_before=106, exit=105 → slippage выхода = (106-105)*10 = +10
    # total_slippage = +20, turnover = (100+105)*10 = 2050, percent = 20/2050*100 ≈ 0.976
    pos = _setup_closed_pos(
        mon, qty=10, lot=1,
        entry_price=100.0, exit_price=105.0,
        close_price_before_order=106.0,
    )
    pos.price_before_order = 99.0

    await main.process_closing(client, mon)
    row = _read_last_trade(csv_path)
    assert float(row["slippage_rub"]) == pytest.approx(20.0)
    expected_percent = 20.0 / 2050.0 * 100.0
    assert float(row["slippage_percent"]) == pytest.approx(expected_percent, abs=1e-3)


@pytest.mark.asyncio
async def test_process_closing_cumulative_net_pnl(monkeypatch, tmp_path):
    """cumulative_net_pnl_rub = сумма net_pnl всех сделок до текущей включительно."""
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))

    client = FakeClient()
    mon = ExecutionMonitor(client)

    # Сделка 1: net = +576.80 - 10 = +566.80 (приблизительно)
    pos1 = _setup_closed_pos(mon, qty=100, lot=1, entry_price=100.0, exit_price=106.0,
                             actual_commission=10.0, entry_actual_commission=0.0)
    pos1.trade_id = "cum-1"
    pos1.figi = "FIGI-1"
    mon.positions["FIGI-1"] = pos1
    await main.process_closing(client, mon)

    # Сделка 2: net = -240 - 10 = -250 (приблизительно)
    mon.positions.clear()
    pos2 = _setup_closed_pos(mon, qty=100, lot=1, entry_price=100.0, exit_price=97.6,
                             actual_commission=10.0, entry_actual_commission=0.0)
    pos2.trade_id = "cum-2"
    pos2.figi = "FIGI-2"
    mon.positions["FIGI-2"] = pos2
    await main.process_closing(client, mon)

    rows = _read_all_trades(csv_path)
    assert len(rows) == 2
    net1 = float(rows[0]["net_pnl_rub"])
    net2 = float(rows[1]["net_pnl_rub"])
    cum1 = float(rows[0]["cumulative_net_pnl_rub"])
    cum2 = float(rows[1]["cumulative_net_pnl_rub"])
    assert cum1 == pytest.approx(net1)
    assert cum2 == pytest.approx(net1 + net2)


@pytest.mark.asyncio
async def test_process_closing_idempotent(monkeypatch, tmp_path):
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))

    client = FakeClient()
    mon = ExecutionMonitor(client)
    pos = _setup_closed_pos(mon, actual_commission=None, entry_actual_commission=None)
    pos.trade_id = "unique-tid-1"

    await main.process_closing(client, mon)
    await main.process_closing(client, mon)
    await main.process_closing(client, mon)

    rows = [r for r in _read_all_trades(csv_path) if r.get("trade_id") == "unique-tid-1"]
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_process_closing_trade_id_already_logged_marks_logged(monkeypatch, tmp_path):
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(research_logger, "TRADES_FILE", str(csv_path))
    monkeypatch.setattr(research_logger, "EVENTS_FILE", str(tmp_path / "events.jsonl"))
    research_logger._ensure_files()
    research_logger.log_trade({"trade_id": "dup-1", "ticker": "SBER"})

    client = FakeClient()
    mon = ExecutionMonitor(client)
    pos = _setup_closed_pos(mon, actual_commission=None, entry_actual_commission=None)
    pos.trade_id = "dup-1"

    await main.process_closing(client, mon)
    assert pos.logged is True

    rows = [r for r in _read_all_trades(csv_path) if r.get("trade_id") == "dup-1"]
    assert len(rows) == 1