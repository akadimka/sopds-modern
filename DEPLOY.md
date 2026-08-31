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
| memcached | обязателен — общее состояние фоновых задач между worker-процессами gunicorn |
| ОС | TurnKey Linux 17 / Debian 11 |

---

## 1. Подготовка системы

```bash
apt update && apt upgrade -y
apt install -y git curl build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev libffi-dev \
    liblzma-dev libxml2-dev libxslt1-dev libjpeg-dev memcached
```

### Настройка memcached

gunicorn поднимает несколько worker-процессов (`workers = nproc*2+1`, см.
`sopds.settings.gunicorn`), у каждого своя память. Прогресс фоновых задач
(скан, нормализация, синхронизация, компилятор) хранится в общем кеше —
без memcached статус этих операций будет случайно "слетать" на "не
запущено", если запрос попадёт на другой worker.

Увеличьте лимит памяти и максимальный размер элемента (по умолчанию 64 МБ
/ 1 МБ — этого мало для прогресса синхронизации по большой библиотеке):

```bash
nano /etc/memcached.conf
```

Найдите строку `-m 64` и замените на:

```
-m 128
```

Добавьте (или раскомментируйте) строку с максимальным размером элемента:

```
-I 16m
```

Примените и включите автозапуск:

```bash
systemctl restart memcached
systemctl enable memcached
systemctl status memcached   # должен слушать 127.0.0.1:11211
```

> По умолчанию Django-приложение подключается к `127.0.0.1:11211`. Если
> memcached работает на другом хосте/порту, переопределите это через
> `MEMCACHED_LOCATION` в `.env` (шаг 6).

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

> **Опционально** — если memcached работает не на `127.0.0.1:11211`
> (другой хост/порт):
> ```env
> MEMCACHED_LOCATION=127.0.0.1:11211
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

# Скомпилировать переводы (.po → .mo). Без этого шага переключение
# языка визуально ничего не меняет: gettext молча падает обратно
# на исходные строки, если .mo нет.
../.venv/bin/python compile_messages.py

# Собрать статику
../.venv/bin/python manage.py collectstatic --noinput

# Применить миграции
../.venv/bin/python manage.py migrate

# Создать администратора
../.venv/bin/python manage.py createsuperuser
```

> Сокращение для удобства — добавьте `.venv/bin` в PATH или используйте полный путь.

---

## 10. Настройка папки fb2_data и логов

```bash
# Скопируйте ваш genres.xml
cp /path/to/genres.xml /opt/sopds-modern/src/fb2_data/genres.xml

# Убедитесь что папка для CSV существует
mkdir -p /opt/sopds-modern/src/fb2_data/csv

# Папка для логов (log/sopds-modern.log, log/sopds-scaner.log) — не создаётся
# автоматически, gunicorn упадёт с FileNotFoundError без неё
mkdir -p /opt/sopds-modern/src/log
```

> Настройку путей через **FB2Parser → Настройки** в браузере сделаете после того, как сервис запустится — см. шаг 19 «Проверка».

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
Environment=SOPDS_MANAGED_BY_SYSTEMD=1
ExecStart=/opt/sopds-modern/.venv/bin/gunicorn \
    --config "python:sopds.settings.gunicorn" \
    sopds.wsgi
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> **`SOPDS_MANAGED_BY_SYSTEMD=1` обязателен**, если рейтинги Samlib/author.today
> когда-либо будут включены (шаги 14-15). Без него включение галочки в
> Settings запустит сбор рейтингов ещё и в потоке внутри gunicorn-воркера —
> поверх уже работающих `sopds-samlib`/`sopds-authortoday`, вдвое увеличивая
> частоту запросов, а выключение галочки остановит эти systemd-сервисы
> (через общий флаг в кеше), причём `Restart=on-failure` их не поднимет,
> т.к. выход будет "чистым". С этой переменной оба вызова — no-op, сайты
> опрашиваются только независимыми systemd-сервисами.

Активируйте и запустите:

```bash
chown -R www-data:www-data /opt/sopds-modern
systemctl daemon-reload
systemctl enable sopds-modern
systemctl start sopds-modern
systemctl status sopds-modern
```

Сервис запустится на порту **8008**, на всех интерфейсах (`bind = 0.0.0.0:8008` в `sopds.settings.gunicorn`). Это значит, что сайт уже доступен напрямую по адресу `http://<IP-адрес сервера>:8008/` — без Apache. Если хотите открывать сайт именно так (по IP и порту 8008, без reverse-прокси на 80), просто убедитесь, что порт 8008 разрешён в firewall, и переходите сразу к шагу 13, пропустив Apache.

