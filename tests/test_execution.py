# tests/test_execution.py
# Tsclp_v2.1_volatile — тесты ExecutionMonitor.
# Обновлено под register(**kwargs) и check_all -> List[TrackedPosition].

import pytest
from execution import ExecutionMonitor, PositionState


class FakeClient:
    def __init__(self):
        self.uncertain_state = False
        self.order_states = {}

    async def get_sandbox_order_state(self, order_id):
        return self.order_states.get(order_id)


def make_state(status_int, lots_executed, units, nano):
    class Q:
        pass

    class State:
        pass

    st = State()
    st.execution_report_status = status_int
    st.lots_executed = lots_executed
    q = Q()
    q.units = units
    q.nano = nano
    st.executed_order_price = q
    return st


def register_buy(mon, figi="FIGI", ticker="SBER", entry=100.0, qty=1, sl=99.0, tp=102.0, lot=1):
    return mon.register(
        figi=figi, ticker=ticker, direction="BUY",
        entry_price=entry, quantity=qty, sl_price=sl, tp_price=tp,
        lot_size=lot,
    )


# --- Базовые SL/TP ---

@pytest.mark.asyncio
async def test_sl_trigger_buy():
    mon = ExecutionMonitor(FakeClient())
    register_buy(mon)
    to_close = await mon.check_all({"FIGI": 98.5})
    assert len(to_close) == 1
    pos = to_close[0]
    assert pos.close_reason == "SL"
    assert pos.state == PositionState.CLOSING
    assert pos.close_price_before_order == 98.5


@pytest.mark.asyncio
async def test_tp_trigger_buy():
    mon = ExecutionMonitor(FakeClient())
    register_buy(mon)
    to_close = await mon.check_all({"FIGI": 102.5})
    assert len(to_close) == 1
    assert to_close[0].close_reason == "TP"


@pytest.mark.asyncio
async def test_no_trigger_inside_range():
    mon = ExecutionMonitor(FakeClient())
    register_buy(mon)
    to_close = await mon.check_all({"FIGI": 100.5})
    assert len(to_close) == 0


@pytest.mark.asyncio
async def test_no_prices_sets_uncertain():
    client = FakeClient()
    mon = ExecutionMonitor(client)
    register_buy(mon)
    to_close = await mon.check_all({})
    assert client.uncertain_state is True
    assert len(to_close) == 0


@pytest.mark.asyncio
async def test_double_trigger_only_once():
    mon = ExecutionMonitor(FakeClient())
    register_buy(mon)
    first = await mon.check_all({"FIGI": 98.0})
    second = await mon.check_all({"FIGI": 98.0})
    assert len(first) == 1
    assert len(second) == 0


# --- register(**kwargs), активные позиции ---

def test_register_kwargs():
    mon = ExecutionMonitor(FakeClient())
    pos = mon.register(
        figi="F", ticker="T", direction="BUY",
        entry_price=100.0, quantity=2, sl_price=99.0, tp_price=102.0,
        lot_size=10, trade_id="abc", signal_reason="test", signal_time="t0",
        price_before_order=100.1, order_send_time="t1",
    )
    assert pos.figi == "F"
    assert pos.lot_size == 10
    assert pos.trade_id == "abc"
    assert pos.partial is False
    assert pos.logged is False
    assert mon.has_active_position("F") is True
    assert mon.active_count() == 1


def test_has_active_position_false_for_unknown():
    mon = ExecutionMonitor(FakeClient())
    assert mon.has_active_position("NOPE") is False


def test_has_active_position_true_for_uncertain():
    """UNCERTAIN должен блокировать новый вход (ТЗ п.2, п.A)."""
    mon = ExecutionMonitor(FakeClient())
    pos = register_buy(mon)
    pos.state = PositionState.UNCERTAIN
    assert mon.has_active_position("FIGI") is True
    assert mon.active_count() == 1


def test_has_active_position_false_for_closed():
    mon = ExecutionMonitor(FakeClient())
    pos = register_buy(mon)
    pos.state = PositionState.CLOSED
    assert mon.has_active_position("FIGI") is False
    assert mon.active_count() == 0


# --- resolve_closing ---

@pytest.mark.asyncio
async def test_resolve_closing_fill_full():
    client = FakeClient()
    mon = ExecutionMonitor(client)
    pos = register_buy(mon, qty=3)
    pos.state = PositionState.CLOSING
    pos.close_order_id = "ord-1"
    client.order_states["ord-1"] = make_state(1, 3, 300, 0)
    await mon.resolve_closing(pos)
    assert pos.state == PositionState.CLOSED
    assert pos.executed_lots == 3
    assert abs(pos.execution_price - 100.0) < 1e-9


@pytest.mark.asyncio
async def test_resolve_closing_partial_fill_becomes_uncertain():
    client = FakeClient()
    mon = ExecutionMonitor(client)
    pos = register_buy(mon, qty=3)
    pos.state = PositionState.CLOSING
    pos.close_order_id = "ord-2"
    client.order_states["ord-2"] = make_state(5, 2, 200, 0)
    await mon.resolve_closing(pos)
    assert pos.state == PositionState.UNCERTAIN


@pytest.mark.asyncio
async def test_resolve_closing_state_none_is_uncertain():
    client = FakeClient()
    mon = ExecutionMonitor(client)
    pos = register_buy(mon)
    pos.state = PositionState.CLOSING
    pos.close_order_id = "ord-3"
    await mon.resolve_closing(pos)
    assert pos.state == PositionState.UNCERTAIN


@pytest.mark.asyncio
async def test_resolve_closing_no_order_id_is_uncertain():
    mon = ExecutionMonitor(FakeClient())
    pos = register_buy(mon)
    pos.state = PositionState.CLOSING
    pos.close_order_id = None
    await mon.resolve_closing(pos)
    assert pos.state == PositionState.UNCERTAIN


@pytest.mark.asyncio
async def test_resolve_closing_rejected_returns_open():
    client = FakeClient()
    mon = ExecutionMonitor(client)
    pos = register_buy(mon)
    pos.state = PositionState.CLOSING
    pos.close_order_id = "ord-4"
    client.order_states["ord-4"] = make_state(2, 0, 0, 0)
    await mon.resolve_closing(pos)
    assert pos.state == PositionState.OPEN
    assert pos.close_order_id is None


@pytest.mark.asyncio
async def test_resolve_closing_idempotent():
    client = FakeClient()
    mon = ExecutionMonitor(client)
    pos = register_buy(mon, qty=1)
    pos.state = PositionState.CLOSING
    pos.close_order_id = "ord-5"
    client.order_states["ord-5"] = make_state(1, 1, 100, 0)

    await mon.resolve_closing(pos)
    assert pos.state == PositionState.CLOSED
    price_after_first = pos.execution_price

    await mon.resolve_closing(pos)
    assert pos.state == PositionState.CLOSED
    assert abs(pos.execution_price - price_after_first) < 1e-9
    assert mon.active_count() == 0


# --- _extract_price ---

def test_extract_price_divides_by_lots():
    st = make_state(1, 3, 300, 0)
    assert abs(ExecutionMonitor._extract_price(st, 3) - 100.0) < 1e-9


def test_extract_price_handles_none():
    class S:
        executed_order_price = None
    assert ExecutionMonitor._extract_price(S(), 1) is None