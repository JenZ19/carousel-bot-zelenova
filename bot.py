import os
import re
import json
import tempfile
import logging
from dotenv import load_dotenv
from telegram import Update, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
from openai import OpenAI
from carousel_generator import CarouselGenerator

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)
generator = CarouselGenerator()

# модели: карусель — gpt-4o; кликбейт-обложка — умнее (при ошибке откат на gpt-4o)
CAROUSEL_MODEL = "gpt-4o"
COVER_MODEL = "gpt-4o"

sessions: dict = {}

THEME_NAMES = {
    "fuchsia": "💗 Фуксия",
    "hot":     "🔥 Горячий",
    "blush":   "🌸 Блаш",
    "pearl":   "🤍 Жемчуг",
    "plum":    "💜 Слива",
}

# шаги мастера
STEP_IDLE = "idle"
STEP_MODE = "await_mode"
STEP_TEXT = "await_text"
STEP_PHOTO = "await_photo"
STEP_THEME = "await_theme"
STEP_DONE = "done"


def get_session(user_id: int) -> dict:
    if user_id not in sessions:
        sessions[user_id] = {
            "step": STEP_IDLE,
            "mode": "carousel",     # carousel | reels
            "cover_manual": False,  # для reels: True — заголовок пишет пользователь
            "texts": [],
            "photos": [],   # [{path}]  — режим бот выбирает сам
            "theme": "fuchsia",
            "username": "@zelenova_marketing",
        }
    return sessions[user_id]


def reset_session(s: dict, mode: str = None):
    if mode:
        s["mode"] = mode
    s["step"] = STEP_TEXT
    s["cover_manual"] = False
    s["texts"] = []
    s["photos"] = []


MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("▶️ Старт")]],
    resize_keyboard=True,
    input_field_placeholder="Нажми Старт или /start",
)


def mode_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📲 Карусель", callback_data="mode:carousel")],
        [InlineKeyboardButton("🔥 Кликбейт-обложка для Reels", callback_data="mode:reels")],
        [InlineKeyboardButton("✍️ Подпись к посту", callback_data="mode:caption")],
    ])


def caption_type_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎠 Карусель", callback_data="captype:carousel")],
        [InlineKeyboardButton("🎬 Reels / Shorts", callback_data="captype:reels")],
        [InlineKeyboardButton("🖼 Обычный пост", callback_data="captype:feed")],
    ])


def cover_source_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Придумает бот", callback_data="cover:ai")],
        [InlineKeyboardButton("✍️ Напишу сам", callback_data="cover:manual")],
    ])


def parse_manual_headline(text: str):
    """Ручной заголовок → части обложки. Главную фразу пользователь выделяет *звёздочками*:
    'Забудьте всё о *генерации видео* — это тренд' → lead / punch / tail."""
    m = re.search(r"\*(.+?)\*", text)
    if m:
        punch = m.group(1).strip()
        lead = text[:m.start()].replace("*", "").strip(" —-,.")
        tail = text[m.end():].replace("*", "").strip(" —-,.")
    else:
        # без звёздочек — весь текст идёт крупной фразой
        punch = text.replace("*", "").strip()
        lead = tail = ""
    return {"kicker": "", "lead": lead, "punch": punch, "tail": tail}


def skip_photo_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➡️ Без фото, к выбору темы", callback_data="wiz:skip_photo")],
    ])


def photo_next_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Хватит фото, к выбору темы", callback_data="wiz:to_theme")],
    ])


def theme_keyboard(current: str, with_restart: bool = False):
    rows = []
    items = list(THEME_NAMES.items())
    for i in range(0, len(items), 3):
        row = []
        for key, label in items[i:i+3]:
            mark = "✓ " if key == current else ""
            row.append(InlineKeyboardButton(mark + label, callback_data=f"theme:{key}"))
        rows.append(row)
    if with_restart:
        rows.append([InlineKeyboardButton("🆕 Новая карусель", callback_data="wiz:restart")])
    return InlineKeyboardMarkup(rows)


