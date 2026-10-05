"""Голосование по жанру для автосинхронизации — docs/watch-folder-autosync-design.md.

Каждый тест закрепляет одно правило, найденное проверкой на библиотеке
пользователя: что именно решается автоматически, а что уходит человеку.
"""
from fb2parser_core.genre_voting import (
    BLOCK_LOW_SCORE,
    BLOCK_LOW_SHARE,
    BLOCK_NO_ANCHOR,
    BLOCK_NO_SIGNAL,
    BookEvidence,
    GenreVoter,
)
from fb2parser_core.library_memory import GenreMemory

REFERENCE = {"sf_action": "Фантастика", "det_action": "Детектив", "det_police": "Детектив"}
GENRES = ["Фантастика", "Детектив", "Триллер"]


def _memory(books=(), code_batches=()):
    """books: (жанр, автор, [серия], [изд. серии], [коды]); code_batches: (код, жанр, порций)."""
    mem = GenreMemory()
    for genre, author, series, pubseq, codes in books:
        mem.add_book(genre, author, series, pubseq, codes)
    for code, genre, n in code_batches:
        mem.code_batches[code][genre] += n
    return mem


def _voter(mem, confidence=0.8, excluded=("compilation",)):
    return GenreVoter(mem, resolve_code=REFERENCE.get, genre_names=GENRES,
                      excluded_codes=excluded, confidence=confidence)


def _book(path, author="Новый Автор", series="", codes=(), pubseq=()):
    return BookEvidence(path, author, series, list(codes), list(pubseq))


def test_new_volume_follows_its_series_in_library_despite_wrong_codes():
    mem = _memory([("Фантастика", "Иванов Иван", ["Звёзды"], [], [])] * 5)
    book = _book("b/5.fb2", "Иванов Иван", "Звёзды", ["det_action"])
    d80 = _voter(mem).decide_batch([book])[0]
    assert d80.genre == "Фантастика"
    # серия 10 против кодов 4 — 71%: при пороге 80% это ещё вопрос человеку,
    assert not d80.auto and d80.blocker == BLOCK_LOW_SHARE
    # а при пороге 70% — уже решено.
    assert _voter(mem, confidence=0.7).decide_batch([book])[0].auto


def test_unfamiliar_codes_do_not_decide_by_a_single_known_code():
    # «Разгон ошибок»: детектив с незнакомым кодом и одним общим кодом, который
    # память видела только в фантастике, — не должен уехать в фантастику сам.
    mem = _memory([("Фантастика", f"А{i}", [], [], ["popular"]) for i in range(10)],
                  [("popular", "Фантастика", 1)])
    voter = GenreVoter(mem, resolve_code=lambda c: None, genre_names=GENRES)
    d = voter.decide_batch([_book("b/1.fb2", codes=["det_unknown", "popular"])])[0]
    assert d.genre == "Фантастика" and not d.auto
    # голос кодов ослаблен вдвое (знаком лишь один код из двух),
    assert d.signals[("codes", "Фантастика")] == 2.0
    # а правило «popular → Фантастика» видела лишь одна порция — не якорь.
    assert d.blocker == BLOCK_NO_ANCHOR


def test_single_weak_hint_is_below_min_score():
    mem = _memory([("Фантастика", f"А{i}", [], [], ["popular"]) for i in range(10)],
                  [("popular", "Фантастика", 5)])
    voter = GenreVoter(mem, resolve_code=lambda c: None, genre_names=GENRES)
    # один знакомый код из четырёх: 4 × 1/4 = 1 балл (+ столь же слабый голос порции)
    d = voter.decide_batch([_book("b/1.fb2", codes=["x1", "x2", "x3", "popular"])])[0]
    assert d.score < 3 and d.blocker == BLOCK_LOW_SCORE


