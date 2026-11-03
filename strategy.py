
# strategy.py
# Tsclp_v2.0 — стратегия.
#
# ИЗМЕНЕНИЯ ПО СРАВНЕНИЮ С ПРЕДЫДУЩЕЙ ВЕРСИЕЙ:
# 1. ADX hysteresis подтверждается только на новых свечах,
#    а не на повторных вызовах get_market_regime().
# 2. Счётчик подтверждений сбрасывается при отмене кандидата.
# 3. Первоначальное определение режима при запуске — немедленное.
# 4. is_complete=None/NaN не считается признаком незакрытой свечи.
#    Удаляется только свеча с явно установленным False.
#
# ТОРГОВЫЕ УСЛОВИЯ НЕ ИЗМЕНЕНЫ:
# MA 9/21, BB+RSI, ADX 25, MA100, объём 1.5x.
#
# RESEARCH ONLY
# ONLY T-INVEST SANDBOX
# NO LIVE ORDERS
# NO REAL MONEY

import logging
from typing import Optional, Tuple

import pandas as pd
import pandas_ta_classic as ta

import config


class StrategyManager:
    def __init__(self):
        self.min_candles_required = config.MIN_CANDLES_FOR_SIGNAL

        self._last_regime = "UNKNOWN"
        self._regime_streak = 0

        # Состояние подтверждения смены режима.
        self._last_regime_candidate = None
        self._last_regime_candle_time = None

        # Диагностические значения последнего расчёта.
        self.last_adx_value = None
        self.last_signal_candle_time = None

    def get_market_regime(self, candles_df: pd.DataFrame) -> str:
        """
        Определяет режим рынка по ADX с гистерезисом.

        ВАЖНО:
        Подтверждение смены режима засчитывается только один раз
        на уникальную свечу. Повторный вызов функции на том же
        наборе свечей не увеличивает _regime_streak.
        """
        if candles_df is None or candles_df.empty:
            return "UNKNOWN"

        try:
            if len(candles_df) < config.ADX_PERIOD + 1:
                return "UNKNOWN"

            adx_data = ta.adx(
                high=candles_df['high'],
                low=candles_df['low'],
                close=candles_df['close'],
                length=config.ADX_PERIOD,
            )

            if adx_data is None or adx_data.empty:
                return "UNKNOWN"

            adx_col = f"ADX_{config.ADX_PERIOD}"

            if adx_col not in adx_data.columns:
                candidates = adx_data.filter(like='ADX').columns

                if len(candidates) == 0:
                    return "UNKNOWN"

                adx_col = candidates[0]

            recent = (
                adx_data[adx_col]
                .dropna()
                .tail(config.ADX_HYSTERESIS_CANDLES)
            )

            if len(recent) < config.ADX_HYSTERESIS_CANDLES:
                return "UNKNOWN"

            self.last_adx_value = float(recent.iloc[-1])

            above = all(
                value > config.ADX_TREND_THRESHOLD
                for value in recent
            )

            candidate = "TREND" if above else "RANGE"

            # Временная метка последней свечи.
            candle_time = candles_df.index[-1]

            # Первый режим после запуска определяется сразу.
            if self._last_regime == "UNKNOWN":
                self._last_regime = candidate
                self._regime_streak = 0
                self._last_regime_candidate = candidate
                self._last_regime_candle_time = candle_time

                logging.info(
                    f"[ADX REGIME] Initial regime: {candidate}, "
                    f"ADX={self.last_adx_value:.2f}"
                )

                return self._last_regime

            # Повторный вызов на той же свече.
            # Повторно засчитывать подтверждение запрещено.
            if candle_time == self._last_regime_candle_time:

                # Если данные той же свечи изменились и кандидат
                # перестал совпадать с предыдущим кандидатом,
                # сбрасываем незавершённую серию подтверждений.
                if candidate != self._last_regime_candidate:
                    self._last_regime_candidate = candidate
                    self._regime_streak = 0

                return self._last_regime

            # Новая свеча: запоминаем её время.
            self._last_regime_candle_time = candle_time

            # Кандидат совпадает с текущим режимом:
            # незавершённая попытка смены режима отменяется.
            if candidate == self._last_regime:
                self._last_regime_candidate = candidate
                self._regime_streak = 0

                return self._last_regime

            # Кандидат отличается от текущего режима.
            if candidate != self._last_regime_candidate:
                self._last_regime_candidate = candidate
                self._regime_streak = 1
            else:
                self._regime_streak += 1

            required = config.ADX_HYSTERESIS_CANDLES

            logging.info(
                f"[ADX REGIME] Candidate={candidate}, "
                f"current={self._last_regime}, "
                f"confirmation={self._regime_streak}/{required}, "
                f"ADX={self.last_adx_value:.2f}"
            )

            # Смена режима только после нужного количества
            # последовательных новых свечей.
            if self._regime_streak >= required:
                old_regime = self._last_regime

                self._last_regime = candidate
                self._regime_streak = 0
                self._last_regime_candidate = candidate

                logging.info(
                    f"[ADX REGIME] Changed: "
                    f"{old_regime} -> {self._last_regime}"
                )

            return self._last_regime

        except Exception as e:
            logging.error(f"ADX error: {e}")
            return "UNKNOWN"

    def get_signal(
        self,
        candles_df: pd.DataFrame,
        last_price: float,
        drop_incomplete: bool = False,
    ) -> Tuple[str, str]:
        """
        Возвращает (signal, reason).

        signal ∈ {"BUY", "SELL", "HOLD"}.

        drop_incomplete:
        Если True — удаляет последнюю свечу только тогда,
        когда is_complete явно равен False.
        Неизвестное значение None/NaN не считается False.
        """
        prepared = self._prepare_dataframe(
            candles_df,
            drop_incomplete=drop_incomplete,
        )

        if prepared is None or len(prepared) < self.min_candles_required:
            return "HOLD", "Недостаточно данных"

        self.last_signal_candle_time = prepared.index[-1]

        regime = self.get_market_regime(prepared)

        if regime == "TREND":
            signal, reason = self._ma_crossover_signal(prepared)

        elif regime == "RANGE":
            signal, reason = self._bb_rsi_signal(prepared)

        else:
            return "HOLD", "Режим не определён"

        if signal in {"BUY", "SELL"}:
            ok, why = self._volume_confirmation(prepared)

            if not ok:
                return "HOLD", f"Отклонено объёмом ({why})"

        return signal, reason

    def _trend_filter_ok(
        self,
        candles_df: pd.DataFrame,
        signal: str,
    ) -> bool:
        """
        При недостатке данных для MA100 сигнал запрещён.
        Торговое условие сохранено без изменений.
        """
        if len(candles_df) < config.TREND_FILTER_MA_PERIOD:
            return False

        ma = ta.sma(
            candles_df['close'],
            length=config.TREND_FILTER_MA_PERIOD,
        )

        if ma is None or ma.empty or pd.isna(ma.iloc[-1]):
            return False

        last_ma = ma.iloc[-1]
        last_close = candles_df['close'].iloc[-1]

        if signal == "BUY" and last_close < last_ma:
            return False

        if signal == "SELL" and last_close > last_ma:
            return False

        return True

    def _ma_crossover_signal(
        self,
        candles_df: pd.DataFrame,
    ) -> Tuple[str, str]:
        try:
            if len(candles_df) < config.MA_SLOW_PERIOD + 2:
                return "HOLD", "Мало данных для MA"

            fast = ta.sma(
                candles_df['close'],
                length=config.MA_FAST_PERIOD,
            )

            slow = ta.sma(
                candles_df['close'],
                length=config.MA_SLOW_PERIOD,
            )

            if fast is None or slow is None:
                return "HOLD", "MA не рассчитаны"

            lf, ls = fast.iloc[-1], slow.iloc[-1]
            pf, ps = fast.iloc[-2], slow.iloc[-2]

            if any(pd.isna(x) for x in (lf, ls, pf, ps)):
                return "HOLD", "MA NaN"

            logging.info(
                f"[DEBUG MA] fast_prev={pf:.4f} "
                f"fast_last={lf:.4f} "
                f"slow_prev={ps:.4f} "
                f"slow_last={ls:.4f} "
                f"len={len(candles_df)}"
            )

            if pf < ps and lf > ls:
                if not self._trend_filter_ok(candles_df, "BUY"):
                    return "HOLD", "MA↑, но фильтр MA100 не пройден"

                return (
                    "BUY",
                    f"MA({config.MA_FAST_PERIOD}/{config.MA_SLOW_PERIOD}) ↑",
                )

            if pf > ps and lf < ls:
                if not self._trend_filter_ok(candles_df, "SELL"):
                    return "HOLD", "MA↓, но фильтр MA100 не пройден"

                return (
                    "SELL",
                    f"MA({config.MA_FAST_PERIOD}/{config.MA_SLOW_PERIOD}) ↓",
                )

            return "HOLD", "Нет пересечения MA"

        except Exception as e:
            logging.error(f"MA error: {e}")
            return "HOLD", "Ошибка MA"

    def _bb_rsi_signal(
        self,
        candles_df: pd.DataFrame,
    ) -> Tuple[str, str]:
        try:
            if len(candles_df) < config.BB_PERIOD + config.RSI_PERIOD:
                return "HOLD", "Мало данных для BB+RSI"

            bb = ta.bbands(
                candles_df['close'],
                length=config.BB_PERIOD,
                std=config.BB_STD_DEV,
            )

            rsi = ta.rsi(
                candles_df['close'],
                length=config.RSI_PERIOD,
            )

            if (
                bb is None
                or rsi is None
                or bb.empty
                or rsi.empty
            ):
                return "HOLD", "BB/RSI не рассчитаны"

            bbl_cols = bb.filter(like='BBL').columns
            bbu_cols = bb.filter(like='BBU').columns

            if len(bbl_cols) == 0 or len(bbu_cols) == 0:
                return "HOLD", "Колонки BB не найдены"

            lower = bb[bbl_cols[0]].iloc[-1]
            upper = bb[bbu_cols[0]].iloc[-1]
            rsi_val = rsi.iloc[-1]
            last_close = candles_df['close'].iloc[-1]

            if any(pd.isna(x) for x in (lower, upper, rsi_val)):
                return "HOLD", "BB/RSI NaN"

            if (
                last_close < lower
                and rsi_val < config.RSI_OVERSOLD
            ):
                if not self._trend_filter_ok(candles_df, "BUY"):
                    return (
                        "HOLD",
                        f"BB↓ RSI={rsi_val:.1f}, "
                        "фильтр MA100 не пройден",
                    )

                return (
                    "BUY",
                    f"BB↓ RSI={rsi_val:.1f}<{config.RSI_OVERSOLD}",
                )

            if (
                last_close > upper
                and rsi_val > config.RSI_OVERBOUGHT
            ):
                if not self._trend_filter_ok(candles_df, "SELL"):
                    return (
                        "HOLD",
                        f"BB↑ RSI={rsi_val:.1f}, "
                        "фильтр MA100 не пройден",
                    )

                return (
                    "SELL",
                    f"BB↑ RSI={rsi_val:.1f}>{config.RSI_OVERBOUGHT}",
                )

            return "HOLD", "Нет сигнала BB+RSI"

        except Exception as e:
            logging.error(f"BB+RSI error: {e}")
            return "HOLD", "Ошибка BB+RSI"

    def _prepare_dataframe(
        self,
        candles_df: pd.DataFrame,
        drop_incomplete: bool = False,
    ) -> Optional[pd.DataFrame]:
        if candles_df is None or candles_df.empty:
            return None

        df = candles_df.copy()
        df = df.sort_index()
        df = df[~df.index.duplicated(keep='last')]

        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col],
                    errors='coerce',
                )

        df = df.dropna(subset=['high', 'low', 'close'])

        # Удаляем последнюю свечу только при явном False.
        # None/NaN означает, что статус закрытия неизвестен.
        if (
            drop_incomplete
            and 'is_complete' in df.columns
            and not df.empty
        ):
            completion_value = df['is_complete'].iloc[-1]

            if (
                pd.notna(completion_value)
                and not bool(completion_value)
            ):
                df = df.iloc[:-1]

        return df if not df.empty else None

    def _volume_confirmation(
        self,
        candles_df: pd.DataFrame,
    ) -> Tuple[bool, Optional[str]]:
        if (
            'volume' not in candles_df.columns
            or candles_df['volume'].isna().all()
        ):
            return False, "нет данных об объёме"

        window = min(
            config.VOLUME_CONFIRMATION_WINDOW,
            len(candles_df),
        )

        if window < 3:
            return False, "мало свечей для объёма"

        recent = candles_df['volume'].tail(window)

        avg = recent[:-1].mean()
        last = recent.iloc[-1]

        if pd.isna(avg) or pd.isna(last):
            return False, "некорректные данные об объёме"

        if avg <= 0:
            return False, "avg volume = 0"

        if last >= avg * config.VOLUME_SPIKE_FACTOR:
            return True, None

        return (
            False,
            f"объём {last:.0f} < "
            f"{avg * config.VOLUME_SPIKE_FACTOR:.0f}",
        )
