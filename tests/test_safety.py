# tests/test_safety.py
# Tsclp_v2.1_volatile — тесты безопасности.
# Обновлено: self.use_sandbox в TinkoffClient; FakeUser с username.

import pytest
from tinkoff_client import TinkoffClient, SandboxRequiredError


def test_sandbox_required():
    with pytest.raises(SandboxRequiredError):
        TinkoffClient("fake", use_sandbox=False)


def test_sandbox_default_ok():
    client = TinkoffClient("fake", use_sandbox=True)
    assert client.use_sandbox is True


def test_sandbox_default_is_true():
    """По умолчанию use_sandbox=True — песочница обязательна."""
    client = TinkoffClient("fake")
    assert client.use_sandbox is True


def test_telegram_auth_rejects_unknown():
    import telegram_bot

    class FakeUser:
        def __init__(self, uid, username="user"):
            self.id = uid
            self.username = username

    class FakeMessage:
        text = "/start"

    class FakeUpdate:
        def __init__(self, uid):
            self.effective_user = FakeUser(uid)
            self.message = FakeMessage()

    bot = telegram_bot.TelegramBot.__new__(telegram_bot.TelegramBot)
    bot.allowed_user_id = 12345
    assert bot._authorized(FakeUpdate(12345)) is True
    assert bot._authorized(FakeUpdate(99999)) is False


def test_telegram_auth_rejects_when_not_set():
    import telegram_bot

    class FakeUser:
        id = 12345
        username = "user"

    class FakeMessage:
        text = "/start"

    class FakeUpdate:
        effective_user = FakeUser()
        message = FakeMessage()

    bot = telegram_bot.TelegramBot.__new__(telegram_bot.TelegramBot)
    bot.allowed_user_id = None
    assert bot._authorized(FakeUpdate()) is False


def test_telegram_auth_rejects_no_user():
    import telegram_bot

    class FakeMessage:
        text = "/start"

    class FakeUpdate:
        effective_user = None
        message = FakeMessage()

    bot = telegram_bot.TelegramBot.__new__(telegram_bot.TelegramBot)
    bot.allowed_user_id = 12345
    assert bot._authorized(FakeUpdate()) is False


# --- UNCERTAIN запрещает новые входы (ТЗ п.14, п.2) ---

def test_uncertain_blocks_entries():
    """Если client.uncertain_state=True — trading_cycle должен вернуться до входов.
    Проверяем контракт: is_uncertain() читает флаг."""
    client = TinkoffClient("fake", use_sandbox=True)
    assert client.is_uncertain() is False
    client.uncertain_state = True
    assert client.is_uncertain() is True