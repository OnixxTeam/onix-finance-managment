"""Строки листа «Категории» ↔ Category. Ключевые слова правятся руками в таблице,
поэтому разбор должен переживать лишние пробелы, регистр и пустые строки."""

from catalog import SYSTEM_CATEGORY, Category
from sheets import CATEGORIES_HEADER, category_from_row, category_to_row, seed_categories


def test_row_round_trip():
    category = Category(id=4, name="Спорт", keywords=("зал", "бассейн"))

    assert category_from_row(category_to_row(category)) == category


def test_system_flag_survives_round_trip():
    category = Category(id=1, name=SYSTEM_CATEGORY, is_system=True)

    assert category_from_row(category_to_row(category)) == category


def test_keywords_are_trimmed_and_lowercased():
    category = category_from_row([2, "Транспорт", " Такси ,  МЕТРО ,, ", ""])

    assert category.keywords == ("такси", "метро")


def test_row_without_keywords_column():
    assert category_from_row([3, "Спорт"]) == Category(id=3, name="Спорт")


def test_junk_rows_are_skipped():
    assert category_from_row([]) is None
    assert category_from_row(["", ""]) is None
    assert category_from_row(["id", "Название", "", ""]) is None
    assert category_from_row([1, "   "]) is None


def test_seed_has_ids_keywords_and_system_category():
    seeded = seed_categories()

    assert [category.id for category in seeded] == list(range(1, len(seeded) + 1))
    assert any(category.keywords for category in seeded)

    system = [category for category in seeded if category.is_system]
    assert [category.name for category in system] == [SYSTEM_CATEGORY]


def test_header_matches_row_width():
    assert len(category_to_row(Category(id=1, name="Спорт"))) == len(CATEGORIES_HEADER)
