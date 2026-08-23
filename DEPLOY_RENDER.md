# Деплой API на Render

Конфигурация рассчитана на бесплатный Render Web Service без persistent disk.
Локальная файловая система Render временная, поэтому production никогда не
использует SQLite: при отсутствии `DATABASE_URL` приложение остановится с
понятной ошибкой вместо незаметной потери данных.

1. Загрузите папку `fundament` в GitHub (не добавляйте `.env` и `db.sqlite3`).
2. В Render выберите **New → Blueprint** и подключите репозиторий.
3. В Neon откройте **Connect**, включите **Connection pooling** и скопируйте
   строку целиком. В hostname pooled-строки есть суффикс `-pooler`, а в конце
   обычно находятся `sslmode=require&channel_binding=require`.
4. Заполните секретные переменные Blueprint:
   - `DATABASE_URL` — pooled PostgreSQL connection string из Neon;
   - `CORS_ALLOWED_ORIGINS` — точный HTTPS-адрес frontend без `/` в конце;
   - `CSRF_TRUSTED_ORIGINS` — тот же HTTPS-адрес frontend.
5. Не добавляйте Render Disk и не создавайте Render Postgres — постоянные
   данные хранятся в Neon.
6. Дождитесь health check `/api/v1/health/` со статусом `ok`.
7. Заполните `BOOTSTRAP_*` переменные ниже. Компания и владелец создаются
   автоматически после миграций при первом старте. Повторные старты безопасны:
   дубли не создаются, пароль существующего владельца не перезаписывается.

Blueprint автоматически устанавливает зависимости, собирает Django static,
применяет миграции при запуске и запускает один экономный Gunicorn worker с
четырьмя потоками. SQLite для production не используется: `DATABASE_URL`
обязателен.
