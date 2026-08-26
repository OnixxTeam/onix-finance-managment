import pytest

from catalog import (
    MAX_NAME_LENGTH,
    SYSTEM_CATEGORY,
    CatalogError,
    Category,
    add,
    delete,
    find,
    find_by_name,
    keywords_map,
    names,
    next_id,
    normalize_name,
    rename,
)


def catalog() -> list[Category]:
    return [
        Category(id=1, name="Транспорт", keywords=("такси", "метро")),
        Category(id=2, name="Фастфуд", keywords=("шаурма",)),
        Category(id=3, name=SYSTEM_CATEGORY, is_system=True),
    ]


def test_normalize_trims_and_collapses_spaces():
    assert normalize_name("  Кафе   и  бары ") == "Кафе и бары"


def test_add_appends_with_new_id():
    updated, created = add(catalog(), "Спорт")

    assert created.id == 4
    assert created.name == "Спорт"
    assert names(updated)[-1] == "Спорт"


def test_next_id_ignores_holes():
    """id не переиспользуются: висящая в чате кнопка не должна попасть в чужую категорию."""
    assert next_id([Category(id=7, name="Спорт")]) == 8


def test_add_rejects_empty_name():
    with pytest.raises(CatalogError):
        add(catalog(), "   ")


def test_add_rejects_too_long_name():
    with pytest.raises(CatalogError):
        add(catalog(), "я" * (MAX_NAME_LENGTH + 1))


def test_add_rejects_duplicate_ignoring_case_and_spaces():
    with pytest.raises(CatalogError):
        add(catalog(), "  транспорт ")


def test_rename_changes_name_and_reports_old():
    updated, old_name, new_name = rename(catalog(), 1, " Поездки ")

    assert (old_name, new_name) == ("Транспорт", "Поездки")
    assert find(updated, 1).name == "Поездки"
    assert find(updated, 1).keywords == ("такси", "метро")


def test_rename_to_same_name_is_allowed():
    updated, old_name, new_name = rename(catalog(), 1, "Транспорт")

    assert (old_name, new_name) == ("Транспорт", "Транспорт")
    assert names(updated) == names(catalog())


def test_rename_rejects_duplicate_of_another_category():
    with pytest.raises(CatalogError):
        rename(catalog(), 1, "Фастфуд")


def test_rename_rejects_system_category():
    with pytest.raises(CatalogError):
        rename(catalog(), 3, "Прочее")


def test_delete_removes_category():
    updated = delete(catalog(), 2)

    assert names(updated) == ["Транспорт", SYSTEM_CATEGORY]


def test_delete_rejects_system_category():
    """«Другое» — приёмник для трат удалённых категорий, без него удаление некуда сливать."""
    with pytest.raises(CatalogError):
        delete(catalog(), 3)


def test_unknown_id_is_an_error():
    with pytest.raises(CatalogError):
        rename(catalog(), 99, "Что-то")
    with pytest.raises(CatalogError):
        delete(catalog(), 99)


def test_find_by_name_ignores_case():
    assert find_by_name(catalog(), "фастфуд").id == 2
    assert find_by_name(catalog(), "нет такой") is None


def test_keywords_map_skips_categories_without_keywords():
    assert keywords_map(catalog()) == {
        "Транспорт": ["такси", "метро"],
        "Фастфуд": ["шаурма"],
    }