def test_reliable_code_confirmed_by_three_batches_allows_auto_without_anchor():
    mem = _memory([("Фантастика", f"А{i}", [], [], ["sf_action"]) for i in range(6)],
                  [("sf_action", "Фантастика", 3)])
    d = _voter(mem).decide_batch([_book("b/1.fb2", codes=["sf_action"])])[0]
    assert d.auto and d.genre == "Фантастика"


def test_code_confirmed_by_two_batches_is_not_yet_reliable():
    mem = _memory([("Фантастика", f"А{i}", [], [], ["sf_action"]) for i in range(6)],
                  [("sf_action", "Фантастика", 2)])
    d = _voter(mem).decide_batch([_book("b/1.fb2", codes=["sf_action"])])[0]
    assert d.genre == "Фантастика" and not d.auto and d.blocker == BLOCK_NO_ANCHOR


def test_author_profile_needs_five_books():
    two = _memory([("Детектив", "Петров Пётр", [], [], [])] * 2)
    d = _voter(two).decide_batch([_book("b/1.fb2", "Петров Пётр")])[0]
    assert d.blocker == BLOCK_NO_SIGNAL
    five = _memory([("Детектив", "Петров Пётр", [], [], [])] * 5)
    d = _voter(five).decide_batch([_book("b/1.fb2", "Петров Пётр")])[0]
    assert d.genre == "Детектив" and ("profile", "Детектив") in d.signals


def test_author_profile_is_ignored_when_codes_are_clear():
    # Автор фантастики написал детектив: однозначные коды важнее привычки автора.
    mem = _memory([("Фантастика", "Петров Пётр", [], [], [])] * 40)
    d = _voter(mem).decide_batch([_book("b/1.fb2", "Петров Пётр", codes=["det_police"])])[0]
    assert d.genre == "Детектив"
    assert not any(k == "profile" for k, _g in d.signals)


def test_volumes_of_a_new_series_vote_together():
    books = [_book(f"b/{i}.fb2", "Сидоров С", "Новая", ["sf_action"]) for i in range(3)]
    books.append(_book("b/3.fb2", "Сидоров С", "Новая", ["det_action"]))
    decisions = _voter(_memory()).decide_batch(books)
    assert len(decisions) == 1
    assert decisions[0].genre == "Фантастика" and len(decisions[0].files) == 4


def test_tag_with_genre_name_votes_for_that_genre():
    d = _voter(_memory()).decide_batch([_book("b/1.fb2", codes=["Детектив"])])[0]
    assert d.genre == "Детектив"


def test_excluded_codes_are_not_a_genre_signal():
    d = _voter(_memory()).decide_batch([_book("b/1.fb2", codes=["compilation"])])[0]
    assert d.blocker == BLOCK_NO_SIGNAL


def test_series_is_found_under_one_of_the_coauthors():
    mem = _memory([("Фантастика", "Сапфир Олег", ["Кодекс Охотника"], [], [])])
    d = _voter(mem).decide_batch(
        [_book("b/1.fb2", "Винокуров Юрий, Сапфир Олег", "Кодекс Охотника")])[0]
    assert ("series", "Фантастика") in d.signals and d.auto


def test_known_publisher_series_is_an_anchor():
    mem = _memory([("Триллер", f"А{i}", [], ["Новый мировой триллер"], []) for i in range(4)])
    d = _voter(mem).decide_batch(
        [_book("b/1.fb2", codes=["det_action"], pubseq=["Новый мировой триллер"])])[0]
    assert d.genre == "Триллер" and ("pub", "Триллер") in d.signals


def test_profile_alone_does_not_feed_the_batch_vote():
    # Профиль автора — слабая догадка: голос порции её не усиливает
    # (иначе 70 книг Колычева уехали в фантастику по двум книгам автора).
    mem = _memory([("Фантастика", "Колычев Владимир", [], [], [])] * 5)
    books = [_book(f"b/{i}.fb2", "Колычев Владимир") for i in range(5)]
    for d in _voter(mem).decide_batch(books):
        assert not any(k == "batch" for k, _g in d.signals)
