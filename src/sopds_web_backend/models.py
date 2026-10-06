from django.contrib.auth.models import User
from django.db import models


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=150, blank=True, default="")
    bio = models.TextField(blank=True, default="")

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