STRUCTURE_PROMPT = """Ты — ВЁРСТЩИК Instagram-каруселей. Ты НЕ редактор, НЕ копирайтер, НЕ сокращатель.
Тебе дают готовый текст автора. Твоя работа — КРАСИВО РАЗЛОЖИТЬ его по слайдам, сохранив КАЖДОЕ слово.

Верни JSON-массив слайдов (ТОЛЬКО JSON, без объяснений):

[
  {{
    "slide_number": 1,
    "total_slides": N,
    "slide_style": "accent",
    "topic": "2-3 СЛОВА",
    "label": "КАТЕГОРИЯ",
    "title": "Заголовок",
    "accent_word": "слово",
    "body_lines": ["строка 1", "строка 2"],
    "visual_type": "none",
    "visual_data": {{}}
  }}
]

🚫 СТРОГО ЗАПРЕЩЕНО:
- сокращать текст, «делать короче», ужимать
- перефразировать, заменять слова автора своими
- выбрасывать предложения, детали, мысли
- придумывать текст, которого нет в оригинале

✅ ГЛАВНОЕ ПРАВИЛО:
Весь текст автора должен попасть в карусель ДОСЛОВНО. Если склеить все body_lines всех слайдов подряд — должен получиться исходный текст целиком, без потерь. Проверь это перед ответом.

КАК РАСКЛАДЫВАТЬ:
- Раздели текст на логические куски (абзац / законченная мысль) — это и есть слайды
- 4–10 слайдов; текста много → делай больше слайдов И/ИЛИ больше строк на слайде. Не теряй ничего.
- body_lines = ДОСЛОВНЫЕ предложения автора, разбитые по строкам (одно предложение = одна строка)
- title — короткий заголовок слайда (3–7 слов). Заголовки ты ПРИДУМЫВАЕШЬ сам — это НЕ нарушает правило сохранения текста, ведь весь текст автора всё равно целиком идёт в body_lines. Заголовок только подписывает кусок.
- Последний слайд — финальная мысль/вывод автора (+ можно cta_pill)

🎣 ЗАГОЛОВОК 1-ГО СЛАЙДА — ЭТО ХУК (самое важное в карусели!):
Первый заголовок решает, остановится человек или пролистает. Сделай его ВИРУСНЫМ, цепляющим и ТОЧНО ПО ТЕМЕ поста (он должен отражать суть именно этого текста, а не быть общим).
Правила хука:
- 3–8 слов, бьёт прямо в тему и в интерес/боль аудитории
- создаёт интригу ИЛИ обещает конкретную пользу/результат — чтобы невозможно было не открыть
- ЗАПРЕЩЕНЫ общие пустые фразы («Моя история», «Полезный пост», «Несколько советов», «Делюсь опытом») — это провал
- приёмы: число, прямой вопрос, провокация, обещание трансформации, недосказанность, обращение к читателю (ты/вы)
- accent_word 1-го слайда — самое сильное слово хука
- body_lines 1-го слайда: 0–1 короткая строка-подводка (основной текст автора уходит на следующие слайды, ничего не теряем)
Рабочие формулы (адаптируй под тему, не копируй буквально):
• «Как [результат] за [срок] / без [усилия]» → Как собрать карусель за вечер без дизайнера
• «[Число] [ошибок/причин/способов], из-за которых…» → 5 ошибок, из-за которых карусели не читают
• «Никто не говорит, что…» / «Вот что на самом деле…» → интрига
• «Если ты тоже [боль]…» → Если ты тоже залипаешь в Canva на час
• «Забудь про X — делай Y» → провокация
• «Почему [привычное] не работает» → вызов

🎨 РАЗНООБРАЗИЕ ОФОРМЛЕНИЯ (но НИКОГДА ценой текста):
- Кусок выглядит как список из слов автора → bullet_list / numbered_list, используя ЭТИ ЖЕ слова автора как пункты
- В тексте есть «было/стало», противопоставление → comparison или two_col словами автора
- В тексте есть шаги/этапы → timeline словами автора
- В тексте есть конкретная цифра-результат → можешь добавить big_stat или progress_bar (текст вокруг всё равно в body_lines)
- Яркая мысль/вывод автора → quote (дословно)
- Если красивый визуал не получается без выкидывания слов → ставь visual_type "none" и просто аккуратно выложи весь текст в body_lines
Меняй тип слайдов, чтобы было интересно смотреть — но проверка одна: ни одно слово автора не потеряно.

⚠️ НЕ ДУБЛИРУЙ ТЕКСТ: если текст слайда лежит внутри визуала (quote.text, bullet_list.items, numbered_list, checklist, timeline, table и т.п.), НЕ копируй эти же слова ещё и в body_lines. На таком слайде body_lines = [] (пусто) ИЛИ короткая подводка ДРУГИМИ словами. Одна и та же фраза не должна появляться на слайде дважды.

Остальное:
- accent_word: одно слово из заголовка для акцента (или null)
- topic: МАКСИМУМ 2-3 слова (например "МОЙ ОПЫТ", "ЛАЙФХАК")
- label: 1-2 слова (например "РЕЗУЛЬТАТ")

{photo_instructions}

visual_type варианты (НЕ используй photo_text — фото добавляются автоматически как фон):
• "none" — только текст
• "table" — visual_data: {{"rows": [{{"label": "...", "value": "...", "accent": false}}]}}
• "cards" — visual_data: {{"cards": [{{"number": "01", "title": "...", "text": "..."}}]}}
• "comparison" — visual_data: {{"left": {{"title": "...", "items": ["..."], "result": "×5"}}, "right": {{"title": "...", "items": ["..."], "result": "×1"}}}}
• "checklist" — visual_data: {{"title": "...", "items": [{{"text": "...", "done": true, "tag": "OK", "tag_color": "#6A8C6A"}}]}}
• "numbered_list" — visual_data: {{"items": [{{"number": 1, "text": "..."}}]}}
• "bullet_list" — visual_data: {{"items": ["...", "..."]}}
• "progress_bar" — visual_data: {{"label_before": "было", "value_before": "40 мин", "label_after": "стало", "value_after": "10 мин"}}
• "big_stat" — visual_data: {{"stats": [{{"number": "40%", "label": "сэкономила времени"}}]}}
• "number" — ОДНА огромная цифра-герой во весь слайд. visual_data: {{"big_number": "17", "caption": "лет — и первый миллион"}}. Используй для яркой цифры/возраста/результата.
• "quote" — visual_data: {{"text": "цитата", "author": null}}
• "timeline" — visual_data: {{"steps": [{{"number": 1, "title": "Шаг", "text": "пояснение"}}]}}
• "two_col" — visual_data: {{"left_title": "БЫЛО", "left_items": ["..."], "right_title": "СТАЛО", "right_items": ["..."]}}
• "pull_quote" — редакционная цитата Playfair Italic с большим декоративным знаком. visual_data: {{"text": "цитата дословно", "author": "автор или null"}}. Отличается от quote: крупная, по центру, без вертикальной черты слева. Идеально для ключевой мысли или отзыва.
• "stat_grid" — 2×2 бенто-сетка больших цифр (Playfair Display). visual_data: {{"stats": [{{"number": "87%", "label": "клиентов возвращаются", "trend": "+12%"}}]}}. До 4 статистик. trend опционален (+ зелёный, - dim).
• "highlight_card" — крупная акцентная карточка-удар на весь слайд. visual_data: {{"heading": "Заголовок Playfair Italic", "subtext": "пояснение"}}. Для ключевой мысли, вывода, финального послания. slide_style="editorial" → карточка будет ярко-розовой; slide_style="accent" → карточка светлая.
• "steps_flow" — горизонтальные шаги со стрелками (схема "как это работает"). visual_data: {{"steps": [{{"number": "1", "title": "Шаг", "text": "пояснение"}}]}}. 3–4 шага. Для процессов, алгоритмов, роадмапов.
• "magazine_split" — асимметричный разворот: 1/3 слева = большое слово/фраза Playfair, 2/3 справа = список. visual_data: {{"big_label": "5 шагов", "items": [{{"title": "Заголовок пункта", "text": "описание"}}]}}. Журнальный стиль. Максимум 4 пункта.

🎨 СТИЛЬ СЛАЙДА (slide_style) — ОБЯЗАТЕЛЬНОЕ поле:
• "accent" — яркий горячий розовый фон, белый текст. ОБЯЗАТЕЛЕН для: слайда 1 (всегда!), цитат (quote), больших цифр (number/big_stat), последнего слайда. Не более 30% слайдов.
• "editorial" — нежный блаш фон, тёмный текст. Для большинства контентных слайдов с body_lines.
• "minimal" — белый фон, огромный заголовок, почти без текста. Для ключевых мыслей, пауз между блоками (1-2 слайда в карусели).

Правила:
- Слайд 1 → ВСЕГДА "accent"
- Последний слайд → ВСЕГДА "accent"
- quote, pull_quote, number, big_stat → "accent"
- highlight_card, stat_grid → "editorial" (карточка сама создаёт контраст)
- steps_flow, magazine_split → "editorial" или "minimal"
- Остальное: чередуй "editorial" и изредка "minimal", не ставь "editorial" 5+ раз подряд

Дополнительно (опционально):
• "right_label": "ПОДТЕМА" — текст справа в шапке (1-2 слова)
• "cta_pill": "текст кнопки →" — кнопка внизу

Текст пользователя:
{text}"""


