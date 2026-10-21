# VideoTG — Telegram-бот для AI-монтажа видео

Бот, который превращает **текстовое описание** в **готовый MP4-ролик**: нейросеть
пишет анимационную HTML-композицию, а HyperFrames рендерит её в видео.

```
🧑 Вы: «Сделай рилс про 3 способа выучить английский за 20 минут в день»
🤖 Модель (GPT / Claude / Gemini): пишет композицию → запускает рендер
🎬 Бот: присылает готовое видео
```

---

## Что внутри

| Часть | Реализация |
|-------|-----------|
| 🧩 Капча | 4 буквы-цифры в шуме (Pillow): повороты, волна, линии, пятна, солирование, JPEG-деградация. Ответ хранится **только как HMAC-хеш** и никогда не уходит в ИИ |
| 📜 Согласие | Версионируемый текст о передаче данных Google Gemini / Anthropic Claude / OpenAI GPT / ElevenLabs / HyperFrames / MCP-серверам. Фиксируется версия, хеш текста, дата, IP. Есть отзыв |
| 🏠 Главное меню | Баннер-картинка (генерируется Pillow) + описание проекта + кнопки: Монтировать, Подключить ИИ, Коннекторы, Скиллы, Проекты, Настройки, О проекте |
| 🎬 Рендер | HyperFrames CLI (`npx hyperframes`): lint → check → render в MP4, прогресс парсится из `@hf-progress` |
| 🧠 Модели | OpenAI (chat completions), Anthropic (messages+tools), Gemini (function calling) — единый интерфейс и общий агентный цикл |
| 🔌 Коннекторы | HyperFrames, ElevenLabs (TTS/STT), FFmpeg (нарезка/склейка/звук/кадры), Рабочая папка, **MCP** (http / sse / stdio) |
| 🧩 Скиллы | Shorts/Reels, Объяснялка 16:9, Озвучка+субтитры, Монтаж материалов, Промо |
| 🔐 Ключи | BYOK, шифрование Fernet, приоритет: ключ пользователя → серверный ключ |

---

## Требования

| Компонент | Версия | Зачем |
|-----------|--------|-------|
| Python | 3.11+ | бот |
| Node.js | 22+ | HyperFrames CLI |
| FFmpeg | **5.0+** | кодирование (нужен фильтр `setparams`) |
| Chrome/Chromium | любой | захват кадров HyperFrames (ставится автоматически) |

> ⚠️ Частая проблема: в PATH лежит старый FFmpeg (< 5), и рендер падает с
> `No such filter: 'setparams'`. Бот сам ищет современный FFmpeg (в том числе в
> каталогах winget), но надёжнее указать путь явно: `FFMPEG_BINARY=...`.

---

## Быстрый старт

```bash
git clone https://github.com/IL-PY/VideomontageAI.git
cd VideomontageAI
python -m venv .venv
# Windows: .venv\Scripts\activate   |  Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

copy .env.example .env      # Windows
# cp .env.example .env       # Linux/macOS
```

Заполните в `.env`:

```env
BOT_TOKEN=123456789:AA...      # токен от @BotFather
ENCRYPTION_KEY=<случайная строка>
```

Сгенерировать ключ шифрования:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Запуск:

```bash
python -m app
```

### Railway

Сборка без Dockerfile (Railpack, см. `railpack.json`: Python, Node 22, ffmpeg, библиотеки Chrome).
Подключите Railway Volume с mount path `/app/data` — там лежат SQLite, проекты, рендеры и кэш npm/Chrome.

---

## Как пользоваться

1. `/start` → **капча**: введите 4 символа с картинки (бот и нейросеть её прочитать не могут).
2. → **согласие**: прочитайте, кому уходят данные, нажмите «✅ Принимаю».
3. → **главное меню** с баннером.
4. «🎬 Монтировать» → скилл (формат) → модель → описание ролика.
5. Можно прикрепить видео/фото/аудио — файлы попадут в `assets/` проекта.
6. Бот отдаёт готовое видео; история — в «📂 Проекты».

Команды: `/start`, `/menu`, `/cancel`, `/consent` (пересмотреть согласие), `/captcha` (пересдать капчу).

---

## Архитектура

