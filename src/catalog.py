"""
Справочник категорий расходов: правила над списком Category.

Хранилище — лист «Категории» в таблице, за него отвечает sheets.py. Сюда приходит
готовый список, отсюда уходит новый, поэтому валидация имени, защита системной
категории и генерация id проверяются без похода в сеть.

Подробности решения: docs/adr/0002-spravochnik-kategoriy.md
"""

import re
from dataclasses import dataclass, replace

# Приёмник для трат удалённых категорий. Не удаляется и не переименовывается.
SYSTEM_CATEGORY = "Другое"

# Длиннее не влезает в подпись кнопки и разъезжается в строке отчёта.
MAX_NAME_LENGTH = 30

_SPACES_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    keywords: tuple[str, ...] = ()
    is_system: bool = False


class CatalogError(Exception):
    """Нарушено правило справочника. Текст показываем пользователю как есть."""


def normalize_name(name: str) -> str:
    return _SPACES_RE.sub(" ", name).strip()


def names(categories: list[Category]) -> list[str]:
    return [category.name for category in categories]


def find(categories: list[Category], category_id: int) -> Category | None:
    return next((category for category in categories if category.id == category_id), None)


def find_by_name(categories: list[Category], name: str) -> Category | None:
    wanted = normalize_name(name).casefold()
    return next(
        (category for category in categories if category.name.casefold() == wanted),
        None,
    )


def next_id(categories: list[Category]) -> int:
    """id не переиспользуются: иначе висящая в чате кнопка после удаления категории
    молча запишет трату в чужую."""
    return max((category.id for category in categories), default=0) + 1


def keywords_map(categories: list[Category]) -> dict[str, list[str]]:
    """Словарь для автокатегоризации. Категории без ключевых слов в него не попадают."""
    return {
        category.name: list(category.keywords) for category in categories if category.keywords
    }


def add(categories: list[Category], name: str) -> tuple[list[Category], Category]:
    clean = _validated_name(categories, name)
    created = Category(id=next_id(categories), name=clean)
    return categories + [created], created


def rename(
    categories: list[Category], category_id: int, name: str
) -> tuple[list[Category], str, str]:
    """Новый список, старое и новое имя. Имена нужны вызывающему, чтобы переписать
    колонку «Категория» в записях."""
    category = _editable(categories, category_id)
    clean = _validated_name(categories, name, exclude_id=category_id)
    updated = [
        replace(existing, name=clean) if existing.id == category_id else existing
        for existing in categories
    ]
    return updated, category.name, clean


def delete(categories: list[Category], category_id: int) -> list[Category]:
    _editable(categories, category_id)
    return [category for category in categories if category.id != category_id]


def _editable(categories: list[Category], category_id: int) -> Category:
    category = find(categories, category_id)
    if category is None:
        raise CatalogError("Такой категории больше нет — открой список заново.")
    if category.is_system:
        raise CatalogError(f"«{category.name}» — служебная категория, её нельзя менять.")
    return category


def _validated_name(
    categories: list[Category], name: str, exclude_id: int | None = None
) -> str:
    clean = normalize_name(name)
    if not clean:
        raise CatalogError("Название пустое.")
    if len(clean) > MAX_NAME_LENGTH:
        raise CatalogError(f"Название длиннее {MAX_NAME_LENGTH} символов.")

    existing = find_by_name(categories, clean)
    if existing is not None and existing.id != exclude_id:
        raise CatalogError(f"Категория «{existing.name}» уже есть.")
    return clean