COVER_PROMPT = """Ты — мастер ВИРУСНЫХ кликбейтных обложек для Reels/Shorts в стиле @zelenova_marketing.
По теме/тексту придумай ОДНУ обложку, которая мгновенно останавливает листание и заставляет нажать.

Обложка строится из ТРЁХ частей с РАЗНЫМ размером шрифта (это важно — не делай всё одинаковым):
- lead — короткая подводка ПЕРЕД главной фразой, мелким шрифтом (например «Забудьте всё, что вы знали о»). Можно "".
- punch — ГЛАВНАЯ ключевая фраза, 1–3 слова, САМЫЙ крупный текст, выделяется цветом. Смысловой центр обложки.
- tail — короткая добивка/обещание ПОСЛЕ главной фразы, средним шрифтом (например «теперь это в тренде»). Можно "".

Верни ТОЛЬКО JSON (без объяснений):
{{"kicker": "...", "lead": "...", "punch": "...", "tail": "..."}}

Правила:
- Вместе lead + punch + tail должны читаться как одна цельная цепляющая фраза.
- punch — короткий и мощный (1–3 слова), это якорь обложки. НЕ запихивай в punch длинное предложение.
- Кликбейт-приёмы: «ты делаешь это неправильно», «никто не говорит», числа/деньги, «секрет», «никогда», «вот почему», незакрытый цикл, прямое обращение.
- kicker — верхняя плашка 1–3 слова ("СМОТРИ ДО КОНЦА", "ШОК", "ВАЖНО") или "".
- По-русски, живо, дерзко. ЗАПРЕЩЕНЫ скучные формулировки («полезная информация», «моя история»).

Тема/текст рилса:
{text}"""


