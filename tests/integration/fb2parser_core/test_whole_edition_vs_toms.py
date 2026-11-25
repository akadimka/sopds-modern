"""Найдено пользователем в компиляции «Акунин Борис / Приключения магистра»
(2026-11-25): «Тетралогия в 7 книгах» с нумерацией 1, 2, 2.1, …

Файлы «Akunin_Priklyucheniya_Nikolasa_Fandorina_N_….fb2», номер книги —
из <sequence number>: №2 «Внеклассное чтение» + «… Том 1» + «… Том 2», №3
«Ф. М.» + «Ф. М. Том 1» + «Ф. М. Том 2».

- «Внеклассное чтение. Том 2» получал позицию 2 (номер тома = номер книги)
  и вставал перед «Томом 1» (2.1), цельное издание уходило в дубли.
- Цельное «Ф. М.» и оба его тома шли в сборник вместе — текст дважды.

Решение — `FB2CompilerService._group_whole_edition_vs_toms`: на позиции
остаётся цельное издание, если в нём не меньше 95% текста томов, иначе тома.
"""
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info>
<genre>detective</genre>
<author><first-name>Борис</first-name><last-name>Акунин</last-name></author>
<book-title>{title}</book-title>
<sequence name="Приключения магистра" number="{n}"/>
</title-info></description>
<body><section>{text}</section></body>
</FictionBook>
"""

_FOLDER = ("Акунин Борис - Сборник", "Приключения магистра")
_STEM = "Akunin_Priklyucheniya_Nikolasa_Fandorina"


def _paragraphs(seed, count):
    return "".join(f"<p>Книга {seed}, абзац {i}: текст романа для проверки объёма.</p>"
                   for i in range(count))


def _write(folder, name, title, n, text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(_FB2.format(title=title, n=n, text=text), encoding="utf-8")


def _groups(tmp_path):
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    return FB2CompilerService().find_groups(records, tmp_path)


def _library(tmp_path, whole_share=1.0):
    folder = tmp_path.joinpath(*_FOLDER)
    _write(folder, f"{_STEM}_1_Altyin-Tolobas.fb2", "Алтын-Толобас", 1, _paragraphs("A", 40))
    for n, slug, title in ((2, "Vneklassnoe_chtenie.", "Внеклассное чтение"), (3, "F._M.", "Ф. М.")):
        tom1, tom2 = _paragraphs(f"{n}-1", 40), _paragraphs(f"{n}-2", 40)
        whole = (tom1 + tom2)[: int(len(tom1 + tom2) * whole_share)]
        _write(folder, f"{_STEM}_{n}_{slug}.fb2", title, n, whole)
        _write(folder, f"{_STEM}_{n}_{slug}_Tom_1.fb2", f"{title.rstrip('.')}. Том 1", n, tom1)
        _write(folder, f"{_STEM}_{n}_{slug}_Tom_2.fb2", f"{title.rstrip('.')}. Том 2", n, tom2)
    _write(folder, f"{_STEM}_4_Sokol_i_Lastochka.fb2", "Сокол и Ласточка", 4, _paragraphs("D", 40))
    return folder


def test_whole_editions_kept_toms_are_duplicates(tmp_path):
    _library(tmp_path)
    [group] = _groups(tmp_path)
    assert [b.volume_label for b in group.books] == ["1", "2", "3", "4"]
    assert [b.abs_path.name for b in group.books] == [
        f"{_STEM}_1_Altyin-Tolobas.fb2", f"{_STEM}_2_Vneklassnoe_chtenie..fb2",
        f"{_STEM}_3_F._M..fb2", f"{_STEM}_4_Sokol_i_Lastochka.fb2"]
    assert sorted(p.name for p in group.duplicate_paths) == sorted(
        f"{_STEM}_{n}_{slug}_Tom_{t}.fb2"
        for n, slug in ((2, "Vneklassnoe_chtenie."), (3, "F._M.")) for t in (1, 2))


def test_abridged_whole_edition_loses_to_toms(tmp_path):
    _library(tmp_path, whole_share=0.6)
    [group] = _groups(tmp_path)
    assert [b.volume_label for b in group.books] == ["1", "2.1", "2.2", "3.1", "3.2", "4"]
    assert sorted(p.name for p in group.duplicate_paths) == [
        f"{_STEM}_2_Vneklassnoe_chtenie..fb2", f"{_STEM}_3_F._M..fb2"]


def test_tom_number_equal_to_book_number_is_inner_tom(tmp_path):
    """Без цельного издания: «Том 2» второй книги — 2.2 после 2.1, не 2."""
    folder = _library(tmp_path)
    for name in (f"{_STEM}_2_Vneklassnoe_chtenie..fb2", f"{_STEM}_3_F._M..fb2"):
        (folder / name).unlink()
    [group] = _groups(tmp_path)
    assert [(b.volume_label, b.abs_path.name) for b in group.books][1:3] == [
        ("2.1", f"{_STEM}_2_Vneklassnoe_chtenie._Tom_1.fb2"),
        ("2.2", f"{_STEM}_2_Vneklassnoe_chtenie._Tom_2.fb2")]


def test_books_named_tom_n_with_shifted_edition_stay_whole(tmp_path):
    """Розальев, «Вертел я ваши кланы!» (Test2): книги «Том N» с номером N
    и рядом издание со сдвигом («02. … Том 3» с номером 2). «Том 2» не
    становится 2.2 — тома на позиции не дают ряд 1..K."""
    series = "Вертел я ваши кланы!"
    a = tmp_path / "Розальев Андрей" / series
    b = tmp_path / "Розальев Андрей" / "Другое издание" / series
    for n in (1, 2, 3):
        (a / ".").mkdir(parents=True, exist_ok=True)
        (a / f"0{n}_{series} Том {n}.fb2").write_text(
            _FB2.format(title=f"{series} Том {n}", n=n, text=_paragraphs(f"R{n}", 40))
            .replace("Приключения магистра", series), encoding="utf-8")
    for n in (2, 3):
        b.mkdir(parents=True, exist_ok=True)
        (b / f"0{n}. {series} Том {n + 1}.fb2").write_text(
            _FB2.format(title=f"{series} Том {n + 1}", n=n, text=_paragraphs(f"S{n}", 40))
            .replace("Приключения магистра", series), encoding="utf-8")
    labels = [lbl for g in _groups(tmp_path) for lbl in (bk.volume_label for bk in g.books)]
    assert not [lbl for lbl in labels if lbl in ("2.2", "3.3")]
