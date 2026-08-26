"""
Телеграм-часть справочника категорий: /categories и правки кнопками.

Роутер подключается к диспетчеру раньше основного, потому что ввод названия
категории — обычный текст, а основной роутер ловит любой текст как трату.

Правила справочника живут в catalog.py, запись — в sheets.py. Здесь только диалог.
"""

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import catalog
from catalog import SYSTEM_CATEGORY, CatalogError, Category
from config import TELEGRAM_USER_ID
from pinned import refresh_pinned
from sheets import SheetsClient

logger = logging.getLogger(__name__)

router = Router()

CATALOG_PREFIX = "catalog:"

CANCEL_HINT = "Отменить — /cancel."


class CategoryForm(StatesGroup):
    adding = State()
    renaming = State()


def catalog_text(categories: list[Category]) -> str:
    lines = ["🗂 Категории расходов:", ""]
    for number, category in enumerate(categories, start=1):
        mark = " — служебная" if category.is_system else ""
        lines.append(f"{number}. {category.name}{mark}")
    return "\n".join(lines)


def build_catalog_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить", callback_data=CATALOG_PREFIX + "add")
    builder.button(text="✏️ Переименовать", callback_data=CATALOG_PREFIX + "rename")
    builder.button(text="🗑 Удалить", callback_data=CATALOG_PREFIX + "delete")
    builder.adjust(1)
    return builder.as_markup()


def build_pick_keyboard(categories: list[Category], action: str) -> InlineKeyboardMarkup:
    """Выбор категории для правки. Служебную не показываем — её менять нельзя."""
    builder = InlineKeyboardBuilder()
    for category in categories:
        if category.is_system:
            continue
        builder.button(
            text=category.name,
            callback_data=f"{CATALOG_PREFIX}{action}:{category.id}",
        )
    builder.button(text="⬅️ Назад", callback_data=CATALOG_PREFIX + "list")
    builder.adjust(1)
    return builder.as_markup()


