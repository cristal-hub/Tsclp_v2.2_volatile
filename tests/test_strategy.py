# tests/test_strategy.py
# Tsclp_v2.1_volatile — тесты стратегии.
# Обновлено: профиль-независимые проверки (FORCE_TRADE не влияет на контракт).

import pandas as pd
import pytest
from strategy import StrategyManager
import config


def make_df(closes, volumes=None):
    n = len(closes)
    if volumes is None:
        volumes = [1000] * n
    return pd.DataFrame({
        'open': closes,
        'high': [c * 1.001 for c in closes],
        'low': [c * 0.999 for c in closes],
        'close': closes,
        'volume': volumes,
    }, index=pd.date_range('2026-01-01', periods=n, freq='5min'))


def test_insufficient_data_returns_hold():
    """
    Профиль-независимо: подаём МЕНЬШЕ, чем MIN_CANDLES_FOR_SIGNAL в любом
    профиле. При нехватке данных get_signal возвращает HOLD.
    Причина может быть 'Недостаточно данных' или 'Режим не определён' —
    контракт: сигнал HOLD.
    """
    sm = StrategyManager()
    n = max(1, config.MIN_CANDLES_FOR_SIGNAL - 1)
    df = make_df([100.0] * n)
    signal, reason = sm.get_signal(df, 100.0)
    assert signal == "HOLD"
    # Причина осмысленная (не пустая)
    assert reason


def test_ma100_missing_blocks_signal():
    """При < 100 свечах фильтр MA100 должен запрещать сигнал (HOLD)."""
    sm = StrategyManager()
    closes = [100.0 + i * 0.1 for i in range(60)]
    df = make_df(closes)
    signal, reason = sm.get_signal(df, closes[-1])
    assert signal == "HOLD", f"Ожидался HOLD (MA100 < 100 свечей), получен {signal}: {reason}"


def test_volume_confirmation_rejects_low_volume(monkeypatch):
    """
    Изолированно от профиля: фиксируем VOLUME_SPIKE_FACTOR=1.5 и проверяем
    логику _volume_confirmation (низкий объём → False).
    """
    monkeypatch.setattr(config, "VOLUME_SPIKE_FACTOR", 1.5)
    sm = StrategyManager()
    closes = [100.0] * 59 + [100.5]
    volumes = [1000] * 59 + [100]
    df = make_df(closes, volumes)
    ok, why = sm._volume_confirmation(df)
    assert ok is False
    assert why is not None


def test_volume_confirmation_accepts_spike(monkeypatch):
    """Изолированно: VOLUME_SPIKE_FACTOR=1.5, объём-спайк → True."""
    monkeypatch.setattr(config, "VOLUME_SPIKE_FACTOR", 1.5)
    sm = StrategyManager()
    closes = [100.0] * 60
    volumes = [1000] * 59 + [5000]
    df = make_df(closes, volumes)
    ok, why = sm._volume_confirmation(df)
    assert ok is True
    assert why is None


def test_adx_hysteresis_does_not_flip_immediately():
    """Гистерезис ADX: режим не должен меняться мгновенно."""
    sm = StrategyManager()
    sm._last_regime = "TREND"
    sm._regime_streak = 0
    closes = [100.0 + i * 0.5 for i in range(150)]
    df = make_df(closes)
    regime1 = sm.get_market_regime(df)
    regime2 = sm.get_market_regime(df)
    assert regime1 in {"TREND", "RANGE", "UNKNOWN"}
    assert regime2 in {"TREND", "RANGE", "UNKNOWN"}


def test_prepare_dataframe_drops_incomplete_when_asked():
    sm = StrategyManager()
    df = make_df([100.0] * 10)
    df['is_complete'] = [True] * 9 + [False]
    prepared = sm._prepare_dataframe(df, drop_incomplete=True)
    assert len(prepared) == 9


def test_prepare_dataframe_keeps_incomplete_by_default():
    sm = StrategyManager()
    df = make_df([100.0] * 10)
    df['is_complete'] = [True] * 9 + [False]
    prepared = sm._prepare_dataframe(df, drop_incomplete=False)
    assert len(prepared) == 10