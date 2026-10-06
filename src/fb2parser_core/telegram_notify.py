"""Уведомления автосинхронизации в Telegram — docs/watch-folder-autosync-design.md.

Только отправка, без постоянно работающего бота: плановая задача сама
вызывает Bot API (HTTPS-запросы через стандартную библиотеку — никаких
новых зависимостей). Новинки — одной сводкой в канал; админу — итог
запуска, спорные порции и сбои.
"""
from __future__ import annotations

import html
import json
import logging
import re
import urllib.error
import urllib.request
from collections import OrderedDict, defaultdict
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"
MAX_MESSAGE = 4000  # лимит Telegram — 4096 символов; запас на разметку


_TOKEN_RE = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{30,}\b")


def extract_token(text: str) -> str:
    """Токен бота из вставленного текста: в поле часто вставляют всё
    сообщение @BotFather целиком. '' — токена в тексте нет."""
    m = _TOKEN_RE.search(text or "")
    return m.group(0) if m else ""


class TelegramError(Exception):
    def __init__(self, description: str, code: int = 0):
        super().__init__(description)
        self.description = description
        self.code = code


class TelegramClient:
    def __init__(self, token: str, proxy: str = "", timeout: float = 20.0,
                 opener: Optional[Callable[..., Any]] = None):
        self.token = extract_token(token) or token.strip()
        self.timeout = timeout
        if opener is not None:
            self._open = opener
        else:
            handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
            self._open = urllib.request.build_opener(*handlers).open

    def call(self, method: str, _timeout: Optional[float] = None, **params) -> Any:
        if not self.token:
            raise TelegramError("token is empty")
        req = urllib.request.Request(
            API_URL.format(token=self.token, method=method),
            data=json.dumps(params).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._open(req, timeout=_timeout or self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:  # Telegram отвечает JSON-ом и с кодом ошибки
            try:
                body = json.loads(e.read().decode("utf-8"))
            except (ValueError, OSError):
                raise TelegramError(str(e), e.code) from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise TelegramError(f"network: {e}") from e
        if not body.get("ok"):
            raise TelegramError(body.get("description", "unknown error"), body.get("error_code", 0))
        return body.get("result")

    def send_message(self, chat_id: str, text: str, reply_markup: Optional[dict] = None) -> Optional[dict]:
        """Отправить (длинный текст — несколькими сообщениями); кнопки — у
        последнего. Возвращает последнее отправленное сообщение."""
        chunks = split_message(text)
        sent = None
        for i, chunk in enumerate(chunks):
            params: Dict[str, Any] = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                                      "disable_web_page_preview": True}
            if reply_markup and i == len(chunks) - 1:
                params["reply_markup"] = reply_markup
            sent = self.call("sendMessage", **params)
        return sent

    def edit_message(self, chat_id, message_id: int, text: str, reply_markup: Optional[dict] = None) -> None:
        params: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text[:MAX_MESSAGE],
                                  "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        self.call("editMessageText", **params)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def get_updates(self, offset: Optional[int] = None, timeout: int = 0) -> List[dict]:
        """Long polling: ждать новые события до timeout секунд."""
        params: Dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", _timeout=timeout + 15, **params) or []

    def chats(self) -> List[Dict[str, str]]:
        """Кто писал боту и в каких каналах он публиковал/видел сообщения —
        подсказка для поля chat id (getUpdates хранит последние ~24 часа)."""
        return chats_from_updates(self.call("getUpdates") or [])


def chats_from_updates(updates: List[dict]) -> List[Dict[str, str]]:
    found: "OrderedDict[str, Dict[str, str]]" = OrderedDict()
    for upd in updates:
        for key in ("message", "channel_post", "my_chat_member", "edited_message"):
            chat = (upd.get(key) or {}).get("chat")
            if chat:
                title = chat.get("title") or " ".join(
                    x for x in (chat.get("first_name"), chat.get("last_name")) if x)
                if chat.get("username"):
                    title += f" (@{chat['username']})"
                found.setdefault(str(chat["id"]), {"id": str(chat["id"]), "type": chat.get("type", ""),
                                                   "title": title})
    return list(found.values())


ERR_TOKEN, ERR_CHAT, ERR_START, ERR_RIGHTS, ERR_NETWORK, ERR_CONFLICT, ERR_OTHER = (
    "token", "chat", "start", "rights", "network", "conflict", "other")


def error_kind(err: TelegramError) -> str:
    """Вид ошибки Telegram — по нему страница настроек подсказывает, что делать."""
    d = err.description.lower()
    if err.code == 401 or "unauthorized" in d or "token is empty" in d:
        return ERR_TOKEN
    if "bot can't initiate conversation" in d or "bot was blocked" in d:
        return ERR_START
    if "chat not found" in d:
        return ERR_CHAT
    if "not a member" in d or "not enough rights" in d or "need administrator rights" in d:
        return ERR_RIGHTS
    if d.startswith("network:"):
        return ERR_NETWORK
    if err.code == 409 or "conflict" in d:  # события уже забирает работающий процесс бота
        return ERR_CONFLICT
    return ERR_OTHER


_EXPLAIN = {
    ERR_TOKEN: "неверный токен бота",
    ERR_START: "бот не может написать первым — нужно нажать «Старт» у бота",
    ERR_CHAT: "чат не найден",
    ERR_RIGHTS: "бот не администратор канала",
    ERR_NETWORK: "нет связи с api.telegram.org",
}


def explain_error(err: TelegramError) -> str:
    """Короткая причина для журнала/лога планового запуска."""
    kind = error_kind(err)
    return f"{_EXPLAIN[kind]} ({err.description})" if kind in _EXPLAIN else err.description


def split_message(text: str, limit: int = MAX_MESSAGE) -> List[str]:
    """Разбить по строкам на части не длиннее limit."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        parts.append(cur)
    return parts


def _e(s: str) -> str:
    return html.escape(s or "", quote=False)


def build_digest(rows: List[Dict[str, Any]], link: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None) -> str:
    """Сводка новинок для канала: жанр → автор → серия (число книг) / книга.

    rows — книги журнала с outcome=moved (genre, author, series, title).
    link(item) — ссылка на страницу серии/книги или None.
    """
    tree: Dict[str, Dict[str, Dict[str, List[Dict[str, Any]]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for r in rows:
        tree[r.get("genre") or "—"][r.get("author") or "—"][r.get("series") or ""].append(r)
    lines = [f"📚 <b>Новые книги в библиотеке: {len(rows)}</b>"]
    for genre in sorted(tree):
        lines += ["", f"<b>{_e(genre)}</b>"]
        for author in sorted(tree[genre]):
            for series, books in sorted(tree[genre][author].items()):
                if series:
                    label = f"«{_e(series)}»" + (f" — {len(books)} кн." if len(books) > 1 else "")
                    url = link({"kind": "series", "series": series, "author": author, "genre": genre}) if link else None
                    lines.append(f"• {_e(author)}: " + (f'<a href="{_e(url)}">{label}</a>' if url else label))
                else:
                    for b in books:
                        title = _e(b.get("title") or "")
                        url = link({"kind": "book", "title": b.get("title") or "", "author": author}) if link else None
                        shown = f'<a href="{_e(url)}">{title}</a>' if url and title else title
                        lines.append(f"• {_e(author)}" + (f": {shown}" if shown else ""))
    return "\n".join(lines)


def build_admin_report(summary: Dict[str, Any], pending: List[Dict[str, Any]], inbox_url: str = "") -> str:
    """Итог запуска для админа.

    summary — status/mode/moved/kept/removed/error/notes; pending — порции,
    ждущие решения: [{"batch": ..., "books": n}].
    """
    status = summary.get("status")
    if status == "error":
        head = f"❌ <b>Автосинхронизация: сбой</b>\n{_e(str(summary.get('error') or ''))}"
    elif summary.get("mode") == "dry_run":
        head = ("🗂 <b>Автосинхронизация</b> (пробный режим — ничего не перемещено)\n"
                f"Было бы перемещено автоматически: {summary.get('would_move', 0)}")
    else:
        head = ("🗂 <b>Автосинхронизация</b>\n"
                f"Перемещено: {summary.get('moved', 0)}, оставлено синхронизацией: {summary.get('kept', 0)}, "
                f"удалено как дубликаты: {summary.get('removed', 0)}")
    lines = [head]
    notes = summary.get("notes") or {}
    if notes.get("reconciliation_notes"):
        lines.append(f"⚠ Нужна сверка автора: {len(notes['reconciliation_notes'])} файл(ов)")
    if notes.get("genre_conflict_notes"):
        lines.append(f"⚠ Конфликт жанра внутри серии: {len(notes['genre_conflict_notes'])} сер.")
    if pending:
        lines += ["", f"<b>Ждут решения: {sum(p['books'] for p in pending)} кн.</b>"]
        lines += [f"• {_e(p['batch'])}: {p['books']}" for p in pending]
    if inbox_url and (pending or notes.get("reconciliation_notes") or notes.get("genre_conflict_notes")):
        lines += ["", f'<a href="{_e(inbox_url)}">Открыть «Входящие»</a>']
    return "\n".join(lines)


PROMPT_UNITS = 8


def build_batch_prompt(batch, options: List[str]) -> str:
    """Сообщение админу о спорной порции (кнопки жанров — под ним).

    batch — InboxBatch (name, books, units с author/series/files)."""
    lines = [f"📥 <b>Ждёт решения:</b> {_e(batch.name)} — {batch.books} кн."]
    for u in batch.units[:PROMPT_UNITS]:
        who = _e(u.author or "—") + (f" / «{_e(u.series)}»" if u.series else "")
        lines.append(f"• {who}: {len(u.files)} кн.")
    if len(batch.units) > PROMPT_UNITS:
        lines.append(f"… и ещё {len(batch.units) - PROMPT_UNITS}")
    if options:
        lines += ["", "Выберите жанр — он будет назначен <b>всей порции</b>, книги уйдут в библиотеку. "
                      "Разные жанры для разных книг — во «Входящих»."]
    else:
        lines += ["", "Подсказки нет — выберите жанр во «Входящих»."]
    return "\n".join(lines)
