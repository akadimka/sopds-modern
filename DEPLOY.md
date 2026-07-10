# Развёртывание SOPDS-MODERN на TurnKey Linux v.17

TurnKey v.17 основан на Debian 11 (Bullseye).

---

## Требования

| Компонент | Версия |
|-----------|--------|
| Python | **3.13** (строго) |
| uv | последняя |
| gunicorn | ≥ 23.0 (входит в зависимости) |
| БД | SQLite (по умолчанию) или PostgreSQL 17 |
| ОС | TurnKey Linux 17 / Debian 11 |

---

## 1. Подготовка системы

```bash
apt update && apt upgrade -y
apt install -y git curl build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev libffi-dev \
    liblzma-dev libxml2-dev libxslt1-dev libjpeg-dev
```

---

## 2. Установка uv

```bash
curl -Lsf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env   # добавляет uv в PATH

# gunicorn будет запускаться от www-data и должен иметь доступ к
# интерпретатору Python. По умолчанию uv ставит его в ~/.local/share/uv,
# т.е. под /root — а туда www-data попасть не может (drwx------ на /root).
# Поэтому кладём managed-Python в общедоступный каталог:
export UV_PYTHON_INSTALL_DIR=/opt/uv/python
echo 'export UV_PYTHON_INSTALL_DIR=/opt/uv/python' >> /root/.bashrc

uv --version
```

---

## 3. Установка Python 3.13

Python 3.13 отсутствует в стандартных репозиториях Debian 11, а PPA (например, deadsnakes) не всегда доступен на конкретном дистрибутиве/релизе. Вместо системного пакета используем uv — он скачивает собственный переносимый билд Python:

```bash
mkdir -p /opt/uv/python
uv python install 3.13
uv python list   # убедиться, что 3.13.x установлен
```

---

## 4. Клонирование репозитория

```bash
cd /opt
git clone -b master https://github.com/akadimka/sopds-modern.git
cd sopds-modern
```

---

## 5. Установка зависимостей

```bash
uv sync --no-dev
```

uv создаст виртуальное окружение `.venv` в папке проекта и установит все зависимости из `pyproject.toml`.

---

## 6. Настройка окружения (.env)

Скопируйте шаблон и откройте для редактирования:

```bash
cp base.env src/.env
nano src/.env
```

Минимальная конфигурация для **SQLite**:

```env
DJANGO_SETTINGS_MODULE=sopds.settings.base
DEBUG=False
SECRET_KEY_FILE=/opt/sopds-modern/secret_key.txt
SOPDS_VERSION=0.7
SOPDS_SERVER_LOG_LEVEL=WARNING
SOPDS_DB_ENGINE=sqlite
SOPDS_DB_NAME=sopds.db
ALLOWED_HOSTS=<IP-адрес сервера>,localhost
TIME_ZONE=Europe/Moscow
SOPDS_BOOK_PATH=/path/to/your/ebook/library
```

> **Для PostgreSQL** замените строки с DB:
> ```env
> SOPDS_DB_ENGINE=postgres
> SOPDS_DB_NAME=sopds
> SOPDS_DB_USER=sopds
> SOPDS_DB_PASSWORD=yourpassword
> SOPDS_DB_HOST=localhost
> SOPDS_DB_PORT=5432
> ```

---

## 7. Генерация секретного ключа

```bash
cd /opt/sopds-modern
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(50))" \
    > /opt/sopds-modern/secret_key.txt
chmod 600 /opt/sopds-modern/secret_key.txt
```

---

## 8. (Если PostgreSQL) Создание базы данных

```bash
apt install -y postgresql
sudo -u postgres psql <<EOF
CREATE USER sopds WITH PASSWORD 'yourpassword';
CREATE DATABASE sopds OWNER sopds;
EOF
```

---

## 9. Инициализация проекта

```bash
cd /opt/sopds-modern/src

# manage.py по умолчанию использует sopds.settings.local (для разработки,
# требует dev-зависимость debug_toolbar). .env этот дефолт не перекрывает,
# т.к. читается уже после того, как Django выбрал settings-модуль —
# поэтому явно задаём переменную окружения перед запуском команд:
export DJANGO_SETTINGS_MODULE=sopds.settings.base

# Собрать статику
../.venv/bin/python manage.py collectstatic --noinput

# Применить миграции
../.venv/bin/python manage.py migrate

# Создать администратора
../.venv/bin/python manage.py createsuperuser
```

