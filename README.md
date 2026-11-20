# UWW-Arena-JSON-Converter

Сервис в Docker: читает данные из UWW Arena по REST API и отдаёт плоский JSON для **vMix Data Source**.
Одна строка на ковёр: таймер, весовая категория, страны, ФИО борцов, счёт и победитель.

## Запуск

```bash
cp .env.example .env      # указать ARENA_URL, ключи OAuth, при необходимости ARENA_EVENT_ID / MATS
docker compose up -d --build
curl http://localhost:8000/status
```

В vMix: **Data Sources → Add → JSON**, URL `http://<хост>:8000/vmix` (все ковры, строка = ковёр)
или `http://<хост>:8000/vmix/mat/A` (один ковёр). Интервал обновления — 1 с.

**Все ковры в одном источнике, одной строкой:** `http://<хост>:8000/vmix/flat`.
Поля получают префикс ковра: `A_red_name`, `A_timer`, `B_blue_score`, `C_winner_name` …
Каждое поле титра привязывается к своему столбцу, строку переключать не нужно.
`/vmix/flat?key=index` даёт префиксы по порядку: `mat1_…`, `mat2_…`.
Если имя ковра — число, префикс `mat1_`, `mat2_`.

## Формат ответа `/vmix`

```json
[{
  "mat": "A", "mat_id": "…", "has_fight": true,
  "fight_number": 12, "fight_round": "", "status": 3, "period": 2,
  "timer": "1:35",
  "weight_category": "FS 65 kg", "weight": 65, "sport": "",
  "red_name": "IVANOV Ivan", "red_last_name": "", "red_first_name": "",
  "red_country": "AIN", "red_country_name": "…", "red_flag": "AIN.png", "red_score": 7, "red_cp": 0,
  "blue_name": "SMITH John", "blue_country": "USA", "blue_score": 3, "…": "…",
  "winner_side": "red", "winner_name": "IVANOV Ivan", "winner_country": "AIN",
  "victory_type": "VPO1"
}]
```

Набор полей всегда одинаковый, даже если на ковре нет схватки (`has_fight: false`, поля пустые) —
vMix не теряет привязки.

## Какие методы Arena используются

| Что | Метод |
|---|---|
| Текущий турнир | `GET /api/json/arena/current-event` (или `ARENA_EVENT_ID`) |
| Ковры турнира | `GET /api/json/mat/{sportEventId}` (запасной: `/scoreboard/mat/{sportEventId}`) |
| Открытая схватка на ковре | `GET /api/json/scoreboard/fight/open/{sportEventMatId}` (запасной: `/fight/open/{id}`) |
| Авторизация | OAuth2 `client_credentials`, `POST {ARENA_TOKEN_PATH}` → `Bearer` |

`{_format}` в путях спецификации = `json`.

## Важно: схемы ответов в спецификации нет

В `arena-api.json` у всех методов `responses: default` без описания полей. Поэтому где лежат имя,
страна, счёт и т. д., задаётся в `app/mapping.default.json`: для каждого поля — список путей,
берётся первый непустой (`{n}` = 1 красный / 2 синий). Порядок настройки на реальной Arena:

1. `GET /status` — найден ли турнир и ковры, нет ли ошибки.
2. `GET /raw/fight/<mat_id>` — сырой JSON открытой схватки (также `/raw/mats`, `/raw/api/<любой GET-путь>`).
3. Если какое-то поле в `/vmix` пустое — найти его в сыром JSON и добавить путь в свой `mapping.json`
   (достаточно указать только изменённые ключи), подключить через `MAPPING_FILE` и volume.

```json
{ "timer": ["fightClock.seconds"], "country": ["fighter{n}.athlete.team.alternateName"] }
```

**Таймер.** Если в REST-ответе схватки нет живого времени (табло может передавать его только
через Mercure — в API есть `/scoreboard/mercuretoken/{sportEvent}/{localClient}`), поле `timer`
останется пустым. Тогда понадобится подписка на Mercure — пришлите пример `/raw/fight/...` во время схватки.

## Переменные окружения

См. `.env.example`. Главное: `ARENA_URL`, `ARENA_CLIENT_ID`/`ARENA_CLIENT_SECRET` (или `ARENA_API_TOKEN`),
`ARENA_EVENT_ID`, `MATS`, `POLL_INTERVAL`, `TIMER_UNIT` (`s`/`ms`), `MAPPING_FILE`, `ENABLE_RAW`.

## Тесты

```bash
pip install -r requirements.txt pytest && pytest -q
```