CAPTION_PROMPT = """Ты — копирайтер для Instagram в стиле @zelenova_marketing. Пишешь подписи, которые работают на алгоритм: останавливают прокрутку, провоцируют сохранить/прокомментировать, привлекают целевую аудиторию в нише маркетинга.

Тип поста: {post_type}
Тема/текст: {text}

Создай подпись по структуре:

1. ХУК (первые 1-2 строки — до "читать ещё") — самое важное. Должен останавливать. Используй: боль, интригу, провокацию, обещание результата, вопрос в лоб.
2. ТЕЛО — раскрой ценность, 3-5 ключевых тезисов (можно списком или коротко по-человечески). Не лей воду.
3. CTA — конкретный призыв: задай вопрос читателям, попроси сохранить/поделиться, напиши что в посте/карусели.
4. ХЭШТЕГИ — 5-8 штук, релевантных теме. Смешай высоко- и среднечастотные. Отдельной строкой.

Правила:
- Живой разговорный язык, как будто пишет реальный человек
- Абзацы с пробелом между ними — для читаемости
- Никакого канцелярита, официоза, перечислений ради перечислений
- Эмодзи уместно, но не больше 2-3 и только по смыслу
- Длина: 700-1200 символов (тело), хэштеги отдельно

Верни готовый текст подписи (без объяснений, без «вот подпись»)."""