Шаг 12 нужен, только если хотите отдавать сайт на стандартном порту 80 (или с доменным именем/SSL) через reverse-прокси.

---

## 12. (Опционально) Настройка Apache (обратный прокси на порт 80)

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

## 13. Конвертация книг (EPUB / AZW3 / MOBI)

Для конвертации FB2 в другие форматы используется **Calibre** (`ebook-convert`).

### Установка

```bash
apt install -y calibre
which ebook-convert   # → /usr/bin/ebook-convert
```

> Calibre — крупный пакет (~500 МБ). На TurnKey/Debian он доступен прямо из репозитория, дополнительных источников не нужно.

### Настройка в SOPDS

После запуска сервиса войдите как суперпользователь и откройте **Settings** (`/web/settings/`):

| Поле                      | Значение                  |
|---------------------------|---------------------------|
| fb2→epub converter path   | `/usr/bin/ebook-convert`  |
| fb2→azw3 converter path   | `/usr/bin/ebook-convert`  |
| fb2→mobi converter path   | `/usr/bin/ebook-convert`  |
| **Temp directory**        | `/tmp`                    |

> **Temp directory обязателен.** Без него конвертация завершится ошибкой 404 — конвертер не знает куда записать временный файл. Рекомендуется `/tmp` или отдельная папка с правами записи для `www-data`.

Убедитесь что `/tmp` доступен для записи пользователю `www-data`:

```bash
ls -ld /tmp   # должно быть drwxrwxrwt
```

Если используете отдельную папку:

```bash
mkdir -p /opt/sopds-modern/tmp
chown www-data:www-data /opt/sopds-modern/tmp
```

### Форматы и Kindle

| Формат    | Назначение                                     |
|-----------|------------------------------------------------|
| EPUB      | Универсальный — PocketBook, Kobo, iOS, Android |
| AZW3      | Kindle (новые устройства, лучше MOBI)          |
| MOBI      | Kindle (старые устройства)                     |

> Calibre 6+ официально рекомендует AZW3 вместо MOBI для Kindle. Кнопка AZW3 отображается только когда путь конвертера заполнен.

---

## 14. (Опционально) Рейтинги Samlib.ru

Если включена настройка **«Fetch ratings from Samizdat»** (SOPDS → Settings,
`/web/settings/` → раздел Services; хранится как `samlib_rating` в
`config.json`, метод — `series` или `fb2`), рейтинги получает отдельная
management-команда:

```bash
cd /opt/sopds-modern/src
../.venv/bin/python manage.py fetch_samlib_ratings
```

> Это **бесконечный процесс** (не одноразовая команда и не cron-задача):
> он в цикле обходит книги без рейтинга, делает паузу 15–30 сек между
> запросами (дольше — при 429/503 от samlib.ru), а исчерпав очередь, ждёт
> сутки и проверяет снова. Если настройка `samlib_rating` выключена, команда
> сразу завершается — включите её *до* запуска.

Запускайте как отдельный systemd-сервис, а не через cron (cron создавал бы
дублирующиеся бесконечные процессы при каждом запуске):

```bash
nano /etc/systemd/system/sopds-samlib.service
```

```ini
[Unit]
Description=SOPDS samlib.ru rating fetcher
After=network.target sopds-modern.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/sopds-modern/src
EnvironmentFile=/opt/sopds-modern/src/.env
Environment=DJANGO_SETTINGS_MODULE=sopds.settings.base
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/sopds-modern/.venv/bin/python manage.py fetch_samlib_ratings
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now sopds-samlib
systemctl status sopds-samlib
```

> `PYTHONUNBUFFERED=1` — без него Python буферизует stdout при выводе не в
> терминал, и `journalctl -u sopds-samlib -f` не показывает прогресс, пока
> буфер не заполнится или процесс не завершится.

---

## 15. (Опционально) Рейтинги author.today

В отличие от Samlib, у author.today нет числовой оценки вида «X.XX из N
голосов» — есть только лайки, платные «награды» от читателей и счётчик
просмотров. Если включена настройка **«Fetch likes from author.today»**
(SOPDS → Settings, `/web/settings/` → раздел Services; хранится как
`authortoday_rating` в `config.json`), эти данные получает отдельная
management-команда:

```bash
cd /opt/sopds-modern/src
../.venv/bin/python manage.py fetch_authortoday_ratings
```

> Как и `fetch_samlib_ratings` — это **бесконечный процесс**, не разовая
> команда: обходит книги без данных, пауза 15–30 сек между запросами
> (дольше при 429/503 — у author.today лимит ~20 запросов/мин на IP),
> обновляет записи старше 14 дней. Поиск — через публичную страницу
> `https://author.today/search`, авторизация/API-токен не нужны.

