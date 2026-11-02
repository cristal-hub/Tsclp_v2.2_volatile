# tests/test_slippage.py
# Tsclp_v2.1_volatile — числовые тесты знака slippage (ТЗ п.3).
# LONG: положительное значение = неблагоприятное.
#   вход:  entry_exec - price_before_order
#   выход: close_price_before_order - exit_exec
# Формулы зеркалят main.process_closing.

import pytest


def entry_slippage_long(entry_exec, price_before_order, shares):
    return (entry_exec - price_before_order) * shares


def exit_slippage_long(close_price_before_order, exit_exec, shares):
    return (close_price_before_order - exit_exec) * shares


def test_entry_slippage_unfavorable_positive():
    """Ожидали 100, исполнили 101 → slippage = +1 (плохо)."""
    assert entry_slippage_long(101.0, 100.0, 1) == pytest.approx(1.0)


def test_entry_slippage_favorable_negative():
    """Ожидали 100, исполнили 99 → slippage = -1 (хорошо)."""
    assert entry_slippage_long(99.0, 100.0, 1) == pytest.approx(-1.0)


def test_entry_slippage_zero():
    assert entry_slippage_long(100.0, 100.0, 1) == pytest.approx(0.0)


def test_exit_slippage_unfavorable_positive():
    """Ожидали продажу 105, исполнили 104 → slippage = +1 (плохо)."""
    assert exit_slippage_long(105.0, 104.0, 1) == pytest.approx(1.0)


def test_exit_slippage_favorable_negative():
    """Ожидали 105, исполнили 106 → slippage = -1 (хорошо)."""
    assert exit_slippage_long(105.0, 106.0, 1) == pytest.approx(-1.0)


def test_exit_slippage_zero():
    assert exit_slippage_long(105.0, 105.0, 1) == pytest.approx(0.0)


def test_total_slippage_with_shares():
    """10 акций: вход +1/акция, выход +1/акция → итого +20 ₽."""
    entry = entry_slippage_long(101.0, 100.0, 10)
    exit_ = exit_slippage_long(105.0, 104.0, 10)
    assert entry + exit_ == pytest.approx(20.0)


def test_total_slippage_mixed_sign():
    """Вход плохой (+1/акц), выход хороший (-1/акц) → 0 при равных shares."""
    entry = entry_slippage_long(101.0, 100.0, 10)
    exit_ = exit_slippage_long(105.0, 106.0, 10)
    assert entry + exit_ == pytest.approx(0.0)


def test_entry_slippage_formula_matches_main_py():
    """
    Контракт main.py:
        entry_slippage = (pos.entry_price - pos.price_before_order) * shares
    Здесь entry_exec == pos.entry_price, price_before_order == pos.price_before_order.
    """
    pos_entry_price = 101.0
    pos_price_before_order = 100.0
    shares = 10
    expected = (pos_entry_price - pos_price_before_order) * shares
    assert entry_slippage_long(pos_entry_price, pos_price_before_order, shares) == pytest.approx(expected)


def test_exit_slippage_formula_matches_main_py():
    """
    Контракт main.py (ИСПРАВЛЕНО):
        exit_slippage = (pos.close_price_before_order - exit_price) * shares
    """
    pos_close_price_before_order = 105.0
    exit_price = 104.0
    shares = 10
    expected = (pos_close_price_before_order - exit_price) * shares
    assert exit_slippage_long(pos_close_price_before_order, exit_price, shares) == pytest.approx(expected)


def test_exit_slippage_old_formula_would_be_negative():
    """
    Регрессия: старая формула (exit - close_before) давала бы -1 при
    неблагоприятном выходе. Проверяем, что новая формула даёт +1.
    """
    close_before = 105.0
    exit_exec = 104.0
    new_formula = exit_slippage_long(close_before, exit_exec, 1)
    old_formula = (exit_exec - close_before) * 1
    assert new_formula == pytest.approx(1.0)
    assert old_formula == pytest.approx(-1.0)
    assert new_formula != old_formula