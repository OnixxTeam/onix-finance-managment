import asyncio
import logging
import re
from contextlib import suppress
from datetime import date

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import bot_catalog
import catalog
import listing
from catalog import Category
from categories import INCOME_CATEGORIES
from categorizer import get_categorizer
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID
from pinned import refresh_pinned, run_daily_updates
from report import PERIODS, Report, build_report, format_amount, format_report, period_title
from sheets import SheetsClient

sheets = SheetsClient()
categorizer = get_categorizer(lambda: catalog.keywords_map(sheets.fetch_categories()))

# sheets отдаём хендлерам через workflow_data: так модуль справочника не зависит от bot.py.
dp = Dispatcher(sheets=sheets)
dp.message.filter(F.from_user.id == TELEGRAM_USER_ID)
dp.callback_query.filter(F.from_user.id == TELEGRAM_USER_ID)

# Свои хендлеры диспетчер проверяет раньше вложенных роутеров, а handle_message ловит
# любой текст. Поэтому всё живёт в роутерах, и справочник подключён первым — иначе
# введённое название категории уедет в траты.
router = Router()
dp.include_router(bot_catalog.router)
dp.include_router(router)

AMOUNT_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*(тысяч[аи]?|тыс\.?|к|k)?", re.IGNORECASE)

AMOUNT_MULTIPLIERS = {
    "к": 1000,
    "k": 1000,
    "тыс": 1000,
    "тыс.": 1000,
    "тысяча": 1000,
    "тысячи": 1000,
}

REPORT_BUTTON = "📊 Отчёт"

# Префиксы callback_data: без них колбэк отчёта неотличим от выбора категории.
# Расходную категорию адресуем id из справочника: имя редактируемое и может не влезть
# в 64 байта callback_data, а позиция в списке съезжает после удаления.
CATEGORY_PREFIX = "cat:"
INCOME_PREFIX = "inc:"
PERIOD_PREFIX = "period:"
DRILL_PREFIX = "drill:"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=REPORT_BUTTON)]],
    resize_keyboard=True,
)

# user_id -> {"description": str, "amount": float, "is_income": bool}
pending: dict[int, dict] = {}


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Пиши трату или доход в формате: <описание> <сумма>\n"
        "Например: кофе 300\n"
        "Отрицательная сумма считается доходом.\n"
        f"Кнопка «{REPORT_BUTTON}» — сводка по доходам, расходам и остатку.\n"
        "/list N — последние записи.\n"
        "/categories — категории расходов: добавить, переименовать, удалить.",
        reply_markup=MAIN_KEYBOARD,
    )


def parse_entry(text: str) -> tuple[str, float] | None:
    match = AMOUNT_RE.search(text)
    if not match:
        return None
    amount = float(match.group(1).replace(",", "."))
    suffix = (match.group(2) or "").lower().rstrip(".")
    amount *= AMOUNT_MULTIPLIERS.get(suffix, 1)
    description = (text[: match.start()] + text[match.end() :]).strip()
    if not description:
        return None
    return description, amount


def build_expense_keyboard(categories: list[Category]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category.name, callback_data=f"{CATEGORY_PREFIX}{category.id}")
    builder.adjust(1)
    return builder.as_markup()


def build_income_keyboard(options: list[str]) -> InlineKeyboardMarkup:
    """Доходных категорий две и они не редактируются, поэтому имя в callback_data."""
    builder = InlineKeyboardBuilder()
    for category in options:
        builder.button(text=category, callback_data=INCOME_PREFIX + category)
    builder.adjust(1)
    return builder.as_markup()


def build_period_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for period, label in PERIODS.items():
        builder.button(text=label, callback_data=PERIOD_PREFIX + period)
    builder.adjust(2)
    return builder.as_markup()


@router.message(Command("report"))
@router.message(F.text == REPORT_BUTTON)
async def ask_period(message: Message) -> None:
    await message.answer("За какой период?", reply_markup=build_period_keyboard())


def build_report_keyboard(
    report: Report, categories: list[Category], period: str
) -> InlineKeyboardMarkup | None:
    """Кнопка на каждую категорию расходов из отчёта — переход к её тратам.

    Порядок тот же, что и в тексте отчёта. Категории, которых уже нет в справочнике
    (правка листа руками), остаются без кнопки: адресовать их нечем.
    """
    by_amount = sorted(report.expense_by_category.items(), key=lambda item: item[1], reverse=True)

    builder = InlineKeyboardBuilder()
    buttons = 0
    for name, _ in by_amount:
        category = catalog.find_by_name(categories, name)
        if category is None:
            continue
        builder.button(text=name, callback_data=f"{DRILL_PREFIX}{period}:{category.id}")
        buttons += 1

    if not buttons:
        return None
    builder.adjust(2)
    return builder.as_markup()


@router.callback_query(F.data.startswith(PERIOD_PREFIX))
async def handle_period_choice(callback: CallbackQuery) -> None:
    await callback.answer()

    period = callback.data.removeprefix(PERIOD_PREFIX)
    # gspread синхронный: без to_thread чтение всего листа блокирует polling.
    entries = await asyncio.to_thread(sheets.fetch_entries)
    categories = await asyncio.to_thread(sheets.fetch_categories)
    report = build_report(entries, period)
    await callback.message.edit_text(
        format_report(report),
        reply_markup=build_report_keyboard(report, categories, period),
    )