```bash
nano /etc/systemd/system/sopds-authortoday.service
```

```ini
[Unit]
Description=SOPDS author.today rating fetcher
After=network.target sopds-modern.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/sopds-modern/src
EnvironmentFile=/opt/sopds-modern/src/.env
Environment=DJANGO_SETTINGS_MODULE=sopds.settings.base
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/sopds-modern/.venv/bin/python manage.py fetch_authortoday_ratings
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now sopds-authortoday
systemctl status sopds-authortoday
```

---

## 16. (Опционально) Рейтинги FantLab

Как и Samlib, fantlab.ru отдаёт числовую оценку вида «X.XX (N голосов)» —
поиск (`https://fantlab.ru/searchmain?searchstr=...`) сразу возвращает её
в результатах, без отдельного запроса на страницу произведения. Если
включена настройка **«Fetch ratings from FantLab»** (SOPDS → Settings →
раздел Services; хранится как `fantlab_rating` в `config.json`), рейтинги
получает отдельная management-команда:

```bash
cd /opt/sopds-modern/src
../.venv/bin/python manage.py fetch_fantlab_ratings
```

> Как и `fetch_samlib_ratings` — это **бесконечный процесс**: обходит
> книги без рейтинга, пауза 15–30 сек между запросами (дольше при
> 429/503), обновляет записи старше 14 дней. Если настройка выключена,
> команда сразу завершается — включите её *до* запуска.

```bash
nano /etc/systemd/system/sopds-fantlab.service
```

```ini
[Unit]
Description=SOPDS fantlab.ru rating fetcher
After=network.target sopds-modern.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/sopds-modern/src
EnvironmentFile=/opt/sopds-modern/src/.env
Environment=DJANGO_SETTINGS_MODULE=sopds.settings.base
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/sopds-modern/.venv/bin/python manage.py fetch_fantlab_ratings
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now sopds-fantlab
systemctl status sopds-fantlab
```

---

## 17. (Опционально) Рейтинги LitMarket

Как и author.today, у litmarket.ru нет числовой оценки — есть только
лайки и приблизительный счётчик просмотров (например «5k»). Если включена
настройка **«Fetch likes from LitMarket»** (SOPDS → Settings → раздел
Services; хранится как `litmarket_rating` в `config.json`), эти данные
получает отдельная management-команда:

```bash
cd /opt/sopds-modern/src
../.venv/bin/python manage.py fetch_litmarket_ratings
```

> Как и `fetch_authortoday_ratings` — это **бесконечный процесс**: обходит
> книги без данных, пауза 15–30 сек между запросами, обновляет записи
> старше 14 дней. Поиск — через публичную страницу
> `https://litmarket.ru/search`, авторизация не нужна.

```bash
nano /etc/systemd/system/sopds-litmarket.service
```

```ini
[Unit]
Description=SOPDS litmarket.ru rating fetcher
After=network.target sopds-modern.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/sopds-modern/src
EnvironmentFile=/opt/sopds-modern/src/.env
Environment=DJANGO_SETTINGS_MODULE=sopds.settings.base
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/sopds-modern/.venv/bin/python manage.py fetch_litmarket_ratings
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now sopds-litmarket
systemctl status sopds-litmarket
```

---

## 18. (Опционально) Плановое сканирование библиотеки по расписанию

Кнопка «Сканировать» в веб-интерфейсе запускает разовое сканирование по
клику — расписание (SOPDS → Settings: день/день недели/час/минута) она не
читает. Расписанием занимается отдельная management-команда:

```bash
cd /opt/sopds-modern/src
../.venv/bin/python manage.py sopds_scanner start
```

> Это **сам планировщик** (APScheduler), а не одноразовая команда и не
> cron-задача: процесс должен работать постоянно. Он ставит cron-джобу по
> `SOPDS_SCAN_SHED_DAY/DOW/HOUR/MIN` и каждые 10 минут перечитывает
> настройки — расписание можно менять в Settings без перезапуска сервиса.
> Настройка **«Start scan directly»** (`scan_start_directly`) запускает
> разовое сканирование сразу при старте процесса, в дополнение к
> расписанию.

```bash
nano /etc/systemd/system/sopds-scan.service
```

```ini
[Unit]
Description=SOPDS scheduled library scanner
After=network.target sopds-modern.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/sopds-modern/src
EnvironmentFile=/opt/sopds-modern/src/.env
Environment=DJANGO_SETTINGS_MODULE=sopds.settings.base
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/sopds-modern/.venv/bin/python manage.py sopds_scanner start
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now sopds-scan
systemctl status sopds-scan
```

