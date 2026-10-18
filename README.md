# DEEper — глубокие исследования с длинными отчётами

Личный исследовательский поисковик: по теме он сам планирует запросы, ищет и читает десятки источников и пишет структурированный отчёт на русском — с оглавлением, разделами, заключением и списком источников. Отчёт сохраняется в PDF, DOCX и Markdown. Работает из командной строки и через Telegram-бота.

Внутри: [GPT Researcher](https://github.com/assafelovic/gpt-researcher) (оркестрация исследования), [SearXNG](https://github.com/searxng/searxng) (бесплатный веб-поиск) или Tavily, научные базы arXiv и OpenAlex, модель DeepSeek.

> Старая версия DEEper (Groq + Tavily) сохранена в ветке [`legacy`](https://github.com/MrBoomer1981/DEEper/tree/legacy).

## Режимы

| Режим | Что делает | Пример |
|---|---|---|
| `detailed` | введение, отдельное исследование под каждую подтему, заключение | ~4 мин, ~8 500 слов, 70 источников |
| `deep` | рекурсивный поиск вширь и вглубь, затем один отчёт | дольше всех |
| `quick` | один проход | ~1 мин, ~1 900 слов |

Источники: `web` (SearXNG или Tavily), `academic` (arXiv, OpenAlex), `all`.

## Запуск на своём компьютере

Нужны Docker, [uv](https://docs.astral.sh/uv/) и ключ [DeepSeek](https://platform.deepseek.com/api_keys). На macOS для PDF: `brew install pango`.

```bash
cp .env.example .env          # впишите DEEPSEEK_API_KEY и SEARXNG_SECRET (openssl rand -hex 32)
docker compose up -d          # локальный SearXNG на 127.0.0.1:8080
uv run research.py "тема"     # отчёт появится в reports/
uv run research.py "тема" --mode deep --sources all --words 2500
uv run bot.py                 # Telegram-бот
```

## Telegram-бот

Пришлите тему — бот предложит кнопками режим и источники, покажет ход работы и пришлёт PDF, DOCX, Markdown и заключение. Ответьте на сообщение с номером отчёта (`#N`), чтобы задать вопрос по нему.

| Команда | |
|---|---|
| `/history` | последние отчёты |
| `/view N` | прислать отчёт N ещё раз |
| `/search текст` | поиск по всем отчётам |
| `/ask N вопрос` | вопрос по отчёту N |
| `/delete N` | удалить отчёт N |

Бот личный: отвечает только пользователям из `TELEGRAM_ALLOWED_USERS`. Остальным он сообщает их Telegram ID — так удобно узнать свой.

## Деплой на Railway

Сборка по `Dockerfile` (настройки в `railway.json`). SearXNG на Railway не поднимается, поэтому веб-поиск там идёт через Tavily (бесплатно 1000 запросов в месяц).

Переменные сервиса:

| Переменная | Значение |
|---|---|
| `DEEPSEEK_API_KEY` | ключ DeepSeek |
| `TELEGRAM_BOT_TOKEN` | токен от @BotFather |
| `TELEGRAM_ALLOWED_USERS` | ваш Telegram ID (несколько — через запятую) |
| `WEB_RETRIEVER` | `tavily` |
| `TAVILY_API_KEY` | ключ [Tavily](https://app.tavily.com) |

Чтобы отчёты и история переживали передеплой, подключите к сервису Volume с путём монтирования `/app/reports`.

## Настройки

Модели, длина отчётов, число подтем и параметры глубокого режима — в `config.json`. Любой ключ оттуда можно переопределить переменной окружения или строкой в `.env`.
