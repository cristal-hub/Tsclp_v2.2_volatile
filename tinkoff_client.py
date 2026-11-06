# tinkoff_client.py
# Tsclp_v2.1_volatile — клиент T-Invest API (ТОЛЬКО ПЕСОЧНИЦА).
#
# ДИАГНОСТИКА: get_last_prices() пишет в logs/price_monitor.log сырой ответ API
#              (FIGI запрошены / получены / price=None / price=0 / отсутствуют).
#              Результат функции (dict) НЕ меняется — торговая логика как раньше.
#              Ошибка записи лога не останавливает робота.
#
# ИЗМЕНЕНО: get_positions() возвращает None при ошибке (fail-closed).
# get_positions_debug() — диагностика (не трогаю).

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from t_tech.invest import (
    CandleInterval,
    InstrumentIdType,
    OrderDirection,
    OrderType,
    StopOrderDirection,
    StopOrderExpirationType,
    StopOrderType,
)
from t_tech.invest.sandbox.async_client import AsyncSandboxClient
from t_tech.invest.utils import now, quotation_to_decimal, decimal_to_quotation

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Диагностический лог цен ---
PRICE_MONITOR_DIR = "logs"
PRICE_MONITOR_FILE = os.path.join(PRICE_MONITOR_DIR, "price_monitor.log")