THREADS_PROMPT = """Ты пишешь пост для Threads от имени @zelenova_marketing — эксперта по маркетингу.

Тема: {text}

Threads — это как Twitter, но на русском. Правила жанра:
- 1-5 коротких абзацев, каждый = одна мысль
- Первая строка = крючок. Должна цеплять без контекста — её видят в ленте.
- Обрыв на полуслове, парадокс, провокация, цифра, личный опыт — всё, что заставляет дочитать
- Разговорный стиль, прямо и честно
- Заканчивай вопросом ИЛИ неожиданным выводом — провоцируй ответы
- Никаких хэштегов (в Threads они не работают)
- Длина: 150-400 символов (короткие посты работают лучше)

Верни только текст поста."""


# ── шаг 1: /start — выбор режима ────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_session(update.effective_user.id)
    s["step"] = STEP_MODE
    await update.message.reply_text(
        "Привет! Что делаем? 💫",
        reply_markup=MAIN_KEYBOARD,
    )
    await update.message.reply_text(
        "Выбери формат:",
        reply_markup=mode_keyboard(),
    )


# ── приём текста ────────────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_session(update.effective_user.id)

    if update.message.text == "▶️ Старт":
        await cmd_start(update, context)
        return

    if s["step"] in (STEP_IDLE, STEP_DONE, STEP_MODE):
        reset_session(s)

    s["texts"].append(update.message.text)

    if s["step"] == STEP_TEXT:
        mode = s.get("mode", "carousel")

        if mode == "caption":
            s["step"] = STEP_DONE

            async def reply_caption(content, reply_markup=None, as_media_group=False, as_photo=False):
                return await update.message.reply_text(content, reply_markup=reply_markup)

            async def edit_caption(m, text):
                await m.edit_text(text)

            await _run_caption(update.effective_user.id, reply_caption, edit_caption)

        elif mode == "reels":
            s["step"] = STEP_PHOTO
            first = "Заголовок принят ✓" if s.get("cover_manual") else "Тема получена ✓"
            await update.message.reply_text(
                f"{first}\n\n"
                "Шаг 2 из 3 — пришли фото для фона обложки 📷 (необязательно)\n"
                "Или сразу к выбору темы.",
                reply_markup=skip_photo_keyboard(),
            )
        else:
            s["step"] = STEP_PHOTO
            await update.message.reply_text(
                "Текст получила ✓\n\n"
                "Шаг 2 из 3 — пришли фото 📷\n"
                "Я сама красиво вставлю их в слайды. Или пропусти этот шаг.",
                reply_markup=skip_photo_keyboard(),
            )
    else:
        # уже на шаге фото — просто добавляем текст
        await update.message.reply_text("Добавила ещё текст ✓")


# ── приём фото ──────────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_session(update.effective_user.id)

    if s["step"] in (STEP_IDLE, STEP_DONE, STEP_MODE):
        reset_session(s)
        s["step"] = STEP_PHOTO

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        await file.download_to_drive(f.name)
        path = f.name

    if update.message.caption:
        s["texts"].append(update.message.caption)

    s["photos"].append({"path": path})
    n = len(s["photos"])

    await update.message.reply_text(
        f"Фото {n} получено 📷\n"
        "Пришли ещё фото или переходи к выбору темы.",
        reply_markup=photo_next_keyboard(),
    )


# ── шаг 3: тема + генерация ─────────────────────────────────────────────────

