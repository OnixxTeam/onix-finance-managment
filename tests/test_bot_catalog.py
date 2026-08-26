from bot_catalog import catalog_text
from catalog import SYSTEM_CATEGORY, Category
from listing import entries_phrase


def test_entries_phrase_declension():
    assert entries_phrase(1) == "1 запись"
    assert entries_phrase(3) == "3 записи"
    assert entries_phrase(5) == "5 записей"
    assert entries_phrase(0) == "0 записей"
    assert entries_phrase(11) == "11 записей"
    assert entries_phrase(21) == "21 запись"
    assert entries_phrase(112) == "112 записей"


def test_catalog_text_numbers_categories_and_marks_system():
    text = catalog_text(
        [
            Category(id=1, name="Транспорт"),
            Category(id=2, name=SYSTEM_CATEGORY, is_system=True),
        ]
    )

    assert "1. Транспорт" in text
    assert f"2. {SYSTEM_CATEGORY} — служебная" in text


def test_commands_are_not_taken_for_category_names():
    """Иначе «/list 5», набранное вместо названия, станет категорией с таким именем."""
    from types import SimpleNamespace

    from bot_catalog import NOT_A_COMMAND

    assert NOT_A_COMMAND.resolve(SimpleNamespace(text="Спорт"))
    assert not NOT_A_COMMAND.resolve(SimpleNamespace(text="/list 5"))
    assert not NOT_A_COMMAND.resolve(SimpleNamespace(text=None))
