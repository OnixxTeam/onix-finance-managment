"""Проверяем сборку бота: запись в таблицу тянет обновление закрепа, а роутеры
подключены в правильном порядке.

Об обновлении закрепа легко забыть при добавлении нового пути записи, а тесты
pinned.py такую забывчивость не увидят: там бот вызывается напрямую.
"""

import asyncio
import sys
import types

import pytest

import sheets as sheets_module


@pytest.fixture
def bot_module(monkeypatch):
    """bot.py создаёт SheetsClient на импорте, поэтому подменяем клиент до импорта."""
    monkeypatch.setattr(
        sheets_module,
        "SheetsClient",
        lambda: types.SimpleNamespace(
            added=[],
            add_expense=lambda *args: None,
            add_income=lambda *args: None,
        ),
    )
    # Роутер справочника — модульный синглтон и к диспетчеру цепляется один раз,
    # поэтому пересоздаём оба модуля вместе.
    monkeypatch.delitem(sys.modules, "bot", raising=False)
    monkeypatch.delitem(sys.modules, "bot_catalog", raising=False)

    import bot

    monkeypatch.setattr(bot.categorizer, "classify", lambda description: "Еда")
    return bot


class RecordingBot:
    pass


class FakeUser:
    id = 1


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.from_user = FakeUser()
        self.answers: list[str] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append(text)


def test_expense_refreshes_pinned_report(bot_module, monkeypatch):
    refreshed: list[int] = []

    async def fake_refresh(bot, storage, chat_id):
        refreshed.append(chat_id)

    monkeypatch.setattr(bot_module, "refresh_pinned", fake_refresh)

    message = FakeMessage("кофе 300")
    asyncio.run(bot_module.handle_message(message, RecordingBot()))

    assert message.answers, "пользователь должен получить подтверждение"
    assert refreshed == [bot_module.TELEGRAM_USER_ID]


def test_catalog_router_goes_before_the_catch_all(bot_module):
    """Название категории приходит обычным текстом. Подключи справочник вторым —
    и его съест handle_message, который ловит любой текст."""
    import bot_catalog

    assert bot_module.dp.sub_routers[0] is bot_catalog.router
    assert bot_module.dp.sub_routers[-1] is bot_module.router
