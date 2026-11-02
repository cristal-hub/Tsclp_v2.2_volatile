# tests/test_commission.py
# Tsclp_v2.1_volatile — тесты извлечения комиссии из OrderState.
# Контракт: если executed_commission есть и > 0 → факт; иначе None (модель).

import pytest
from execution import ExecutionMonitor


class FakeMoney:
    def __init__(self, units, nano):
        self.units = units
        self.nano = nano


class FakeState:
    def __init__(self, commission=None, price=None, lots=1, status=1):
        self.executed_commission = commission
        self.executed_order_price = price
        self.lots_executed = lots
        self.execution_report_status = status


def test_extract_commission_positive():
    st = FakeState(commission=FakeMoney(1, 500_000_000))  # 1.5
    assert ExecutionMonitor._extract_commission(st) == pytest.approx(1.5)


def test_extract_commission_zero_units_but_nano():
    st = FakeState(commission=FakeMoney(0, 250_000_000))  # 0.25
    assert ExecutionMonitor._extract_commission(st) == pytest.approx(0.25)


def test_extract_commission_none_returns_none():
    st = FakeState(commission=None)
    assert ExecutionMonitor._extract_commission(st) is None


def test_extract_commission_zero_returns_none():
    st = FakeState(commission=FakeMoney(0, 0))
    assert ExecutionMonitor._extract_commission(st) is None


def test_extract_commission_negative_nano_abs():
    # API иногда отдаёт отрицательный nano — берём abs.
    st = FakeState(commission=FakeMoney(0, -300_000_000))  # -0.3
    assert ExecutionMonitor._extract_commission(st) == pytest.approx(0.3)


def test_extract_commission_missing_attr_returns_none():
    class Broken:
        pass
    assert ExecutionMonitor._extract_commission(Broken()) is None


def test_extract_commission_broken_money_no_units():
    class BrokenMoney:
        pass

    class State:
        executed_commission = BrokenMoney()

    assert ExecutionMonitor._extract_commission(State()) is None


def test_extract_commission_and_price_independent():
    """Комиссия есть, цены нет — оба извлекаются независимо."""
    st = FakeState(commission=FakeMoney(2, 0), price=None, lots=1)
    assert ExecutionMonitor._extract_commission(st) == pytest.approx(2.0)
    assert ExecutionMonitor._extract_price(st, 1) is None


def test_extract_price_with_commission_present():
    """Цена есть, комиссия есть — оба извлекаются."""
    st = FakeState(
        commission=FakeMoney(1, 0),
        price=FakeMoney(300, 0),
        lots=1,
    )
    assert ExecutionMonitor._extract_price(st, 1) == pytest.approx(300.0)
    assert ExecutionMonitor._extract_commission(st) == pytest.approx(1.0)


def test_extract_commission_large_value():
    st = FakeState(commission=FakeMoney(1234, 560_000_000))  # 1234.56
    assert ExecutionMonitor._extract_commission(st) == pytest.approx(1234.56)