def _price_monitor_write(lines: list) -> None:
    """Пишет блок строк в logs/price_monitor.log (append). Не падает при ошибке."""
    try:
        os.makedirs(PRICE_MONITOR_DIR, exist_ok=True)
        with open(PRICE_MONITOR_FILE, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
            f.write("\n")
    except Exception as e:
        logging.warning(f"[price_monitor] Не удалось записать лог: {e}")


class SandboxRequiredError(RuntimeError):
    pass


class TinkoffClient:
    def __init__(self, token: str, use_sandbox: bool = True):
        if not use_sandbox:
            raise SandboxRequiredError("Tsclp_v2.1_volatile работает ТОЛЬКО в песочнице.")
        self.token = token
        self.use_sandbox = use_sandbox
        self._sandbox_client = None
        self.client = None
        self.account_id: Optional[str] = None
        self.instrument_info_cache = {}
        self.uncertain_state = False

    async def __aenter__(self):
        last_exc = None
        for attempt in range(config.API_RETRY_COUNT):
            try:
                self._sandbox_client = AsyncSandboxClient(self.token)
                self.client = await self._sandbox_client.__aenter__()
                logging.info("Подключение к T-Invest SANDBOX API успешно.")
                await self._ensure_account()
                return self
            except Exception as e:
                last_exc = e
                logging.error(f"Ошибка подключения (попытка {attempt + 1}): {e}")
                if attempt < config.API_RETRY_COUNT - 1:
                    await asyncio.sleep(config.API_RETRY_DELAY_SECONDS)
        raise RuntimeError(f"Не удалось подключиться: {last_exc}")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._sandbox_client:
            try:
                await self._sandbox_client.__aexit__(exc_type, exc_val, exc_tb)
            except Exception as e:
                logging.warning(f"Ошибка закрытия: {e}")

    async def _ensure_account(self):
        if config.TINKOFF_ACCOUNT_ID:
            self.account_id = config.TINKOFF_ACCOUNT_ID
            return
        accounts = await self.client.users.get_accounts()
        if accounts.accounts:
            self.account_id = accounts.accounts[0].id
        else:
            resp = await self.client.sandbox.open_sandbox_account()
            self.account_id = resp.account_id

    async def get_all_tradable_shares(self):
        try:
            instruments = await self.client.instruments.shares()
            return [
                s for s in instruments.instruments
                if (s.exchange or "").upper() == "MOEX"
                and (s.currency or "").lower() == "rub"
                and s.buy_available_flag and s.sell_available_flag
            ]
        except Exception as e:
            logging.error(f"Ошибка списка акций: {e}")
            return []

    async def get_historical_candles(self, figi, days, interval=CandleInterval.CANDLE_INTERVAL_5_MIN):
        try:
            await asyncio.sleep(config.API_CALL_DELAY_SECONDS)
            candles = []
            async for candle in self.client.get_all_candles(
                figi=figi, from_=now() - timedelta(days=days), interval=interval,
            ):
                candles.append(candle)
            return candles
        except Exception as e:
            logging.error(f"Ошибка свечей {figi}: {e}")
            return []

    async def get_last_prices(self, figi_list):
        """
        Получает последние цены. ДИАГНОСТИКА: пишет в logs/price_monitor.log
        сырой ответ API. Результат (dict) НЕ меняется.
        """
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        requested = list(figi_list)
        try:
            await asyncio.sleep(config.API_CALL_DELAY_SECONDS)
            response = await self.client.market_data.get_last_prices(figi=requested)

            # --- ДИАГНОСТИКА: разбираем сырой ответ ДО преобразования в dict ---
            raw_items = list(getattr(response, "last_prices", []) or [])
            returned_figis = []
            none_figis = []
            zero_figis = []
            valid_prices = {}
            for p in raw_items:
                pfigi = getattr(p, "figi", None)
                if pfigi is None:
                    continue
                returned_figis.append(pfigi)
                price = getattr(p, "price", None)
                if price is None:
                    none_figis.append(pfigi)
                    continue
                try:
                    val = float(quotation_to_decimal(price))
                except Exception:
                    none_figis.append(pfigi)
                    continue
                if val == 0.0:
                    zero_figis.append(pfigi)
                else:
                    valid_prices[pfigi] = val

            missing = [f for f in requested if f not in returned_figis]

            lines = [
                f"[{ts}] PRICE_CHECK",
                f"Requested: {len(requested)}",
                f"Returned: {len(returned_figis)}",
                f"Valid prices: {len(valid_prices)}",
                f"Missing FIGI: {len(missing)}",
            ]
            if missing:
                lines.append(f"Missing list: {', '.join(missing)}")
            if none_figis:
                lines.append(f"Price=None FIGI: {', '.join(none_figis)}")
            if zero_figis:
                lines.append(f"Price=0 FIGI: {', '.join(zero_figis)}")
            if not missing and not none_figis and not zero_figis:
                lines.append("Status: OK (all requested FIGI have valid prices)")
            _price_monitor_write(lines)

            # --- РЕЗУЛЬТАТ (как раньше) ---
            return {
                p.figi: float(quotation_to_decimal(p.price))
                for p in raw_items if getattr(p, "price", None) is not None
            }
        except Exception as e:
            logging.error(f"Ошибка цен: {e}")
            _price_monitor_write([
                f"[{ts}] PRICE_CHECK_ERROR",
                f"Requested: {len(requested)}",
                f"Exception: {type(e).__name__}: {e!r}",
            ])
            return {}

    async def get_instrument_info(self, figi):
        if figi in self.instrument_info_cache:
            return self.instrument_info_cache[figi]
        try:
            response = await self.client.instruments.get_instrument_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=figi,
            )
            instrument = response.instrument
            inc = quotation_to_decimal(instrument.min_price_increment)
            info = {
                'lot': int(instrument.lot or 1),
                'min_price_increment': float(inc),
                'ticker': instrument.ticker,
                'name': instrument.name,
            }
            self.instrument_info_cache[figi] = info
            return info
        except Exception as e:
            logging.error(f"Ошибка info {figi}: {e}")
            return None

    async def get_account_balance(self):
        try:
            portfolio = await self.client.sandbox.get_sandbox_portfolio(account_id=self.account_id)
            return float(quotation_to_decimal(portfolio.total_amount_portfolio))
        except Exception as e:
            logging.error(f"Ошибка баланса: {e}")
            return 0.0

    async def get_positions(self) -> Optional[dict]:
        """
        Fail-closed: None при ошибке API (состояние НЕИЗВЕСТНО),
        {} — только при успешном ответе с пустым портфелем.
        """
        try:
            portfolio = await self.client.sandbox.get_sandbox_portfolio(account_id=self.account_id)
            result = {}
            for pos in portfolio.positions:
                qty = float(quotation_to_decimal(pos.quantity))
                if qty != 0:
                    result[pos.figi] = qty
            return result
        except Exception as e:
            logging.error(f"Ошибка позиций (состояние неизвестно): {e}")
            self.uncertain_state = True
            return None

    async def get_positions_debug(self):
        """ДИАГНОСТИКА — не используется в торговой логике."""
        try:
            portfolio = await self.client.sandbox.get_sandbox_portfolio(account_id=self.account_id)
            result = []
            for pos in portfolio.positions:
                info = await self.get_instrument_info(pos.figi) if pos.figi else None
                q_raw = getattr(pos, "quantity", None)
                ql_raw = getattr(pos, "quantity_lots", None)
                try:
                    q_dec = float(quotation_to_decimal(q_raw)) if q_raw is not None else None
                except Exception:
                    q_dec = None
                try:
                    ql_dec = float(quotation_to_decimal(ql_raw)) if ql_raw is not None else None
                except Exception:
                    ql_dec = None
                result.append({
                    "figi": pos.figi,
                    "ticker": getattr(pos, "ticker", None),
                    "instrument_type": getattr(pos, "instrument_type", None),
                    "quantity_raw": str(q_raw),
                    "quantity_lots_raw": str(ql_raw),
                    "quantity_dec": q_dec,
                    "quantity_lots_dec": ql_dec,
                    "lot_size": info["lot"] if info else None,
                })
            return result
        except Exception as e:
            logging.error(f"Ошибка диагностики позиций: {e}")
            return []

    async def post_market_order(self, figi, quantity, direction):
        order_id = str(uuid.uuid4())
        try:
            response = await self.client.sandbox.post_sandbox_order(
                account_id=self.account_id, figi=figi, quantity=quantity,
                direction=direction, order_type=OrderType.ORDER_TYPE_MARKET,
                order_id=order_id,
            )
            return response
        except Exception as e:
            logging.error(f"Ошибка заявки {figi}: {e}")
            self.uncertain_state = True
            return None

    async def get_sandbox_order_state(self, order_id: str):
        try:
            return await self.client.sandbox.get_sandbox_order_state(
                account_id=self.account_id, order_id=order_id,
            )
        except Exception as e:
            logging.error(f"Ошибка статуса заявки {order_id}: {e}")
            return None

    def is_uncertain(self) -> bool:
        return self.uncertain_state