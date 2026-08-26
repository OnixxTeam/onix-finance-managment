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
from listing import entries_phrase
from pinned import refresh_pinned
from sheets import SheetsClient

logger = logging.getLogger(__name__)

router = Router()

CATALOG_PREFIX = "catalog:"

CANCEL_HINT = "Отменить — /cancel."

# Команду, набранную вместо названия, отдаём основному роутеру: иначе «/list 5»
# превратится в категорию с таким именем.
NOT_A_COMMAND = F.text & ~F.text.startswith("/")


class CategoryForm(StatesGroup):
    adding = State()
    renaming = State()
    confirming_rename = State()


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


def build_rename_confirm_keyboard() -> InlineKeyboardMarkup:
    """Новое имя лежит в состоянии диалога: в 64 байта callback_data оно не всегда влезает."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Переименовать", callback_data=CATALOG_PREFIX + "renameconfirm")
    builder.button(text="Отмена", callback_data=CATALOG_PREFIX + "list")
    builder.adjust(2)
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

    _, category = await _picked_category(callback, sheets)
    if category is None:
        return

    await state.set_state(CategoryForm.renaming)
    await state.update_data(category_id=category.id)
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

    _, category = await _picked_category(callback, sheets)
    if category is None:
        return

    affected = await asyncio.to_thread(sheets.count_by_category, category.name)
    await callback.message.edit_text(
        f"Удалить «{category.name}»?\n"
        f"{entries_phrase(affected)} перейдут в «{SYSTEM_CATEGORY}». Отменить будет нельзя.",
        reply_markup=build_confirm_keyboard(category.id),
    )


@router.callback_query(F.data.startswith(CATALOG_PREFIX + "delconfirm:"))
async def do_delete(callback: CallbackQuery, bot: Bot, sheets: SheetsClient) -> None:
    await callback.answer()

    categories, category = await _picked_category(callback, sheets)
    if category is None:
        return

    try:
        updated = catalog.delete(categories, category.id)
    except CatalogError as error:
        await callback.message.edit_text(str(error), reply_markup=build_catalog_keyboard())
        return

    await asyncio.to_thread(sheets.save_categories, updated)
    moved = await _recategorize(sheets, category.name, SYSTEM_CATEGORY)

    await callback.message.edit_text(
        f"Удалил «{category.name}». {entries_phrase(moved)} перешли в «{SYSTEM_CATEGORY}».\n\n"
        + catalog_text(updated),
        reply_markup=build_catalog_keyboard(),
    )
    await refresh_pinned(bot, sheets, TELEGRAM_USER_ID)


@router.message(CategoryForm.adding, Command("cancel"))
@router.message(CategoryForm.renaming, Command("cancel"))
@router.message(CategoryForm.confirming_rename, Command("cancel"))
async def cancel_editing(message: Message, sheets: SheetsClient, state: FSMContext) -> None:
    await state.clear()
    categories = await asyncio.to_thread(sheets.fetch_categories)
    await message.answer(catalog_text(categories), reply_markup=build_catalog_keyboard())


@router.message(CategoryForm.adding, NOT_A_COMMAND)
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


@router.message(CategoryForm.renaming, NOT_A_COMMAND)
async def confirm_rename(message: Message, sheets: SheetsClient, state: FSMContext) -> None:
    """Имя проверяем сразу, а правку записей подтверждаем: она необратима, как и удаление."""
    data = await state.get_data()
    categories = await asyncio.to_thread(sheets.fetch_categories)
    try:
        _, old_name, new_name = catalog.rename(categories, data["category_id"], message.text)
    except CatalogError as error:
        await message.answer(f"{error} {CANCEL_HINT}")
        return

    affected = await asyncio.to_thread(sheets.count_by_category, old_name)
    await state.set_state(CategoryForm.confirming_rename)
    await state.update_data(new_name=new_name)
    await message.answer(
        f"Переименовать «{old_name}» в «{new_name}»?\n"
        f"{entries_phrase(affected)} обновятся. Отменить будет нельзя.",
        reply_markup=build_rename_confirm_keyboard(),
    )


@router.callback_query(CategoryForm.confirming_rename, F.data == CATALOG_PREFIX + "renameconfirm")
async def do_rename(
    callback: CallbackQuery, bot: Bot, sheets: SheetsClient, state: FSMContext
) -> None:
    await callback.answer()

    data = await state.get_data()
    categories = await asyncio.to_thread(sheets.fetch_categories)
    try:
        updated, old_name, new_name = catalog.rename(
            categories, data["category_id"], data["new_name"]
        )
    except CatalogError as error:
        await state.clear()
        await callback.message.edit_text(str(error), reply_markup=build_catalog_keyboard())
        return

    await state.clear()
    await asyncio.to_thread(sheets.save_categories, updated)
    renamed = await _recategorize(sheets, old_name, new_name)

    await callback.message.edit_text(
        f"Теперь «{new_name}». {entries_phrase(renamed)} обновлены.\n\n" + catalog_text(updated),
        reply_markup=build_catalog_keyboard(),
    )
    await refresh_pinned(bot, sheets, TELEGRAM_USER_ID)


async def _picked_category(
    callback: CallbackQuery, sheets: SheetsClient
) -> tuple[list[Category], Category | None]:
    """Справочник и категория, выбранная кнопкой. None — категорию уже удалили:
    кнопка могла прилететь из старого сообщения. Тогда сами показываем список заново."""
    categories = await asyncio.to_thread(sheets.fetch_categories)
    category = catalog.find(categories, int(callback.data.rsplit(":", 1)[1]))
    if category is None:
        await callback.message.edit_text(
            "Такой категории больше нет.", reply_markup=build_catalog_keyboard()
        )
    return categories, category


async def _recategorize(sheets: SheetsClient, old_name: str, new_name: str) -> int:
    """Справочник уже сохранён, поэтому упавшая правка записей не должна ронять диалог:
    новые траты и так пойдут с новым именем, старые чинятся повтором операции."""
    try:
        return await asyncio.to_thread(sheets.recategorize, old_name, new_name)
    except Exception:
        logger.exception("Не удалось переписать категорию %r на %r", old_name, new_name)
        return 0