async def _run_make(uid: int, reply_func, edit_func):
    s = get_session(uid)

    if not s["texts"]:
        await reply_func("Сначала пришли текст. Нажми /start", reply_markup=None)
        return

    msg = await reply_func("Структурирую контент…")

    photo_count = len(s["photos"])
    if photo_count:
        photo_instructions = (
            f"К посту приложено {photo_count} фото. ПЕРВОЕ фото станет ФОНОМ ОБЛОЖКИ (1-го слайда) — "
            "текст пойдёт поверх фото, поэтому заголовок 1-го слайда сделай контрастным и коротким, "
            "а body_lines обложки — максимум 1 короткая строка. "
        )
        if photo_count > 1:
            photo_instructions += (
                f"Остальные {photo_count - 1} фото размести ПО СМЫСЛУ: на слайдах со 2-го, где картинка "
                f"уместна по содержанию, добавь поле \"wants_photo\": true (не больше {photo_count - 1} таких слайдов). "
                "Туда бот аккуратно вставит фото отдельным блоком; текст на этих слайдах держи компактнее. "
            )
        photo_instructions += "НЕ используй visual_type photo_text."
    else:
        photo_instructions = "Фото нет."

    text = "\n\n".join(s["texts"])

    try:
        response = client.chat.completions.create(
            model=CAROUSEL_MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": STRUCTURE_PROMPT.format(
                text=text,
                photo_instructions=photo_instructions,
            )}]
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        slides = json.loads(raw)
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        await edit_func(msg, f"Ошибка: {e}")
        return

    await edit_func(msg, f"Генерирую {len(slides)} слайдов…")

    # 1-е фото → фон обложки; остальные → на слайды по смыслу (wants_photo), иначе по порядку
    photos = [p["path"] for p in s["photos"]]
    cover_photo = photos[0] if photos else None
    rest = photos[1:]

    marked = [sl for sl in slides if sl.get("slide_number", 1) != 1 and sl.get("wants_photo")]
    if not marked:
        marked = [sl for sl in slides if sl.get("slide_number", 1) != 1]
    photo_by_num = dict(zip([sl.get("slide_number") for sl in marked], rest))

    slide_files = []
    try:
        for slide in slides:
            sn = slide.get("slide_number", 1)
            photo_path = None
            photo_mode = None
            if sn == 1 and cover_photo:
                photo_path, photo_mode = cover_photo, "cover_bg"
            elif sn in photo_by_num:
                photo_path, photo_mode = photo_by_num[sn], "block"

            path = generator.generate_slide(
                slide,
                theme=s["theme"],
                username=s["username"],
                photo_path=photo_path,
                photo_mode=photo_mode,
            )
            slide_files.append(path)

        await edit_func(msg, f"Готово! Отправляю {len(slide_files)} слайдов…")

        for i in range(0, len(slide_files), 10):
            batch = slide_files[i:i+10]
            handles, media = [], []
            for path in batch:
                fh = open(path, "rb")
                handles.append(fh)
                media.append(InputMediaPhoto(fh))
            await reply_func(media, as_media_group=True)
            for fh in handles:
                fh.close()

        await msg.delete()

        s["step"] = STEP_DONE
        await reply_func(
            "Готово! ✨\n"
            "Хочешь другую тему — выбери ниже, пересоберу.\n"
            "Или начни новую карусель.",
            reply_markup=theme_keyboard(s["theme"], with_restart=True),
        )

    except Exception as e:
        logger.error(f"Generation error: {e}")
        await edit_func(msg, f"Ошибка генерации: {e}")
    finally:
        for path in slide_files:
            try: os.unlink(path)
            except: pass


# ── режим Reels: кликбейт-обложка ───────────────────────────────────────────