@router.callback_query(F.data.startswith(DRILL_PREFIX))
async def handle_category_drilldown(callback: CallbackQuery) -> None:
    """Траты одной категории за период отчёта, из которого пришли."""
    await callback.answer()

    period, raw_id = callback.data.removeprefix(DRILL_PREFIX).split(":", 1)
    categories = await asyncio.to_thread(sheets.fetch_categories)
    category = catalog.find(categories, int(raw_id))
    if category is None:
        await callback.message.edit_text("Этой категории больше нет — построй отчёт заново.")
        return

    entries = await asyncio.to_thread(sheets.fetch_entries)
    matched = listing.in_category(entries, category.name, period)
    await callback.message.edit_text(
        listing.format_category_listing(category.name, period_title(period, date.today()), matched),
        reply_markup=build_back_keyboard(period),
    )


def build_back_keyboard(period: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К отчёту", callback_data=PERIOD_PREFIX + period)
    return builder.as_markup()


@router.message(Command("list"))
async def show_recent(message: Message, command: CommandObject) -> None:
    limit = listing.parse_limit(command.args)
    if limit is None:
        await message.answer(
            f"Сколько записей показать? Формат: /list N, "
            f"по умолчанию {listing.DEFAULT_LIST_SIZE}, максимум {listing.MAX_LIST_SIZE}."
        )
        return

    entries = await asyncio.to_thread(sheets.fetch_entries)
    await message.answer(
        listing.format_listing(
            "Последние записи", listing.recent(entries, limit), total=len(entries)
        )
    )


@router.message(F.text)
async def handle_message(message: Message, bot: Bot) -> None:
    parsed = parse_entry(message.text)
    if parsed is None:
        await message.answer("Не понял. Формат: <описание> <сумма>, например: кофе 300")
        return

    description, amount = parsed
    is_income = amount < 0
    amount = abs(amount)

    if is_income:
        await ask_category(
            message, description, amount, build_income_keyboard(INCOME_CATEGORIES), is_income=True
        )
        return

    category = categorizer.classify(description)
    if category is None:
        categories = await asyncio.to_thread(sheets.fetch_categories)
        await ask_category(
            message, description, amount, build_expense_keyboard(categories), is_income=False
        )
        return

    sheets.add_expense(description, amount, category)
    await message.answer(f"Записал: {description} — {format_amount(amount)} — {category}")
    await refresh_pinned(bot, sheets, TELEGRAM_USER_ID)


async def ask_category(
    message: Message,
    description: str,
    amount: float,
    keyboard: InlineKeyboardMarkup,
    is_income: bool,
) -> None:
    pending[message.from_user.id] = {
        "description": description,
        "amount": amount,
        "is_income": is_income,
    }
    await message.answer(
        f"{description} — {format_amount(amount)}. Выбери категорию:",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith(CATEGORY_PREFIX))
async def handle_expense_category_choice(callback: CallbackQuery, bot: Bot) -> None:
    entry = _take_pending(callback.from_user.id)
    await callback.answer()
    if entry is None:
        await callback.message.edit_text("Эта запись уже устарела, начни заново.")
        return

    categories = await asyncio.to_thread(sheets.fetch_categories)
    category = catalog.find(categories, int(callback.data.removeprefix(CATEGORY_PREFIX)))
    if category is None:
        # Кнопка из старого сообщения: категорию успели удалить, писать некуда.
        await callback.message.edit_text(
            "Этой категории больше нет. Отправь трату заново — покажу актуальный список."
        )
        return

    sheets.add_expense(entry["description"], entry["amount"], category.name)
    await _confirm(callback, bot, entry, category.name)


@router.callback_query(F.data.startswith(INCOME_PREFIX))
async def handle_income_category_choice(callback: CallbackQuery, bot: Bot) -> None:
    entry = _take_pending(callback.from_user.id)
    await callback.answer()
    if entry is None:
        await callback.message.edit_text("Эта запись уже устарела, начни заново.")
        return

    category = callback.data.removeprefix(INCOME_PREFIX)
    sheets.add_income(entry["description"], entry["amount"], category)
    await _confirm(callback, bot, entry, category)


def _take_pending(user_id: int) -> dict | None:
    """Достаём запись один раз: двойной тап по кнопке не должен записать её дважды."""
    return pending.pop(user_id, None)


async def _confirm(callback: CallbackQuery, bot: Bot, entry: dict, category: str) -> None:
    await callback.message.edit_text(
        f"Записал: {entry['description']} — {format_amount(entry['amount'])} — {category}"
    )
    await refresh_pinned(bot, sheets, TELEGRAM_USER_ID)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    # Ссылку на задачу держим: задача без ссылок может быть собрана сборщиком мусора.
    daily_updates = asyncio.create_task(run_daily_updates(bot, sheets, TELEGRAM_USER_ID))
    try:
        await dp.start_polling(bot)
    finally:
        daily_updates.cancel()
        with suppress(asyncio.CancelledError):
            await daily_updates


if __name__ == "__main__":
    asyncio.run(main())
