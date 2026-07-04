"""
Телеграм-бот расписания ДВЮИ МВД России.

Умеет показывать расписание и находить задания из планов семинарских
и практических занятий по дисциплине, дате и номеру темы из расписания.
"""

import asyncio
import json
import logging
import os
import re
import threading
from datetime import date, datetime, timedelta

import pytz
from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from materials import (
    find_lessons,
    find_material_for_lesson,
    find_subject_id,
    format_exam_questions_message,
    format_material_message,
    get_exam_questions,
    load_materials,
    parse_requested_date,
    split_telegram_message,
    subject_discipline,
)
from scraper import MONTHS_MAP, WEEKDAYS_RUS, VLAD_TZ, format_lesson, load_or_refresh_cache, scrape_schedule


TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("BOT_TOKEN")
    or os.getenv("API_TOKEN")
    or ""
).strip()
CACHE_PATH = "schedule_cache.json"
CACHE_REFRESH_HOURS = int(os.getenv("CACHE_REFRESH_HOURS", "6"))
STUDY_END_DATE = os.getenv("STUDY_END_DATE", "").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_schedule_cache: dict = {}
_materials_cache: dict = {}


def get_schedule() -> dict:
    return _schedule_cache


def is_direct_command(text: str) -> bool:
    text = text.lower().strip()
    return text.startswith(
        (
            "пары",
            "расписание",
            "когда",
            "следующая",
            "следующий",
            "обновить",
            "обнови",
            "задания",
            "задание",
            "дз",
            "вопросы",
            "сколько осталось",
        )
    )


def is_exam_command(text: str) -> bool:
    text = text.lower().strip().replace("ё", "е")
    return ("вопрос" in text and "зачет" in text) or text.startswith("/exam")


def is_materials_command(text: str) -> bool:
    text = text.lower().strip()
    return text.startswith(("задания", "задание", "дз", "вопросы")) or text.startswith("/tasks")


def is_structured_schedule(schedule: dict) -> bool:
    for lessons in schedule.values():
        if lessons:
            return isinstance(lessons[0], dict)
    return True


def lesson_text(lesson, lesson_num: int | None = None) -> str:
    if isinstance(lesson, dict):
        return lesson.get("text") or format_lesson(lesson, lesson_num)
    return str(lesson)


def save_schedule_cache(schedule: dict) -> None:
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"schedule": schedule, "updated_at": datetime.now().isoformat()},
            f,
            ensure_ascii=False,
        )


async def health_check(request):
    return web.Response(text="OK", status=200)


