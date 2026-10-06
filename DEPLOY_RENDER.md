# Деплой API на Render

## Настройка сервиса

1. Если репозиторий содержит непосредственно backend, используйте `render.yaml` в его корне. Если загружаете общий репозиторий с папками `fundament` и `front-fundament`, задайте **Root Directory: `fundament`** в настройках Web Service.
2. Используйте Python 3.13: версия закреплена в `.python-version`. Если в Render уже задан `PYTHON_VERSION`, удалите этот override либо установите совместимую полную версию 3.13.x. Переменная имеет приоритет над файлом.
3. Заполните переменные окружения:

| Переменная | Значение |
| --- | --- |
| `DATABASE_URL` | Полная PostgreSQL-строка подключения к вашей базе, например Neon; для production требуется SSL |
| `DJANGO_SECRET_KEY` | Случайный секрет; Blueprint генерирует его автоматически |
| `DJANGO_DEBUG` | `False` |
| `DJANGO_ALLOWED_HOSTS` | `.onrender.com` и ваш собственный домен, если используется; без схемы https |
| `CORS_ALLOWED_ORIGINS` | HTTPS-адрес фронтенда без завершающего `/`; несколько адресов через запятую |
| `CSRF_TRUSTED_ORIGINS` | HTTPS-адрес фронтенда и собственный домен админки, если используется |
| `DJANGO_TIME_ZONE` | `Asia/Almaty` |
| `SECURE_SSL_REDIRECT` | `True` |
| `SECURE_HSTS_SECONDS` | `31536000`, как в Blueprint |

На Render SQLite запрещён: данные сервиса должны храниться во внешней PostgreSQL. Настройки драйвера включают тайм-аут подключения 3 секунды, отключение server-side cursors и prepared statements для совместимости с pooled подключением.

## Команды

Build Command:

```sh
python -m pip install -r requirements.txt && python manage.py check && python manage.py collectstatic --noinput
```

Start Command:

```sh
python manage.py migrate --noinput && python manage.py bootstrap_from_env && python -m gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120 --access-logfile - --error-logfile -
```

При настройке существующего сервиса вручную обновите обе команды в Render: изменение файла само по себе не гарантирует обновление вручную настроенного сервиса.

## Первоначальный владелец

`bootstrap_from_env` создаёт компанию и владельца, если задан `BOOTSTRAP_ADMIN_PASSWORD`. Остальные переменные: `BOOTSTRAP_COMPANY_NAME`, `BOOTSTRAP_COMPANY_SLUG`, `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_EMAIL`.

Если пароля нет, шаг пропускается и сервер запускается. На новой базе в этом случае администратора ещё нет: задайте bootstrap-переменные или выполните `python manage.py createsuperuser` через доступный серверный shell.

Повторный запуск не меняет пароль, email и флаги существующего пользователя, назначенные ему роли, права ролей, названия статусов или рабочие графики. Графики по умолчанию создаются только для нового сотрудника. Не используйте bootstrap для восстановления пароля существующего пользователя.

## Health check и UptimeRobot

Render Health Check Path: `/api/v1/health/`.

Для UptimeRobot укажите HTTPS-адрес backend с путём `/health/`. Доступны также `/health`, `/` и `/api/v1/health/`. Авторизация и `X-Company-ID` не нужны.

| Результат | GET | HEAD |
| --- | --- | --- |
| База отвечает на `SELECT 1` | 200, `{"status":"ok"}` | 200, пустое тело |
| База недоступна | 503, `{"status":"database_unavailable"}` | 503, пустое тело |

Оба метода возвращают `Cache-Control: no-store` и `X-Health-Status`. Health endpoints выполняют проверку и при внутреннем HTTP-запросе Render; остальные маршруты продолжают перенаправляться на HTTPS. UptimeRobot обычно начинает проверку с HEAD. Бесплатный Render может уходить в спящий режим: задержка холодного старта отличается от ошибки приложения.

## Если деплой не проходит

Смотрите первую ошибку в **Logs**, а не только итоговое `Deploy failed`.

| Ошибка | Что проверить |
| --- | --- |
| `manage.py` или `requirements.txt` не найден | Root Directory и выбранную ветку репозитория |
| Установка пакетов падает | Активную версию Python и первую ошибку pip; установленный `PYTHON_VERSION` перекрывает `.python-version` |
| `DATABASE_URL is required` / `Render requires a PostgreSQL` | Переменную базы в окружении Web Service |
| `OperationalError` при миграциях | Доступность PostgreSQL, hostname, имя базы, SSL и актуальные учётные данные |
| Старый `BOOTSTRAP_ADMIN_PASSWORD is required` | На сервисе запущен предыдущий код или выбрана другая ветка |
| `DisallowedHost` | Backend-домен в `DJANGO_ALLOWED_HOSTS` |
| Health check 503 | Сервер запускается, но база не отвечает |
| CORS/CSRF после успешного запуска | Точный домен фронтенда/админки и схему HTTPS |

Не публикуйте значения секретов, токенов, паролей или `DATABASE_URL` при передаче логов.

Источники: [Django на Render](https://render.com/docs/deploy-django), [версии Python](https://render.com/docs/python-version), [health checks](https://render.com/docs/health-checks), [бесплатный Render](https://render.com/docs/free), [HEAD-проверки UptimeRobot](https://help.uptimerobot.com/en/articles/11358466-how-to-debug-a-monitor-showing-as-down-in-uptimerobot).