async def _run_cover(uid: int, reply_func, edit_func):
    s = get_session(uid)

    if not s["texts"]:
        await reply_func("Сначала пришли заголовок/тему. Нажми /start", reply_markup=None)
        return

    text = " ".join(s["texts"]).strip()

    cover = None
    if s.get("cover_manual"):
        # заголовок написал пользователь
        cover = parse_manual_headline(text)
        msg = await reply_func("Рисую обложку…")
    else:
        msg = await reply_func("Придумываю кликбейт… 🔥")
        used_model = None
        for model in (COVER_MODEL, CAROUSEL_MODEL):
            try:
                response = client.chat.completions.create(
                    model=model,
                    max_tokens=400,
                    messages=[{"role": "user", "content": COVER_PROMPT.format(text=text)}],
                )
                raw = response.choices[0].message.content.strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                cover = json.loads(raw)
                used_model = model
                break
            except Exception as e:
                logger.error(f"Cover model {model} error: {e}")
                continue

        if not cover:
            await edit_func(msg, "Не получилось придумать заголовок, попробуй ещё раз")
            return
        logger.info(f"Cover headline by {used_model}: {cover.get('headline')}")
        await edit_func(msg, "Рисую обложку…")

    photo_path = s["photos"][0]["path"] if s["photos"] else None

    try:
        path = generator.generate_cover(
            cover, theme=s["theme"], username=s["username"], photo_path=photo_path,
        )
        with open(path, "rb") as fh:
            await reply_func(fh, as_photo=True)
        try: os.unlink(path)
        except: pass

        await msg.delete()
        s["step"] = STEP_DONE
        await reply_func(
            "Готово! 🔥\n"
            "Другая тема — выбери ниже, перерисую.\n"
            "Или начни заново.",
            reply_markup=theme_keyboard(s["theme"], with_restart=True),
        )
    except Exception as e:
        logger.error(f"Cover generation error: {e}")
        await edit_func(msg, f"Ошибка генерации обложки: {e}")


# ── режим Caption: подпись к посту ──────────────────────────────────────────

async def _run_caption(uid: int, reply_func, edit_func):
    s = get_session(uid)

    if not s["texts"]:
        await reply_func("Сначала пришли текст. Нажми /start", reply_markup=None)
        return

    text = "\n\n".join(s["texts"]).strip()
    post_type = s.get("caption_type", "пост")

    msg = await reply_func("Пишу подпись…")

    try:
        response = client.chat.completions.create(
            model=CAROUSEL_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": CAPTION_PROMPT.format(
                text=text,
                post_type=post_type,
            )}]
        )
        caption = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Caption error: {e}")
        await edit_func(msg, f"Ошибка: {e}")
        return

    await msg.delete()
    s["step"] = STEP_DONE
    await reply_func(
        caption,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🆕 Новая задача", callback_data="wiz:restart")
        ]])
    )


# ── режим Threads: короткий пост ────────────────────────────────────────────

async def _run_threads(uid: int, reply_func, edit_func):
    s = get_session(uid)

    if not s["texts"]:
        await reply_func("Сначала пришли тему. Нажми /start", reply_markup=None)
        return

    text = "\n\n".join(s["texts"]).strip()
    msg = await reply_func("Пишу пост для Threads…")

    try:
        response = client.chat.completions.create(
            model=CAROUSEL_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": THREADS_PROMPT.format(text=text)}]
        )
        post = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Threads error: {e}")
        await edit_func(msg, f"Ошибка: {e}")
        return

    await msg.delete()
    s["step"] = STEP_DONE
    await reply_func(
        post,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🆕 Новая задача", callback_data="wiz:restart"),
        ]])
    )


