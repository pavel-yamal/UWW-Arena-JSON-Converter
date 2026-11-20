# UWW-Arena-JSON-Converter

Сервис в Docker: читает данные из UWW Arena по REST API и отдаёт плоский JSON для **vMix Data Source**.
Одна строка на ковёр: таймер, весовая категория, страны, ФИО борцов, счёт и победитель.

## Запуск

```bash
cp .env.example .env      # указать ARENA_URL, ключи OAuth, при необходимости ARENA_EVENT_ID / MATS
docker compose up -d --build
curl http://localhost:8090/status
```

Сервис сам постоянно опрашивает Arena (раз в `POLL_INTERVAL`, по умолчанию 1 с) и держит готовый документ.

В vMix: **Data Sources → Add → JSON**, URL **`http://<хост>:8090/vmix`**, интервал 1 с.
Это **один источник, одна строка**: все ковры в одном документе, поля с префиксом ковра —
`A_red_name`, `A_timer`, `B_blue_score`, `C_winner_name` … Каждое поле титра привязывается к своему столбцу.

- `VMIX_KEY=index` (или `/vmix?key=index`) — префиксы по порядку: `mat1_…`, `mat2_…`.
- Если имя ковра — число, префикс `mat1_`, `mat2_`.
- `arena_ok` — `false`, если последний опрос Arena не удался (отдаются последние полученные данные).
- `/vmix/rows` — те же данные по строке на ковёр, `/vmix/mat/A` — один ковёр.

## Формат ответа `/vmix`

```json
[{
  "updated_at": 1763637852.4, "arena_ok": true,
  "A_mat": "A", "A_has_fight": true, "A_fight_number": 12, "A_period": 2,
  "A_timer": "1:35", "A_weight_category": "FS 65 kg",
  "A_red_name": "IVANOV Ivan", "A_red_country": "AIN", "A_red_flag": "AIN.png", "A_red_score": 7,
  "A_blue_name": "SMITH John", "A_blue_country": "USA", "A_blue_score": 3,
  "A_winner_side": "red", "A_winner_name": "IVANOV Ivan", "A_victory_type": "VPO1",
  "B_mat": "B", "B_has_fight": false, "B_timer": "", "B_red_name": "", "…": "…"
}]
```

Набор полей ковра всегда одинаковый, даже если на нём нет схватки (`X_has_fight: false`, поля пустые) —
vMix не теряет привязки.

## Защита эфира

Все значения в `/vmix` — **только строки**, без `null`, `true/false`, чисел и вложенных объектов.
Сервис чистит каждое поле:

| Пришло из Arena | Уйдёт в vMix |
|---|---|
| `null`, `"null"`, `"undefined"`, `"NaN"`, `true`/`false` | `""` (и берётся следующий путь из mapping) |
| `65.0`, `7` | `"65"`, `"7"`; счёт всегда цифрами, мусор → `"0"` |
| `\n`, `\t`, двойные пробелы, неразрывный пробел | один пробел, обрезка по краям |
| `\x00`, zero-width, BOM, `�` | удаляются |
| `&amp;`, `<b>…</b>` | `&`, теги удаляются |
| `Ð˜Ð²Ð°Ð½` (битая кодировка) | `Иван` |
| `["1/8"]`, `{"name": "…"}` | `"1/8"`, значение поля |
| `winnerFighter: true` без id | победитель не показывается (не «красный») |

Флаг (`red_flag`) формируется только из настоящего кода страны из 2–3 латинских букв (`KAZ.png`).
Префиксы ковров только латиница/цифры и не повторяются (`Ковёр 1` → `mat1`).
Если запрос по одному ковру сорвался, отдаются его прошлые данные — без мигания в эфире.
Если Arena недоступна целиком — отдаётся последний снимок, `arena_ok: "0"`.
Ответ `Content-Type: application/json; charset=utf-8`.

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
