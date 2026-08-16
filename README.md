# Fundament Backend

Универсальный мультитенантный Django-бэкенд для клиентов, сотрудников, расписания, записей и задач. Роли, статусы, дополнительные поля и параметры компаний настраиваются через Django Admin без изменения кода.

## Быстрый запуск

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py bootstrap_company --company "Моя компания" --slug my-company --username owner --email owner@example.com --password "ChangeMe123!"
python manage.py runserver
```

- Админка: `http://127.0.0.1:8000/admin/`
- Swagger: `http://127.0.0.1:8000/api/docs/`
- Проверка: `http://127.0.0.1:8000/api/v1/health/`

Команда `bootstrap_company` делает владельца `is_staff`, поэтому он может войти в админку. Для полного доступа ко всей стандартной админке назначьте ему `is_superuser` через `python manage.py createsuperuser` либо настройте стандартные Django permissions. Роли продукта и права API редактируются в разделе «Роли».

## Как фронтенду работать с API

Фронтенд получает токен через `POST /api/auth/token/` с полями `username` и `password`, затем отправляет `Authorization: Token <token>`. После входа он получает список доступных компаний через `GET /api/v1/companies/`. Во все tenant-запросы он также передаёт UUID компании:

```http
X-Company-ID: 00000000-0000-0000-0000-000000000000
```

Рекомендуемый user flow:

1. Войти через `/api/auth/token/`.
2. Вызвать `GET /api/v1/me/` без компании и показать выбор доступной компании.
3. После выбора повторить `/api/v1/me/` с `X-Company-ID` — ответ содержит роли, `permissions`, готовые `capabilities` и массив `navigation` для меню.
4. Передавать токен и `X-Company-ID` во всех рабочих запросах.
5. После смены роли или компании заново получать `/me/`, чтобы интерфейс сразу перестроился.

Работник без прав `*.view_all` получает только свои записи, рабочий график, клиентов из своих записей и назначенные ему задачи. Менеджер с `appointments.view_all`, `tasks.view_all`, `schedule.view_all`, `clients.view_all` видит всю компанию. Ограничение выполняется backend, поэтому его нельзя обойти изменением frontend.

Примеры прав роли: `clients.view`, `clients.manage`, `employees.view`, `employees.manage`, `schedule.view`, `schedule.manage`, `appointments.view`, `appointments.manage`, `tasks.view`, `tasks.manage`, `roles.view`, `roles.manage`, `settings.view`, `settings.manage`, `audit.view`. Значение `*` даёт все API-права.

Основные endpoints находятся под `/api/v1/`: `clients`, `employees`, `users`, `roles`, `services`, `resources`, `statuses`, `schedules`, `time-off`, `appointments`, `tasks`, `comments`, `audit`, `dashboard`. Поиск: `?search=...`, фильтры зависят от ресурса, сортировка: `?ordering=-created_at`, пагинация: `?page=2`.

Перенос и отмена записи:

```http
POST /api/v1/appointments/{id}/cancel/     {"reason":"Клиент отказался"}
POST /api/v1/appointments/{id}/reschedule/ {"starts_at":"...","ends_at":"..."}
```

## Адаптация под заказчика

- `Company.settings` — бренд, feature flags и общие настройки.
- `Client.extra_data`, `Membership.extra_data`, `Task.extra_data`, `Appointment.extra_data` — индивидуальные поля без миграции схемы.
- `StatusDefinition` — произвольные статусы задач и записей.
- `Role.permissions` — произвольные роли и уровни доступа.
- Все рабочие сущности изолированы по компании; API проверяет членство и заголовок `X-Company-ID`.

Модули компании включаются через `GET/PATCH /api/v1/company-settings/`. Доступные ключи: `clients`, `employees`, `services`, `resources`, `schedule`, `appointments`, `tasks`, `comments`, `audit`. Отключённый модуль исчезает из `/me/` и блокируется backend. Новый предметный модуль добавляется отдельными model/serializer/viewset без изменения остальных модулей.

Обычный `DELETE` для клиентов, сотрудников, услуг и ресурсов выполняет безопасное архивирование (`is_active=false`). Они скрываются из списков, но история остаётся целой. Восстановление: `POST /api/v1/{resource}/{id}/restore/`. Физическое удаление: `DELETE /api/v1/{resource}/{id}/hard-delete/` с отдельным правом `{resource}.delete`; если объект связан с историей, API вернёт `409`. Задачи, записи, графики и другие сущности без архива удаляются физически обычным `DELETE`.

Для Neon скопируйте Connection string из панели Neon целиком в `DATABASE_URL`; отдельные `POSTGRES_USER` и `POSTGRES_PASSWORD` тогда не нужны. `DATABASE_URL` имеет приоритет над `POSTGRES_*`. Для production отключите `DJANGO_DEBUG`, смените секрет, настройте HTTPS, CORS/CSRF, резервное копирование и внешний reverse proxy. Перед выпуском выполните `python manage.py check --deploy` и `python manage.py test`.

## Деплой на Render

В корне находится готовый `render.yaml`. Загрузите проект в GitHub, в Render выберите **New → Blueprint** и подключите репозиторий. При первом создании Render попросит секретные значения:

- `DATABASE_URL` — pooled Connection string из Neon;
- `CORS_ALLOWED_ORIGINS` — адрес будущего frontend, например `https://frontend.example.com`;
- `CSRF_TRUSTED_ORIGINS` — тот же HTTPS-адрес frontend.

Render автоматически установит зависимости, соберёт static-файлы, применит миграции и запустит Gunicorn на `$PORT`. Health check: `/api/v1/health/`; он также проверяет соединение с базой. После первого запуска создайте администратора через Render Shell:

```bash
python manage.py createsuperuser
```

Для собственного домена добавьте его в `DJANGO_ALLOWED_HOSTS` через запятую, например `.onrender.com,api.example.com`. На платном Render рекомендуется вынести `python manage.py migrate` из `startCommand` в `preDeployCommand`.