def build_confirm_keyboard(category_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Удалить", callback_data=f"{CATALOG_PREFIX}delconfirm:{category_id}")
    builder.button(text="Отмена", callback_data=CATALOG_PREFIX + "list")
    builder.adjust(2)
    return builder.as_markup()


@router.message(Command("categories"))
async def show_catalog(message: Message, sheets: SheetsClient, state: FSMContext) -> None:
    await state.clear()
    categories = await asyncio.to_thread(sheets.fetch_categories)
    await message.answer(catalog_text(categories), reply_markup=build_catalog_keyboard())


@router.callback_query(F.data == CATALOG_PREFIX + "list")
async def back_to_catalog(callback: CallbackQuery, sheets: SheetsClient, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    categories = await asyncio.to_thread(sheets.fetch_categories)
    await callback.message.edit_text(catalog_text(categories), reply_markup=build_catalog_keyboard())


@router.callback_query(F.data == CATALOG_PREFIX + "add")
async def ask_new_name(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(CategoryForm.adding)
    await callback.message.edit_text(f"Название новой категории? {CANCEL_HINT}")


@router.callback_query(F.data == CATALOG_PREFIX + "rename")
async def ask_rename_target(callback: CallbackQuery, sheets: SheetsClient) -> None:
    await callback.answer()
    categories = await asyncio.to_thread(sheets.fetch_categories)
    await callback.message.edit_text(
        "Какую категорию переименовать?",
        reply_markup=build_pick_keyboard(categories, "rename"),
    )


@router.callback_query(F.data.startswith(CATALOG_PREFIX + "rename:"))
async def ask_renamed_name(callback: CallbackQuery, sheets: SheetsClient, state: FSMContext) -> None:
    await callback.answer()

    category_id = int(callback.data.rsplit(":", 1)[1])
    categories = await asyncio.to_thread(sheets.fetch_categories)
    category = catalog.find(categories, category_id)
    if category is None:
        await callback.message.edit_text(
            "Такой категории больше нет.", reply_markup=build_catalog_keyboard()
        )
        return

    await state.set_state(CategoryForm.renaming)
    await state.update_data(category_id=category_id)
    await callback.message.edit_text(f"Новое название для «{category.name}»? {CANCEL_HINT}")


@router.callback_query(F.data == CATALOG_PREFIX + "delete")
async def ask_delete_target(callback: CallbackQuery, sheets: SheetsClient) -> None:
    await callback.answer()
    categories = await asyncio.to_thread(sheets.fetch_categories)
    await callback.message.edit_text(
        "Какую категорию удалить?",
        reply_markup=build_pick_keyboard(categories, "delete"),
    )


@router.callback_query(F.data.startswith(CATALOG_PREFIX + "delete:"))
async def confirm_delete(callback: CallbackQuery, sheets: SheetsClient) -> None:
    """Спрашиваем с числом записей: правка необратима, отката нет."""
    await callback.answer()

    category_id = int(callback.data.rsplit(":", 1)[1])
    categories = await asyncio.to_thread(sheets.fetch_categories)
    category = catalog.find(categories, category_id)
    if category is None:
        await callback.message.edit_text(
            "Такой категории больше нет.", reply_markup=build_catalog_keyboard()
        )
        return

    affected = await asyncio.to_thread(sheets.count_by_category, category.name)
    await callback.message.edit_text(
        f"Удалить «{category.name}»?\n"
        f"{_entries_phrase(affected)} перейдут в «{SYSTEM_CATEGORY}». Отменить будет нельзя.",
        reply_markup=build_confirm_keyboard(category_id),
    )


@router.callback_query(F.data.startswith(CATALOG_PREFIX + "delconfirm:"))
async def do_delete(callback: CallbackQuery, bot: Bot, sheets: SheetsClient) -> None:
    await callback.answer()

    category_id = int(callback.data.rsplit(":", 1)[1])
    categories = await asyncio.to_thread(sheets.fetch_categories)
    category = catalog.find(categories, category_id)
    if category is None:
        await callback.message.edit_text(
            "Такой категории больше нет.", reply_markup=build_catalog_keyboard()
        )
        return

    try:
        updated = catalog.delete(categories, category_id)
    except CatalogError as error:
        await callback.message.edit_text(str(error), reply_markup=build_catalog_keyboard())
        return

    await asyncio.to_thread(sheets.save_categories, updated)
    moved = await _recategorize(sheets, category.name, SYSTEM_CATEGORY)

    await callback.message.edit_text(
        f"Удалил «{category.name}». {_entries_phrase(moved)} перешли в «{SYSTEM_CATEGORY}».\n\n"
        + catalog_text(updated),
        reply_markup=build_catalog_keyboard(),
    )
    await refresh_pinned(bot, sheets, TELEGRAM_USER_ID)


@router.message(CategoryForm.adding, Command("cancel"))
@router.message(CategoryForm.renaming, Command("cancel"))
async def cancel_editing(message: Message, sheets: SheetsClient, state: FSMContext) -> None:
    await state.clear()
    categories = await asyncio.to_thread(sheets.fetch_categories)
    await message.answer(catalog_text(categories), reply_markup=build_catalog_keyboard())


@router.message(CategoryForm.adding, F.text)
async def do_add(message: Message, sheets: SheetsClient, state: FSMContext) -> None:
    categories = await asyncio.to_thread(sheets.fetch_categories)
    try:
        updated, created = catalog.add(categories, message.text)
    except CatalogError as error:
        await message.answer(f"{error} {CANCEL_HINT}")
        return

    await state.clear()
    await asyncio.to_thread(sheets.save_categories, updated)
    await message.answer(
        f"Добавил «{created.name}».\n\n" + catalog_text(updated),
        reply_markup=build_catalog_keyboard(),
    )


@router.message(CategoryForm.renaming, F.text)
async def do_rename(message: Message, bot: Bot, sheets: SheetsClient, state: FSMContext) -> None:
    data = await state.get_data()
    categories = await asyncio.to_thread(sheets.fetch_categories)
    try:
        updated, old_name, new_name = catalog.rename(categories, data["category_id"], message.text)
    except CatalogError as error:
        await message.answer(f"{error} {CANCEL_HINT}")
        return

    await state.clear()
    await asyncio.to_thread(sheets.save_categories, updated)
    renamed = await _recategorize(sheets, old_name, new_name)

    await message.answer(
        f"Теперь «{new_name}». {_entries_phrase(renamed)} обновлены.\n\n" + catalog_text(updated),
        reply_markup=build_catalog_keyboard(),
    )
    await refresh_pinned(bot, sheets, TELEGRAM_USER_ID)


async def _recategorize(sheets: SheetsClient, old_name: str, new_name: str) -> int:
    """Справочник уже сохранён, поэтому упавшая правка записей не должна ронять диалог:
    новые траты и так пойдут с новым именем, старые чинятся повтором операции."""
    try:
        return await asyncio.to_thread(sheets.recategorize, old_name, new_name)
    except Exception:
        logger.exception("Не удалось переписать категорию %r на %r", old_name, new_name)
        return 0


def _entries_phrase(count: int) -> str:
    tail = "записей"
    if count % 10 == 1 and count % 100 != 11:
        tail = "запись"
    elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        tail = "записи"
    return f"{count} {tail}"