async def refresh_schedule_task(context: ContextTypes.DEFAULT_TYPE):
    global _schedule_cache
    logger.info("Фоновое обновление расписания...")
    try:
        data = await scrape_schedule()
        if data:
            _schedule_cache = data
            save_schedule_cache(data)
            logger.info("Расписание обновлено: %s дней", len(data))
        else:
            logger.warning("Обновление не дало результата")
    except Exception as e:
        logger.error("Ошибка фонового обновления: %s", e, exc_info=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.lower().strip()
    if not is_direct_command(text):
        return

    if is_exam_command(text):
        await handle_exam_request(update, text)
        return

    if is_materials_command(text):
        await handle_materials_request(update, text)
        return

    if "сколько осталось" in text:
        await send_days_left(update)
        return

    if any(w in text for w in ["обновить расписание", "обнови расписание", "refresh", "/refresh"]):
        await update.message.reply_text("🔄 Обновляю расписание с сайта, подожди немного...")
        await refresh_now(update)
        return

    sched = get_schedule()
    now = datetime.now(VLAD_TZ)

    if "когда" in text or "следующая" in text or "следующий" in text:
        await send_next_lesson(update, text, sched, now)
        return

    if any(w in text for w in ["пары", "расписание"]):
        target_date = parse_schedule_date(text, now)
        wants_week = is_week_request(text)
        if target_date is None and wants_week and "следующ" in text:
            monday = now.date() - timedelta(days=now.weekday()) + timedelta(days=7)
            await send_week(update, sched, monday, "Следующая неделя")
            return
        if target_date is None and wants_week:
            monday = now.date() - timedelta(days=now.weekday())
            await send_week(update, sched, monday, "Текущая неделя")
            return
        await send_day(update, sched, target_date or now.date())


async def handle_materials_request(update: Update, text: str):
    sched = get_schedule()
    materials = _materials_cache or load_materials()
    now = datetime.now(VLAD_TZ)

    subject_id = find_subject_id(text, materials)
    if not subject_id:
        await update.message.reply_text(
            "Укажи дисциплину: например\n"
            "• <code>задания налоговое 26.06</code>\n"
            "• <code>задания наркотики завтра</code>\n"
            "• <code>задания личность пятница</code>\n"
            "• <code>задания предпринимательское</code>",
            parse_mode="HTML",
        )
        return

    target_date = parse_requested_date(text, now)
    lessons = find_lessons(sched, subject_id, materials, target_date)
    discipline = subject_discipline(subject_id, materials)

    if not lessons:
        if target_date:
            await update.message.reply_text(
                f"Не нашёл пару по дисциплине «{discipline}» на {target_date.strftime('%d.%m')}."
            )
        else:
            await update.message.reply_text(
                f"Не нашёл ближайшую пару по дисциплине «{discipline}» в загруженном расписании."
            )
        return

    sent_any = False
    missing = []
    seen = set()

    for lesson in lessons:
        if not isinstance(lesson, dict):
            continue

        lesson_date = date.fromisoformat(lesson["date"])
        material = find_material_for_lesson(lesson, materials)
        dedupe_key = (
            lesson.get("discipline"),
            lesson.get("class_type"),
            lesson.get("topic_code"),
            lesson_date.isoformat(),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        if not material:
            missing.append(lesson)
            continue

        msg = format_material_message(lesson, material, lesson_date)
        for chunk in split_telegram_message(msg):
            await update.message.reply_text(chunk, parse_mode="HTML")
        sent_any = True

    if not sent_any:
        lines = [f"Пара найдена, но заданий в базе пока нет: <b>{discipline}</b>"]
        for lesson in missing:
            lines.append(
                f"• {lesson.get('date')} · {lesson.get('lesson_time')} · "
                f"{lesson.get('class_type')} · тема {lesson.get('topic_code') or 'не указана'}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_exam_request(update: Update, text: str):
    materials = _materials_cache or load_materials()

    subject_id = find_subject_id(text, materials)
    if not subject_id:
        await update.message.reply_text(
            "Укажи дисциплину: например\n"
            "• <code>вопросы на зачёт предпринимательское</code>\n"
            "• <code>вопросы на зачёт наркотики</code>\n"
            "• <code>вопросы на зачёт личность</code>",
            parse_mode="HTML",
        )
        return

    discipline = subject_discipline(subject_id, materials)
    questions = get_exam_questions(subject_id, materials)

    if not questions:
        await update.message.reply_text(
            f"Вопросов на зачёт по дисциплине «{discipline}» пока нет в базе."
        )
        return

    msg = format_exam_questions_message(discipline, questions)
    for chunk in split_telegram_message(msg):
        await update.message.reply_text(chunk, parse_mode="HTML")


async def refresh_now(update: Update):
    global _schedule_cache
    data = await scrape_schedule()
    if data:
        _schedule_cache = data
        save_schedule_cache(data)
        await update.message.reply_text(f"✅ Готово! Загружено {len(data)} дней расписания.")
    else:
        await update.message.reply_text("❌ Не удалось получить расписание с сайта.")


def parse_schedule_date(text: str, now: datetime) -> date | None:
    if is_week_request(text):
        return None

    parsed = parse_requested_date(text, now)
    if parsed:
        return parsed

    match = re.search(r"(\d{1,2})\s+([а-яё]+)", text)
    if match:
        day = int(match.group(1))
        month = MONTHS_MAP.get(match.group(2))
        if month:
            return date(now.year, month, day)

    return None


def is_week_request(text: str) -> bool:
    normalized = text.lower().replace("ё", "е")
    return bool(re.search(r"\b(на\s+)?неделю\b|\bнеделя\b|\bследующая\s+неделя\b", normalized))


async def send_next_lesson(update: Update, text: str, sched: dict, now: datetime):
    materials = _materials_cache or load_materials()
    subject_id = find_subject_id(text, materials)

    if subject_id:
        lessons = find_lessons(sched, subject_id, materials, None)
        if lessons:
            lesson = lessons[0]
            d = date.fromisoformat(lesson["date"])
            day_name = WEEKDAYS_RUS[d.weekday()]
            await update.message.reply_text(
                f"🔎 <b>Ближайшая пара — {lesson.get('discipline')}:</b>\n"
                f"📅 {d.strftime('%d.%m')} ({day_name}) · {lesson.get('lesson_time')}\n"
                f"{lesson_text(lesson)}",
                parse_mode="HTML",
            )
            return

        await update.message.reply_text("Не нашёл эту дисциплину в ближайшем расписании.")
        return

    for i in range(0, 14):
        d = (now + timedelta(days=i)).date()
        day_lessons = sched.get(d.strftime("%Y-%m-%d"), [])
        if day_lessons:
            day_name = WEEKDAYS_RUS[d.weekday()]
            msg = f"📅 <b>Ближайший учебный день — {d.strftime('%d.%m')} ({day_name}):</b>\n\n"
            msg += "\n".join(lesson_text(lesson, idx + 1) for idx, lesson in enumerate(day_lessons))
            await update.message.reply_text(msg, parse_mode="HTML")
            return

    await update.message.reply_text("📭 В ближайшие 2 недели пар не нашёл.")


async def send_day(update: Update, sched: dict, target_date: date):
    day_name = WEEKDAYS_RUS[target_date.weekday()]
    date_fmt = target_date.strftime("%d.%m")
    lessons = sched.get(target_date.strftime("%Y-%m-%d"), [])

    msg = f"📅 <b>Расписание на {date_fmt} ({day_name}):</b>\n\n"
    msg += "\n".join(lesson_text(lesson, idx + 1) for idx, lesson in enumerate(lessons)) if lessons else "Пар нет — отдыхаем! ✨"

    if not sched:
        msg += "\n\n⚠️ <i>Расписание не загружено. Напиши «обновить расписание»</i>"

    await update.message.reply_text(msg, parse_mode="HTML")


async def send_week(update: Update, sched: dict, monday: date, title: str):
    lines = [f"📆 <b>{title}:</b>\n"]
    has_any = False
    for i in range(6):
        d = monday + timedelta(days=i)
        lessons = sched.get(d.strftime("%Y-%m-%d"), [])
        day_name = WEEKDAYS_RUS[d.weekday()]
        lines.append(f"<b>— {d.strftime('%d.%m')} {day_name}:</b>")
        if lessons:
            has_any = True
            lines.extend(lesson_text(lesson, idx + 1) for idx, lesson in enumerate(lessons))
        else:
            lines.append("  Пар нет")
        lines.append("")

    if not has_any:
        lines.append("⚠️ Расписание на эту неделю не загружено.")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def send_days_left(update: Update):
    today = datetime.now(VLAD_TZ).date()
    end_date = None
    source = ""

    if STUDY_END_DATE:
        try:
            end_date = date.fromisoformat(STUDY_END_DATE)
            source = "STUDY_END_DATE"
        except ValueError:
            await update.message.reply_text("Дата окончания учёбы задана неверно в STUDY_END_DATE. Нужен формат YYYY-MM-DD.")
            return

    if end_date is None:
        schedule_dates = []
        for key in get_schedule().keys():
            try:
                schedule_dates.append(date.fromisoformat(key))
            except ValueError:
                pass
        future_dates = [d for d in schedule_dates if d >= today]
        if future_dates:
            end_date = max(future_dates)
            source = "последняя дата в загруженном расписании"

    if end_date is None:
        await update.message.reply_text(
            "Я не знаю дату окончания учёбы. Добавь в Render переменную "
            "<code>STUDY_END_DATE</code> в формате <code>YYYY-MM-DD</code>.",
            parse_mode="HTML",
        )
        return

    if end_date < today:
        await update.message.reply_text(
            f"Дата окончания учёбы сейчас стоит <b>{end_date.strftime('%d.%m.%Y')}</b>, "
            "она уже прошла. Обнови переменную <code>STUDY_END_DATE</code> в Render.",
            parse_mode="HTML",
        )
        return

    days_left = (end_date - today).days
    await update.message.reply_text(
        f"До конца учёбы осталось: <b>{days_left}</b> дней.\n"
        f"Ориентир: {end_date.strftime('%d.%m.%Y')}\n"
        f"<i>Источник: {source}</i>",
        parse_mode="HTML",
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот расписания группы <b>Ю 16 ПОНБ 2022</b>.\n\n"
        "Что умею:\n"
        "• <code>пары сегодня</code> / <code>пары завтра</code>\n"
        "• <code>расписание на неделю</code> / <code>расписание следующая неделя</code>\n"
        "• <code>когда налоговое</code>\n"
        "• <code>задания налоговое 26.06</code>\n"
        "• <code>задания наркотики завтра</code>\n"
        "• <code>вопросы на зачёт предпринимательское</code>\n"
        "• <code>сколько осталось учиться</code>\n\n"
        f"Расписание загружено: <b>{len(get_schedule())} дней</b>",
        parse_mode="HTML",
    )


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Обновляю расписание с сайта...")
    await refresh_now(update)


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "задания " + " ".join(context.args)
    await handle_materials_request(update, text)


async def cmd_exam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "вопросы на зачёт " + " ".join(context.args)
    await handle_exam_request(update, text)


async def post_init(application):
    global _schedule_cache, _materials_cache

    _materials_cache = load_materials()
    logger.info("Материалы загружены: %s записей", len(_materials_cache.get("items", [])))

    logger.info("Загружаю расписание при старте...")
    data = await load_or_refresh_cache(CACHE_PATH, max_age_hours=CACHE_REFRESH_HOURS)
    if data and not is_structured_schedule(data):
        logger.info("Кэш старого формата, обновляю расписание")
        data = await scrape_schedule()

    _schedule_cache = data or {}
    logger.info("Расписание загружено: %s дней", len(_schedule_cache))

    application.job_queue.run_repeating(
        refresh_schedule_task,
        interval=CACHE_REFRESH_HOURS * 3600,
        first=CACHE_REFRESH_HOURS * 3600,
        name="auto_refresh",
    )


def run_web_server():
    async def web_app():
        port = int(os.getenv("PORT", 8080))
        app_web = web.Application()
        app_web.router.add_get("/health", health_check)
        runner = web.AppRunner(app_web)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info("Health check сервер запущен на порту %s", port)
        await asyncio.Event().wait()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(web_app())


if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Задай TELEGRAM_TOKEN в переменных окружения Render")

    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .connect_timeout(60.0)
        .read_timeout(120.0)
        .write_timeout(60.0)
        .pool_timeout(60.0)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("exam", cmd_exam))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Бот запущен!")
    app.run_polling(drop_pending_updates=True, timeout=60)
