"""Память автосинхронизации — docs/watch-folder-autosync-design.md, «Память»."""
import zipfile

from fb2parser_core.genre_assign import GenreAssignmentService
from fb2parser_core.library_memory import (
    NO_GENRE_FOLDER,
    LibraryMemory,
    author_keys,
    extract_evidence,
    norm_key,
    parse_library_path,
    read_evidence,
)

_TEXT = "Глава первая. " + "Длинный текст книги, который не меняется при синхронизации. " * 40

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
{genres}
<author><first-name>Иван</first-name><last-name>Иванов</last-name></author>
<book-title>{title}</book-title>
</title-info>
<src-title-info><genre>prose_classic</genre></src-title-info>
<publish-info><publisher>Изд</publisher><sequence name="Новый мировой триллер" number="3"/></publish-info>
</description>
<body><title><p>{title}</p></title><section><p>{text}</p></section></body>
</FictionBook>
"""


def _fb2(genres=("sf_action", "det_action"), title="Книга", text=_TEXT):
    return _FB2.format(genres="\n".join(f"<genre>{g}</genre>" for g in genres), title=title, text=text)


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_norm_key_and_author_keys():
    assert norm_key("Сапфир Олег") == norm_key("олег  сапфир")
    assert norm_key("Ёлкин") == norm_key("елкин")
    keys = author_keys("Винокуров Юрий, Сапфир Олег")
    assert keys[0] == norm_key("Винокуров Юрий, Сапфир Олег")
    assert norm_key("Сапфир Олег") in keys and norm_key("Винокуров Юрий") in keys
    assert author_keys("Сапфир Олег") == [norm_key("Сапфир Олег")]


def test_extract_evidence_reads_title_info_genres_and_publisher_series():
    ev = extract_evidence(_fb2())
    assert ev.codes == ["sf_action", "det_action"]  # без жанров оригинала (src-title-info)
    assert ev.pubseq == ["Новый мировой триллер"]
    assert ev.fingerprint


def test_short_body_has_no_fingerprint():
    assert extract_evidence(_fb2(text="Коротко.")).fingerprint is None


def test_fingerprint_depends_only_on_body():
    # синхронизация переписывает <description> (название, жанр) — тело не трогает
    original = _fb2()
    patched = original.replace("<book-title>Книга</book-title>", "<book-title>Другое название</book-title>")
    patched = patched.replace("<genre>sf_action</genre>", "<genre>Фантастика</genre>")
    assert patched != original
    assert extract_evidence(patched).fingerprint == extract_evidence(original).fingerprint
    assert extract_evidence(_fb2(text=_TEXT + "!")).fingerprint != extract_evidence(original).fingerprint


def test_parse_library_path():
    assert parse_library_path("Фантастика/Автор/Мир/Серия/книга.fb2") == ("Фантастика", "Автор", ["Мир", "Серия"])
    assert parse_library_path("Фантастика/Автор/книга.fb2") == ("Фантастика", "Автор", [])


def test_genre_rewrite_keeps_fingerprint_and_recorder_gets_original_codes(tmp_path):
    book = _write(tmp_path / "src" / "Порция" / "книга.fb2", _fb2())
    before = read_evidence(book)
    memory = LibraryMemory(tmp_path / "mem.db")
    seen = []

    def recorder(path, content, batch):
        seen.append(batch)
        memory.record_from_text(content, batch, "user")

    svc = GenreAssignmentService(logger=_Silent(), codes_recorder=recorder)
    assert svc.assign_genre_to_folder(str(tmp_path / "src" / "Порция"), "Фантастика") == 1
    after = read_evidence(book)
    assert after.codes == ["Фантастика"]
    assert after.fingerprint == before.fingerprint
    assert seen == [str(tmp_path / "src" / "Порция")]
    # повторное назначение уже переписанного файла не затирает исходные коды
    svc.assign_genre_to_folder(str(tmp_path / "src" / "Порция"), "Детектив")
    with memory._connect() as conn:
        assert conn.execute("SELECT codes FROM orig_codes").fetchall() == [("sf_action\x1fdet_action",)]


def test_refresh_build_and_incremental_update(tmp_path):
    lib = tmp_path / "lib"
    a = _write(lib / "Фантастика" / "Иванов Иван" / "Звёзды" / "1.fb2", _fb2(genres=["Фантастика"], text=_TEXT + "1"))
    _write(lib / NO_GENRE_FOLDER / "Иванов Иван" / "2.fb2", _fb2(genres=["x"], text=_TEXT + "2"))
    zpath = lib / "Детектив" / "Петров Пётр" / "3.fb2.zip"
    zpath.parent.mkdir(parents=True)
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("3.fb2", _fb2(genres=["Детектив"], text=_TEXT + "3"))
    memory = LibraryMemory(tmp_path / "mem.db")
    assert memory.refresh(lib) == {"total": 3, "updated": 3, "removed": 0}
    assert memory.refresh(lib) == {"total": 3, "updated": 0, "removed": 0}

    # исходные коды книги «1» — из порции, где она была до синхронизации
    fp = read_evidence(a).fingerprint
    assert memory.record_orig_codes(fp, ["sf_action"], "Порция A", "bootstrap")
    mem = memory.build()
    assert mem.series[(norm_key("Иванов Иван"), norm_key("Звёзды"))] == {"Фантастика": 1}
    assert mem.author[norm_key("Петров Пётр")] == {"Детектив": 1}
    assert mem.pub[norm_key("Новый мировой триллер")] == {"Фантастика": 1, "Детектив": 1}
    assert mem.code["sf_action"] == {"Фантастика": 1}
    assert mem.code_batches["sf_action"] == {"Фантастика": 1}
    assert NO_GENRE_FOLDER not in mem.author[norm_key("Иванов Иван")]

    a.unlink()
    assert memory.refresh(lib)["removed"] == 1
    assert "sf_action" not in memory.build().code


def test_code_batches_count_distinct_batches(tmp_path):
    lib = tmp_path / "lib"
    memory = LibraryMemory(tmp_path / "mem.db")
    for i, batch in enumerate(["П1", "П1", "П2", "П3"]):
        p = _write(lib / "Фантастика" / f"Автор{i}" / f"{i}.fb2", _fb2(genres=["Фантастика"], text=_TEXT + str(i)))
        memory.refresh(lib)
        memory.record_orig_codes(read_evidence(p).fingerprint, ["sf_action"], batch, "user")
    mem = memory.build()
    assert mem.code["sf_action"]["Фантастика"] == 4
    assert mem.code_batches["sf_action"]["Фантастика"] == 3


class _Silent:
    def log(self, *_a, **_k):
        pass