```
app/
├── bot.py                  # сборка Dispatcher, polling/webhook, обработчик ошибок
├── container.py            # контейнер сервисов (бот, рендерер, коннекторы, скиллы)
├── config.py               # pydantic-settings, все параметры из .env
├── middlewares.py          # загрузка пользователя + контроль доступа (капча/согласие)
├── db/                     # SQLAlchemy 2.1 async: users, consent, keys, connectors, projects, jobs
├── security/               # Fernet-шифрование, генератор капчи
├── ai/
│   ├── types.py            # ToolSpec / ToolCall / LLMResponse / ChatTurn
│   ├── agent.py            # цикл «модель → инструмент → модель»
│   └── providers/          # openai / anthropic / gemini — общий интерфейс
├── connectors/             # hyperframes, elevenlabs, ffmpeg, workspace, mcp
├── skills/                 # скиллы: системный промпт + набор коннекторов + пресеты
├── services/               # rendering (HyperFrames CLI), jobs (очередь), accounts, consent, banner
├── ui/                     # theme (цветные кнопки), keyboards, texts
└── handlers/               # start, main_menu, ai_menu, connectors_menu, montage, projects
```

### Как «текст → медиа» вообще работает

1. **Текст** пользователя уходит в LLM вместе с системным промптом скилла, где лежит
   *контракт композиции HyperFrames* (`data-start`, `data-duration`, `class="clip"`,
   `window.__timelines["main"]`, правила линтера).
2. LLM пишет `index.html` (анимация, типографика, `<video>`/`<audio>` из `assets/`).
3. Коннектор HyperFrames прогоняет `lint` → модель видит замечания и чинит композицию →
   `check` (headless-браузер) → `render`.
4. Готовый MP4 уходит пользователю в чат.

### Скиллы

| id | Что делает | Кадр |
|----|-----------|------|
| `shorts` | вертикальный ролик с хуком, 3–5 блоков и CTA | 1080×1920 |
| `explainer` | объяснялка со сценами | 1920×1080 |
| `voiceover` | озвучка ElevenLabs + субтитры по таймкодам | 1920×1080 |
| `montage` | нарезка и склейка загруженных материалов | 1920×1080 |
| `promo` | короткий рекламный ролик | 1920×1080 |

### Коннекторы и MCP

Любой MCP-сервер подключается в разделе «🔌 Коннекторы» и его инструменты
становятся обычными функциями для модели:

* **http** (streamable HTTP) — например `https://api.elevenlabs.io/v1/mcp`;
* **sse** — старый SSE-транспорт;
* **stdio** — локальный процесс: `npx -y @elevenlabs/mcp-server`.

В `env` stdio-сервера поддерживаются подстановки `{{elevenlabs_key}}`,
`{{openai_key}}`, `{{anthropic_key}}`, `{{gemini_key}}` — ключи подставляются
из хранилища и в базу не попадают.

---

## Настройки (.env)

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `BOT_TOKEN` | — | токен @BotFather (обязательно) |
| `ENCRYPTION_KEY` | — | секрет для шифрования ключей |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/videotg.db` | БД |
| `DATA_DIR` | `./data` | каталог данных и проектов |
| `RENDER_MODE` | `local` | `local` / `docker` / `cloud` |
| `HYPERFRAMES_CLI` | `npx` | как запускать CLI |
| `HYPERFRAMES_VERSION` | `0.8.181` | закреплённая версия CLI |
| `FFMPEG_BINARY` | — | путь к FFmpeg ≥ 5 |
| `MAX_CONCURRENT_RENDERS` | `2` | параллельные рендеры |
| `RENDER_TIMEOUT` | `1800` | таймаут рендера, сек |
| `CONSENT_VERSION` | `2026-10-20` | версия согласия (смена → новое согласие) |
| `CAPTCHA_TTL_SECONDS` | `300` | время жизни капчи |
| `CAPTCHA_MAX_ATTEMPTS` | `5` | попыток ввода |
| `AGENT_MAX_STEPS` | `12` | лимит шагов агента |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `ELEVENLABS_API_KEY` | — | серверные ключи (BYOK не обязателен) |
| `LONG_POLLING` | `true` | polling или webhook |

---

## Тесты

```bash
pytest -q                     # 30 unit/smoke-тестов, без сети
RUN_RENDER_TESTS=1 pytest -q  # + сквозной тест реального рендера (нужен Node/ffmpeg)
```

Покрыто: генерация и проверка капчи, согласие и его версия, санитайзер HTML,
защита от выхода за пределы каталога проекта, схемы инструментов, конвертация
сообщений между OpenAI/Anthropic/Gemini, агентный цикл, сквозной рендер в MP4,
а также smoke-тест онбординга (капча → согласие → главное меню) с мок-сессией Telegram.

---

## Ограничения

* Telegram не умеет красить фон кнопок — «цвета» реализованы эмодзи-индикаторами
  (🟢 ок / 🔵 инфо / 🟡 внимание / 🔴 ошибка / ⛔ опасное действие).
* Лимит Telegram на файл — ~50 МБ; крупные ролики отправляются как документ.
* Рендер требует локального Chrome и FFmpeg ≥ 5 (или `RENDER_MODE=docker` / `cloud`).
* Качество генерации композиции целиком зависит от выбранной модели.

## Лицензия

MIT
