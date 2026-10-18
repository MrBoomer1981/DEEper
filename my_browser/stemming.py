"""Russian morphology for gpt-researcher's BM25 context filter.

Upstream's keyword filter (gpt_researcher/context/lexical.py) strips only English plurals and
drops only English stopwords, so «аккумулятор», «аккумуляторы» and «аккумуляторов» count as
different words and Russian pages rank poorly. Importing this module swaps in a tokenizer that
stems Cyrillic words with the Snowball Russian stemmer (nltk is already installed) and drops
Russian stopwords; Latin words go through upstream's stemmer unchanged.
"""

import re
from functools import lru_cache

from gpt_researcher.context import lexical
from nltk.stem.snowball import SnowballStemmer

_CYRILLIC = re.compile("[а-яё]")
_russian = SnowballStemmer("russian")

RU_STOPWORDS = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее её мне было
вот от меня еще ещё нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас
нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем
была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем
ним здесь этом один почти мой тем чтобы нее неё сейчас были куда зачем всех никогда можно при наконец
два об другой хоть после над больше тот через эти нас про всего них какая много разве три эту моя
впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между
это также который которые которая которых которого котором является являются
""".split())


@lru_cache(maxsize=200_000)
def _stem(token: str) -> str:
    return _russian.stem(token) if _CYRILLIC.search(token) else lexical._stem(token)


def tokenize(text: str) -> list[str]:
    return [
        _stem(t) for t in lexical._TOKEN.findall(text.lower())
        if len(t) > 1 and t not in lexical._STOPWORDS and t not in RU_STOPWORDS
    ]


lexical.tokenize = tokenize