# ── callbacks ───────────────────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    s = get_session(uid)
    data = query.data

    if data.startswith("mode:"):
        mode = data.split(":")[1]
        reset_session(s, mode=mode)
        if mode == "reels":
            await query.edit_message_text(
                "🔥 Кликбейт-обложка для Reels\n\n"
                "Кто придумает заголовок?",
                reply_markup=cover_source_keyboard(),
            )
        elif mode == "caption":
            s["step"] = STEP_TEXT
            await query.edit_message_text(
                "✍️ Подпись к посту\n\n"
                "Для какого формата нужна подпись?",
                reply_markup=caption_type_keyboard(),
            )
        else:
            await query.edit_message_text(
                "📲 Карусель\n\n"
                "Шаг 1 из 3 — пришли текст 📝\n"
                "Можно несколько сообщений подряд."
            )
        return

    if data.startswith("captype:"):
        captype = data.split(":")[1]
        type_names = {"carousel": "карусель", "reels": "Reels", "feed": "пост"}
        s["caption_type"] = type_names.get(captype, "пост")
        s["step"] = STEP_TEXT
        await query.edit_message_text(
            f"✍️ Подпись для формата: {s['caption_type']}\n\n"
            "Пришли текст поста или его тему 📝\n"
            "Чем больше деталей — тем точнее подпись."
        )
        return

    if data.startswith("cover:"):
        src = data.split(":")[1]
        s["mode"] = "reels"
        s["cover_manual"] = (src == "manual")
        s["step"] = STEP_TEXT
        s["texts"] = []
        if src == "manual":
            await query.edit_message_text(
                "✍️ Свой заголовок\n\n"
                "Отправь текст обложки. Главную фразу оберни в *звёздочки* — "
                "она станет КРУПНОЙ и цветной, а остальное — мелким шрифтом сверху/снизу:\n\n"
                "например:  Забудьте всё о *генерации видео* теперь это тренд"
            )
        else:
            await query.edit_message_text(
                "✨ Ок, придумаю сам!\n\n"
                "Шаг 1 — пришли тему или текст рилса 📝"
            )
        return

    if data == "wiz:skip_photo" or data == "wiz:to_theme":
        s["step"] = STEP_THEME
        tail = "соберу обложку" if s.get("mode") == "reels" else "соберу карусель"
        await query.edit_message_text(
            "Шаг 3 из 3 — выбери тему 🎨\n"
            f"После выбора сразу {tail}.",
            reply_markup=theme_keyboard(s["theme"]),
        )
        return

    if data == "wiz:restart":
        # чистим старые фото
        for p in s["photos"]:
            try: os.unlink(p["path"])
            except: pass
        s["step"] = STEP_MODE
        s["texts"] = []
        s["photos"] = []
        await query.edit_message_text(
            "Начинаем заново 🆕\n\nЧто делаем?",
            reply_markup=mode_keyboard(),
        )
        return

    if data.startswith("theme:"):
        theme = data.split(":")[1]
        s["theme"] = theme

        async def reply(content, reply_markup=None, as_media_group=False, as_photo=False):
            if as_media_group:
                return await query.message.reply_media_group(content)
            if as_photo:
                return await query.message.reply_photo(content, reply_markup=reply_markup)
            return await query.message.reply_text(content, reply_markup=reply_markup)

        async def edit(m, text):
            await m.edit_text(text)

        await query.edit_message_text(f"Тема: {THEME_NAMES[theme]} ✓")
        if s.get("mode") == "reels":
            await _run_cover(uid, reply, edit)
        else:
            await _run_make(uid, reply, edit)
        return

    if data == "wiz:caption_go":
        async def reply(content, reply_markup=None, as_media_group=False, as_photo=False):
            if as_media_group:
                return await query.message.reply_media_group(content)
            if as_photo:
                return await query.message.reply_photo(content, reply_markup=reply_markup)
            return await query.message.reply_text(content, reply_markup=reply_markup)

        async def edit(m, text):
            await m.edit_text(text)

        await _run_caption(uid, reply, edit)
        return


# ── служебные команды ───────────────────────────────────────────────────────

async def cmd_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_session(update.effective_user.id)
    if context.args:
        name = context.args[0]
        if not name.startswith("@"):
            name = "@" + name
        s["username"] = name
        await update.message.reply_text(f"Никнейм: {name}")
    else:
        await update.message.reply_text("Пример: /username @zelenova_marketing")


def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("Нет TELEGRAM_TOKEN в .env")
    if not OPENAI_API_KEY:
        raise ValueError("Нет OPENAI_API_KEY в .env")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("username", cmd_username))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