> Сокращение для удобства — добавьте `.venv/bin` в PATH или используйте полный путь.

---

## 10. Настройка папки fb2_data

```bash
# Скопируйте ваш genres.xml
cp /path/to/genres.xml /opt/sopds-modern/src/fb2_data/genres.xml

# Убедитесь что папка для CSV существует
mkdir -p /opt/sopds-modern/src/fb2_data/csv
```

> Настройку путей через **FB2Parser → Настройки** в браузере сделаете после того, как сервис запустится — см. шаг 13 «Проверка».

---

## 11. Настройка systemd-сервиса

Создайте файл сервиса:

```bash
nano /etc/systemd/system/sopds-modern.service
```

Содержимое:

```ini
[Unit]
Description=SOPDS-MODERN (gunicorn)
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/sopds-modern/src
EnvironmentFile=/opt/sopds-modern/src/.env
ExecStart=/opt/sopds-modern/.venv/bin/gunicorn \
    --config "python:sopds.settings.gunicorn" \
    sopds.wsgi
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Активируйте и запустите:

```bash
chown -R www-data:www-data /opt/sopds-modern
systemctl daemon-reload
systemctl enable sopds-modern
systemctl start sopds-modern
systemctl status sopds-modern
```

Сервис запустится на порту **8008**.

---

## 12. Настройка Apache (обратный прокси)

TurnKey поставляется с Apache. Включаем нужные модули и создаём конфиг:

```bash
a2enmod proxy proxy_http headers
apt install -y apache2
nano /etc/apache2/sites-available/sopds-modern.conf
```

Содержимое:

```apache
<VirtualHost *:80>
    ServerName <IP-адрес или домен>

    ProxyPreserveHost On
    ProxyPass        / http://127.0.0.1:8008/
    ProxyPassReverse / http://127.0.0.1:8008/

    RequestHeader set X-Forwarded-Proto "http"

    # Увеличенный таймаут для долгих операций (сканирование, нормализация)
    ProxyTimeout 120

    ErrorLog  ${APACHE_LOG_DIR}/sopds-modern-error.log
    CustomLog ${APACHE_LOG_DIR}/sopds-modern-access.log combined
</VirtualHost>
```

```bash
a2ensite sopds-modern
a2dissite 000-default          # отключить дефолтный сайт (опционально)
apache2ctl configtest
systemctl reload apache2
```

---

## 13. Проверка

Откройте в браузере: `http://<IP-адрес сервера>/`

- Главная страница SOPDS → статистика библиотеки
- `/fb2parser/` → раздел FB2Parser (только для суперпользователя)
- `/admin/` → Django Admin

Войдите как суперпользователь и в разделе **FB2Parser → Настройки** заполните:
- **Путь к библиотеке** — папка с вашими FB2-файлами (`SOPDS_BOOK_PATH` из `.env`)
- **Путь к файлу жанров** — `/opt/sopds-modern/src/fb2_data/genres.xml`

---

## Обновление

```bash
cd /opt/sopds-modern
git pull
uv sync --no-dev
cd src
export DJANGO_SETTINGS_MODULE=sopds.settings.base
../.venv/bin/python manage.py migrate
../.venv/bin/python manage.py collectstatic --noinput
systemctl restart sopds-modern
```

---

## Устранение неполадок

| Проблема                                      | Решение                                                                               |
|-----------------------------------------------|---------------------------------------------------------------------------------------|
| `502 Bad Gateway`                             | `systemctl status sopds-modern` — проверить ошибки gunicorn                           |
| `ALLOWED_HOSTS` ошибка                        | Добавить IP сервера в `.env` → `ALLOWED_HOSTS`                                        |
| Статика не грузится                           | Проверить `collectstatic`, убедиться что `whitenoise` в MIDDLEWARE                    |
| Нет доступа к `/fb2parser/`                   | Войти как суперпользователь (`is_superuser=True`)                                     |
| Ошибка прав на файлы                          | `chown -R www-data:www-data /opt/sopds-modern`                                        |
| Apache: `AH00961: failed to make connection`  | Gunicorn не запущен — `systemctl start sopds-modern`                                  |
| Apache: `403 Forbidden` на статику            | Whitenoise обслуживает статику через gunicorn — `ProxyPass /` должен покрывать всё    |
| `systemd`: `status=203/EXEC`                  | `www-data` не может выполнить Python из `.venv` — Python установлен под `/root` (см. шаг 2, `UV_PYTHON_INSTALL_DIR`) |
