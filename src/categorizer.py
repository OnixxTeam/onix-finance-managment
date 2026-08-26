"""
Категоризация трат/доходов.

Сейчас: словарный классификатор (подстрока в тексте -> категория).
Дальше (когда словарь перестанет справляться с новыми словами типа "кола"):
подключить embeddings-классификатор — план подключения в конце файла.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable


class Categorizer(ABC):
    @abstractmethod
    def classify(self, text: str) -> str | None:
        """Вернуть категорию или None, если уверенности нет (тогда бот спрашивает пользователя)."""


class KeywordCategorizer(Categorizer):
    """Словарь ключевых слов живёт в таблице и правится на ходу, поэтому берём его
    через provider на каждый разбор, а не запоминаем на старте."""

    def __init__(self, keywords_provider: Callable[[], dict[str, list[str]]]):
        self._keywords_provider = keywords_provider

    def classify(self, text: str) -> str | None:
        normalized = text.lower()
        for category, words in self._keywords_provider().items():
            if any(word in normalized for word in words):
                return category
        return None


def get_categorizer(keywords_provider: Callable[[], dict[str, list[str]]]) -> Categorizer:
    return KeywordCategorizer(keywords_provider)


# ---------------------------------------------------------------------------
# Шаги подключения embeddings-классификатора (вариант 1, когда понадобится):
#
# 1. pip install sentence-transformers
#    (тянет ~200-400MB зависимостей + модель при первом запуске)
#
# 2. Скачать модель один раз (при первом старте скачается автоматически):
#    model_name = "paraphrase-multilingual-MiniLM-L12-v2"
#
# 3. Создать класс EmbeddingCategorizer(Categorizer):
#    - в __init__: self._model = SentenceTransformer(model_name)
#      посчитать и закэшировать эмбеддинги якорных слов на категорию
#      (те же списки ключевых слов из листа «Категории», но не для substring-match,
#      а как anchor-фразы для semantic similarity)
#    - в classify(text): эмбеддинг text, cosine similarity со всеми якорями,
#      если max similarity > порог (подобрать эмпирически, начать с 0.5) —
#      вернуть категорию победителя, иначе None (fallback на ручной выбор)
#
# 4. В get_categorizer() заменить return KeywordCategorizer(keywords_provider)
#    на связку: сначала KeywordCategorizer (мгновенно, бесплатно),
#    если вернул None — EmbeddingCategorizer (ловит "кола" и подобное)
#
# 5. Добавить EmbeddingCategorizer в requirements.txt отдельной опциональной
#    зависимостью, чтобы не тянуть sentence-transformers всем, кто не пользуется
# ---------------------------------------------------------------------------
