"""Голосование по жанру для автосинхронизации.

См. docs/watch-folder-autosync-design.md, раздел «Голосование по жанру» —
там же объяснено, откуда взялись веса и условия (проверка на библиотеке
пользователя). Модуль не трогает файлы: на входе — то, что regen и
заголовки файлов сказали о книгах порции, и память библиотеки
(`library_memory.GenreMemory`); на выходе — решение по каждой единице
(серия порции или отдельная книга): жанр, уверенность, можно ли решать
автоматически, и из каких сигналов это сложилось.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .library_memory import GenreMemory, author_keys, norm_key

W_SERIES = 10.0   # серия автора уже лежит в библиотеке
W_PUB = 6.0       # известная издательская серия
W_CODES = 4.0     # коды <genre>
W_PROFILE = 3.0   # профиль автора (только когда коды молчат/спорят)
W_BATCH = 6.0     # согласный голос всей порции

CODE_MIN_BOOKS = 5          # код считается выученным, если встречался в стольких книгах
PUB_MIN_BOOKS, PUB_PURITY = 3, 0.8
PROFILE_MIN_BOOKS = 5
PROFILE_IF_CODES_BELOW = 0.7  # профиль включается, если доля лидера кодов ниже
BATCH_HOMOGENEITY = 0.6
RELIABLE_MIN_BATCHES, RELIABLE_PURITY = 3, 0.9

ANCHORS = frozenset({"series", "pub", "profile"})

# Почему единица не решена автоматически
BLOCK_NO_SIGNAL = "no_signal"
BLOCK_LOW_SHARE = "low_share"
BLOCK_LOW_SCORE = "low_score"
BLOCK_NO_ANCHOR = "no_anchor"


@dataclass
class BookEvidence:
    """Что известно о книге порции (из regen и заголовка файла)."""
    file_path: str
    author: str = ""
    series: str = ""
    codes: List[str] = field(default_factory=list)
    pubseq: List[str] = field(default_factory=list)


@dataclass
class UnitDecision:
    author: str
    series: str
    files: List[str]
    genre: Optional[str]
    share: float
    score: float
    auto: bool
    blocker: str = ""
    # (сигнал, жанр) -> вес
    signals: Dict[Tuple[str, str], float] = field(default_factory=dict)

    def describe(self) -> str:
        """Краткое объяснение решения для журнала и сообщений."""
        what = f"{self.author} / {self.series}" if self.series else (self.author or self.files[0])
        top = max(self.signals.values(), default=0.0)
        shown = [(k, g, w) for (k, g), w in sorted(self.signals.items(), key=lambda x: -x[1])
                 if w >= max(0.5, top * 0.1)]
        parts = ", ".join(f"{k} → {g} ({w:.1f})" for k, g, w in shown)
        verdict = "авто" if self.auto else f"на решение ({self.blocker})"
        genre = self.genre or "—"
        return f"{what} [{len(self.files)} кн.]: {genre}, {self.share:.0%}, {verdict}; {parts or 'сигналов нет'}"


def _dist(counter) -> Dict[str, float]:
    tot = sum(v for v in counter.values() if v > 0)
    return {g: v / tot for g, v in counter.items() if v > 0} if tot else {}


def _series_parts(series: str) -> List[str]:
    """Компоненты серии из regen (``Корень\\Подсерия``), от частного к общему."""
    return [p for p in reversed(re.split(r"[\\/]", series or "")) if p.strip()]


class GenreVoter:
    def __init__(
        self,
        memory: GenreMemory,
        resolve_code: Callable[[str], Optional[str]],
        genre_names: Iterable[str] = (),
        excluded_codes: Iterable[str] = (),
        confidence: float = 0.8,
        min_score: float = 3.0,
    ):
        self.mem = memory
        self.resolve_code = resolve_code
        self.genre_by_lower = {g.lower(): g for g in genre_names}
        self.excluded = {c.lower() for c in excluded_codes}
        self.confidence = confidence
        self.min_score = min_score

    # ---------- сигналы ----------
    def _codes(self, codes: Iterable[str]) -> List[str]:
        return [c.strip().lower() for c in codes if c.strip() and c.strip().lower() not in self.excluded]

    def code_vote(self, codes: Iterable[str]) -> Counter:
        """Голос кодов одной книги. Выученное из библиотеки, иначе справочник;
        вес умножается на долю «знакомых» кодов — книга с незнакомыми кодами
        голосует слабо (иначе её решал бы случайный знакомый код)."""
        per: List[Dict[str, float]] = []
        unknown = 0
        for c in self._codes(codes):
            name = self.genre_by_lower.get(c)
            if name:  # в теге уже имя жанра, а не код
                per.append({name: 1.0})
                continue
            learned = self.mem.code.get(c)
            if learned and sum(learned.values()) >= CODE_MIN_BOOKS:
                per.append(_dist(learned))
                continue
            g = self.resolve_code(c)
            if g:
                per.append({g: 1.0})
                continue
            unknown += 1
        out: Counter = Counter()
        if not per:
            return out
        familiar = len(per) / (len(per) + unknown)
        for d in per:
            for g, v in d.items():
                out[g] += W_CODES * v / len(per) * familiar
        return out

    def _pub_vote(self, pubseq: Iterable[str]) -> Optional[str]:
        for p in pubseq:
            cnt = self.mem.pub.get(norm_key(p))
            if cnt and sum(cnt.values()) >= PUB_MIN_BOOKS:
                top, n = cnt.most_common(1)[0]
                if n / sum(cnt.values()) >= PUB_PURITY:
                    return top
        return None

    def reliable_genre(self, code: str) -> Optional[str]:
        """Жанр, который код однозначно означает в этой библиотеке: код
        встречался в >= RELIABLE_MIN_BATCHES разных порциях, и не меньше
        RELIABLE_PURITY из них — в одном жанре."""
        per_g = self.mem.code_batches.get(code)
        if not per_g:
            return None
        tot = sum(per_g.values())
        if tot < RELIABLE_MIN_BATCHES:
            return None
        g, n = per_g.most_common(1)[0]
        return g if n / tot >= RELIABLE_PURITY else None

    def unit_vote(self, members: List[BookEvidence]) -> Tuple[Counter, Dict[Tuple[str, str], float]]:
        score: Counter = Counter()
        signals: Dict[Tuple[str, str], float] = defaultdict(float)
        n = len(members)
        codes_sum: Counter = Counter()
        for b in members:
            for g, v in self.code_vote(b.codes).items():
                codes_sum[g] += v / n
            pub = self._pub_vote(b.pubseq)
            if pub:
                score[pub] += W_PUB / n
                signals[("pub", pub)] += W_PUB / n
        for g, v in codes_sum.items():
            score[g] += v
            signals[("codes", g)] += v
        keys = author_keys(members[0].author)
        cnt = self._series_counter(keys, members[0].series)
        if cnt:
            for g, v in _dist(cnt).items():
                score[g] += W_SERIES * v
                signals[("series", g)] += W_SERIES * v
        codes_top = max(codes_sum.values()) / sum(codes_sum.values()) if codes_sum else 0.0
        prof = self._profile(keys)
        if prof and sum(prof.values()) >= PROFILE_MIN_BOOKS and codes_top < PROFILE_IF_CODES_BELOW:
            for g, v in _dist(prof).items():
                score[g] += W_PROFILE * v
                signals[("profile", g)] += W_PROFILE * v
        return score, dict(signals)

    def _series_counter(self, keys: List[str], series: str) -> Optional[Counter]:
        """Жанры серии в библиотеке: сначала весь авторский коллектив, затем
        каждый соавтор; компоненты серии — от частного к общему."""
        for part in _series_parts(series):
            for a in keys:
                cnt = self.mem.series.get((a, norm_key(part)))
                if cnt:
                    return cnt
        return None

    def _profile(self, keys: List[str]) -> Optional[Counter]:
        """Профиль коллектива, а если такого в библиотеке нет — сумма профилей соавторов."""
        if not keys:
            return None
        if keys[0] in self.mem.author:
            return self.mem.author[keys[0]]
        merged: Counter = Counter()
        for a in keys[1:]:
            merged.update(self.mem.author.get(a, {}))
        return merged or None

    # ---------- решение по порции ----------
    @staticmethod
    def units_of(books: List[BookEvidence]) -> List[List[BookEvidence]]:
        """Серия порции — одна единица (все тома вместе), книга без серии — отдельно."""
        units: Dict[tuple, List[BookEvidence]] = {}
        for b in books:
            key = ("s", norm_key(b.author), norm_key(b.series)) if b.series else ("f", b.file_path)
            units.setdefault(key, []).append(b)
        return list(units.values())

    def decide_batch(self, books: List[BookEvidence]) -> List[UnitDecision]:
        units = self.units_of(books)
        votes = [(m, *self.unit_vote(m)) for m in units]
        # голос порции: только единицы с сильными сигналами (не один профиль)
        batch: Counter = Counter()
        batch_books = 0
        for m, sc, sig in votes:
            if sc and {k for k, _g in sig} - {"profile"}:
                for g, v in sc.items():
                    batch[g] += v * len(m)
                batch_books += len(m)
        tot = sum(batch.values())
        homogeneous = tot > 0 and max(batch.values()) / tot >= BATCH_HOMOGENEITY
        strength = min(1.0, tot / batch_books / W_CODES) if batch_books else 0.0

        decisions = []
        for m, sc, sig in votes:
            sc = Counter(sc)
            sig = dict(sig)
            if homogeneous:
                for g, v in batch.items():
                    w = W_BATCH * v / tot * strength
                    sc[g] += w
                    sig[("batch", g)] = sig.get(("batch", g), 0.0) + w
            decisions.append(self._decide(m, sc, sig))
        return decisions

    def _decide(self, members, sc: Counter, sig) -> UnitDecision:
        files = [b.file_path for b in members]
        d = UnitDecision(author=members[0].author, series=members[0].series, files=files,
                         genre=None, share=0.0, score=0.0, auto=False, signals=sig)
        if not sc:
            d.blocker = BLOCK_NO_SIGNAL
            return d
        g, top = sc.most_common(1)[0]
        d.genre, d.score, d.share = g, top, top / sum(sc.values())
        if d.share < self.confidence:
            d.blocker = BLOCK_LOW_SHARE
        elif top < self.min_score:
            d.blocker = BLOCK_LOW_SCORE
        elif not (ANCHORS & {k for k, _g in sig} or self._codes_reliable_for(members, g)):
            d.blocker = BLOCK_NO_ANCHOR
        else:
            d.auto = True
        return d

    def _codes_reliable_for(self, members: List[BookEvidence], genre: str) -> bool:
        """Хотя бы один надёжный код за жанр и ни одного надёжного против."""
        rel = set()
        for b in members:
            for c in self._codes(b.codes):
                r = self.reliable_genre(c)
                if r:
                    rel.add(r)
        return rel == {genre}