> Не передавайте `--daemon` в `ExecStart` — на Linux эта опция форкает
> процесс в фон сама (двойной fork), что конфликтует с тем, как systemd
> отслеживает PID сервиса. systemd и так держит процесс в фоне и
> перезапускает его при падении (`Restart=on-failure`) — собственный
> daemonize здесь не нужен, только мешает.

---

## 19. (Опционально) Слежение за библиотекой в реальном времени

Дополняет (не заменяет) плановое сканирование из шага 18. `sopds-scan`
раз в сутки честно обходит всю библиотеку целиком — при десятках тысяч
файлов это становится всё дороже, даже если реально изменилось несколько
файлов. `sopds_watch` следит за папкой библиотеки через inotify и
пересобирает в каталоге только те папки, где реально что-то изменилось,
почти сразу после изменения — без ожидания ночного скана. Ночной
`sopds-scan` при этом оставляем как есть — это дешёвая страховка на
случай пропущенных событий (простой демона, переполнение очереди inotify
при экстремальном всплеске активности).

Задержку до пересборки изменившейся папки (по умолчанию 20 секунд —
чтобы пачка файлов, прилетевшая разом, например из синхронизации,
обработалась одним заходом, а не по одному файлу) можно менять в
SOPDS → Settings → Schedule, без перезапуска сервиса.

```bash
nano /etc/systemd/system/sopds-watch.service
```

```ini
[Unit]
Description=SOPDS-MODERN library watcher (incremental)
After=network.target sopds-modern.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/sopds-modern/src
EnvironmentFile=/opt/sopds-modern/src/.env
Environment=DJANGO_SETTINGS_MODULE=sopds.settings.base
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/sopds-modern/.venv/bin/python manage.py sopds_watch
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now sopds-watch
systemctl status sopds-watch
```

> Как и `sopds-scan`, не передавайте `--daemon` — процесс сам блокируется
> в foreground, systemd держит его в фоне.

---

## 20. Проверка

Откройте в браузере: `http://<IP-адрес сервера>:8008/` (без Apache) или `http://<IP-адрес сервера>/` (если настроили Apache на шаге 12)

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
../.venv/bin/python compile_messages.py
../.venv/bin/python manage.py migrate
../.venv/bin/python manage.py collectstatic --noinput
systemctl restart sopds-modern
```

---

## Устранение неполадок

| Проблема                                                                  | Решение                                                                                                                       |
|---------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| `502 Bad Gateway`                                                         | `systemctl status sopds-modern` — проверить ошибки gunicorn                                                                   |
| `ALLOWED_HOSTS` ошибка                                                    | Добавить IP сервера в `.env` → `ALLOWED_HOSTS`                                                                                |
| Статика не грузится                                                       | Проверить `collectstatic`, убедиться что `whitenoise` в MIDDLEWARE                                                            |
| Нет доступа к `/fb2parser/`                                               | Войти как суперпользователь (`is_superuser=True`)                                                                             |
| Ошибка прав на файлы                                                      | `chown -R www-data:www-data /opt/sopds-modern`                                                                                |
| Apache: `AH00961: failed to make connection`                              | Gunicorn не запущен — `systemctl start sopds-modern`                                                                          |
| Apache: `403 Forbidden` на статику                                        | Whitenoise обслуживает статику через gunicorn — `ProxyPass /` должен покрывать всё                                            |
| `systemd`: `status=203/EXEC`                                              | `www-data` не может выполнить Python из `.venv` — Python установлен под `/root` (см. шаг 2, `UV_PYTHON_INSTALL_DIR`)          |
| `500` при открытии страницы + `FileNotFoundError: .../log/sopds-modern.log`   | Создать папку `mkdir -p /opt/sopds-modern/src/log` (см. шаг 10) и повторить `chown -R www-data:www-data /opt/sopds-modern`    |
| Sync/Normalize/Scan/Compiler: прогресс случайно "слетает" на "не запущено" | memcached не установлен/не запущен — `systemctl status memcached` (см. шаг 1); проверить `MEMCACHED_LOCATION` в `.env`        |
| `ConnectionRefusedError`/ошибка кеша в логах gunicorn                     | `systemctl restart memcached`; убедиться что слушает `127.0.0.1:11211` — `ss -ltnp \| grep 11211`                             |
| Логин отдаёт `403`/недоступен после нескольких неверных попыток          | django-axes: блокировка на 1 час после 5 неудачных попыток. Сбросить вручную: `.venv/bin/python manage.py axes_reset`         |
