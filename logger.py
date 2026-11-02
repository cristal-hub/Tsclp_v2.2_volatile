# logger.py
# Tsclp_v2.1_volatile — журнал событий и сделок.
# ИЗМЕНЕНО: log_trade() возвращает bool; _ensure_files() защищён от ошибок
#           (не падает при недоступной директории); log_event не падает.

import csv
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import config

EVENTS_FILE = "research_events.jsonl"
TRADES_FILE = "research_trades.csv"

TRADE_FIELDS = [
    "trade_id", "timestamp_utc", "ticker", "figi", "lot_size",
    "side", "signal_reason", "signal_time",
    "price_before_order", "order_send_time", "order_id",
    "execution_status", "requested_lots", "executed_lots",
    "execution_price", "exit_reason", "exit_price", "exit_time",
    "commission_rub", "commission_source", "actual_commission_rub",
    "entry_commission_rub", "exit_commission_rub",
    "slippage_rub", "slippage_percent",
    "gross_pnl_rub", "net_pnl_rub", "cumulative_net_pnl_rub",
    "error_note",
]


def _file_is_empty_or_header_only(path: str) -> bool:
    if not os.path.isfile(path):
        return True
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f, delimiter=";")
            rows = list(reader)
        return len(rows) <= 1
    except Exception:
        return False


def _ensure_files() -> bool:
    """Создаёт файлы при необходимости. Возвращает True при успехе."""
    try:
        if not os.path.isfile(EVENTS_FILE):
            with open(EVENTS_FILE, "w", encoding="utf-8"):
                pass
        if not os.path.isfile(TRADES_FILE) or _file_is_empty_or_header_only(TRADES_FILE):
            with open(TRADES_FILE, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=TRADE_FIELDS, delimiter=";")
                writer.writeheader()
        return True
    except Exception as e:
        logging.error(f"[logger] Не удалось подготовить файлы: {e}")
        return False


def log_event(event_type: str, payload: dict) -> bool:
    """Записывает событие в JSONL. Возвращает True при успехе."""
    if not _ensure_files():
        return False
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "config_profile": getattr(config, "PROFILE", "?"),
        "commission_mode": getattr(config, "COMMISSION_MODE", "?"),
        **payload,
    }
    try:
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        logging.error(f"[logger] Не удалось записать событие: {e}")
        return False


def log_trade(trade: dict) -> bool:
    """Записывает сделку в CSV. Возвращает True при успехе, False при ошибке."""
    if not _ensure_files():
        return False
    row = {field: trade.get(field, "") for field in TRADE_FIELDS}
    try:
        with open(TRADES_FILE, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_FIELDS, delimiter=";")
            writer.writerow(row)
        return True
    except Exception as e:
        logging.error(f"[logger] Не удалось записать сделку: {e}")
        return False


def get_cumulative_net_pnl() -> float:
    if not os.path.isfile(TRADES_FILE):
        return 0.0
    total = 0.0
    try:
        with open(TRADES_FILE, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                v = row.get("net_pnl_rub", "")
                if not v:
                    continue
                try:
                    total += float(v.replace(",", ".").replace(" ", ""))
                except (ValueError, AttributeError):
                    continue
    except Exception as e:
        logging.warning(f"[logger] Не удалось посчитать cumulative: {e}")
    return total