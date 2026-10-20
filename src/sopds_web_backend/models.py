from django.contrib.auth.models import User
from django.db import models


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=150, blank=True, default="")
    bio = models.TextField(blank=True, default="")
    # Библиотека пользователя открыта всем пользователям (docs/multiuser-design.md);
    # у администратора после миграции — True (все видят то же, что раньше).
    library_public = models.BooleanField(default=False)
    # Личные лимиты: NULL — значение по умолчанию из настроек, 0 — без ограничений.
    quota_library_mb = models.PositiveIntegerField(null=True, blank=True)
    quota_books = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

    def __str__(self):
        return f"Profile({self.user.username})"


class TelegramLink(models.Model):
    """Привязка аккаунта SOPDS к Telegram — только для бота и канала
    библиотеки, на вход на сайт не влияет (docs/telegram-library-bot.md)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="telegram")
    telegram_id = models.BigIntegerField(null=True, blank=True, unique=True)
    telegram_name = models.CharField(max_length=200, blank=True, default="")
    # одноразовый код привязки (пользователь отправляет его боту)
    code = models.CharField(max_length=16, blank=True, default="", db_index=True)
    code_expires = models.DateTimeField(null=True, blank=True)
    linked_at = models.DateTimeField(null=True, blank=True)
    # бот одобрил его заявку на вступление в канал
    in_channel = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Telegram link"
        verbose_name_plural = "Telegram links"

    def __str__(self):
        return f"TelegramLink({self.user.username} → {self.telegram_id or '—'})"

    @property
    def linked(self) -> bool:
        return self.telegram_id is not None


class TelegramFile(models.Model):
    """Файл книги, уже загруженный в Telegram ботом библиотеки: повторно та
    же книга в том же формате уходит по file_id мгновенно, без загрузки и
    конвертации. filesize — чтобы заменённый файл книги не подменялся старым."""

    book = models.ForeignKey("opds_catalog.Book", on_delete=models.CASCADE, related_name="telegram_files")
    fmt = models.CharField(max_length=8)
    filesize = models.IntegerField(default=0)
    file_id = models.CharField(max_length=255)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Telegram file"
        verbose_name_plural = "Telegram files"
        constraints = [models.UniqueConstraint(fields=["book", "fmt"], name="telegram_file_book_fmt")]


class OpdsPassword(models.Model):
    """Отдельный пароль для читалок (OPDS, HTTP Basic): подходит только к
    /opds/, не к сайту. Читалка хранит его и шлёт с каждым запросом —
    утечка из её настроек не даёт войти на сайт, а отзывается он без смены
    основного пароля. Хранится только SHA-256: пароль случайный (100 бит),
    медленный хеш не нужен."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="opds_password")
    token_hash = models.CharField(max_length=64)
    created = models.DateTimeField()
    last_used = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "OPDS password"
        verbose_name_plural = "OPDS passwords"

    def __str__(self):
        return f"OpdsPassword({self.user.username})"


class TotpDevice(models.Model):
    """Приложение-аутентификатор (TOTP, RFC 6238): второй фактор входа на сайт.

    Пока confirmed=False — подключение начато, но первый код из приложения
    ещё не введён (секрет есть только на этой странице профиля). last_step —
    номер 30-секундного шага последнего принятого кода: тот же код (и более
    ранние) повторно не принимаются. Потерян телефон —
    `manage.py totp_reset <логин>` на сервере."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp")
    secret = models.CharField(max_length=64)  # base32
    confirmed = models.BooleanField(default=False)
    created = models.DateTimeField()
    last_step = models.BigIntegerField(default=0)

    class Meta:
        verbose_name = "TOTP device"
        verbose_name_plural = "TOTP devices"

    def __str__(self):
        return f"TotpDevice({self.user.username}, {'on' if self.confirmed else 'pending'})"
