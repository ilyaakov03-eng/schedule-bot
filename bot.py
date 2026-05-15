"""
bot.py — Телеграм-бот расписания ДВЮИ МВД России
Группа: Ю 16 ПОНБ 2022 (4 курс, юриспруденция)
"""

import asyncio
import logging
import random
import re
from datetime import datetime, timedelta, date

import pytz
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters
from aiohttp import web

from scraper import load_or_refresh_cache, scrape_schedule, MONTHS_MAP, WEEKDAYS_RUS

# ──────────────────────────────────────────────
TELEGRAM_TOKEN = "7864155748:AAH5L7SdgnRiLSwfMzRBvOtO7n6Ja41u6AA"
CACHE_PATH = "schedule_cache.json"
VLAD_TZ = pytz.timezone("Asia/Vladivostok")
CACHE_REFRESH_HOURS = 6
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BANNED_USER = "minomet28"

ROAST_REPLIES = [
    "🚫 Расписание для тебя: 1 пара — иди нахуй, 2 пара — продолжай идти нахуй.",
    "📅 Твоё расписание: весь день — быть клоуном. Ты справляешься на отлично.",
    "🤡 О, это опять ты. Расписание для дебилов не завезли, извини.",
    "📖 Пары для тебя отменены. Причина: ты безнадёжен.",
    "❌ Бот временно не обслуживает людей с IQ ниже комнатной температуры.",
    "🗑️ Твоё расписание находится там же, где и твоё будущее — в мусорке.",
    "🧠 Ошибка: мозг пользователя не обнаружен. Расписание показать невозможно.",
    "🧹 Расписание для отбросов общества не предусмотрено. Иди подметай двор.",
    "🐒 К сожалению, расписание доступно только для людей.",
    "🐷 Даже свиньи в навозе найдут больше пользы, чем ты на парах.",
    "🧹 Иди лучше полы драить в дежурке, чем мне мозг выносить.",
    "🎪 Цирк уехал, а ты остался. Расписание для клоунов отменено.",
    "🧟 Судя по активности мозга, тебе пора не на пары, а на кладбище.",
    "⚰️ Даже расписание твоих похорон интереснее, чем твоя учёба.",
    "🤖 Системная ошибка: пользователь слишком тупой для обработки запроса.",
    "💉 Тебе не в универ, а на лечение. Даже я устал от твоего уровня.",
    "🧻 Ты настолько бесполезен, что даже туалетная бумага имеет больше ценности.",
    "🖌️ Маляр ждёт тебя в наряде, иди крась, может хоть там от тебя будет польза.",
    "🧦 Ты даже носки нормально не умеешь складывать, какое нахуй расписание.",
    "🍽️ В столовой готовят лучше, чем ты учишься. А это уже диагноз.",
    "🪖 Ты — тот, из-за которого весь курс на субботник ставят.",
    "💀 Даже в морге лежать приятнее, чем с тобой на одной паре сидеть.",
]

SUBJECT_ALIASES = {
    "упп": "Уголовно-процессуальное право",
    "гпп": "Гражданское процессуальное право",
    "уп": "Уголовное право",
    "гп": "Гражданское право",
    "фп": "Физическая подготовка",
    "физо": "Физическая подготовка",
    "физ": "Физическая подготовка",
    "кримке": "Криминалистика",
    "кримка": "Криминалистика",
    "крим": "Криминалистика",
    "огневой": "Огневая подготовка",
    "огневая": "Огневая подготовка",
    "огнев": "Огневая подготовка",
    "тсп": "Тактико-специальная подготовка",
    "тактико": "Тактико-специальная подготовка",
    "малолеткам": "Несовершеннолетних",
    "несоверш": "Несовершеннолетних",
    "орд": "Основы оперативно-розыскной деятельности",
    "оперативно": "Основы оперативно-розыскной деятельности",
    "конституц": "Конституционное право",
    "кп": "Конституционное право",
    "ап": "Административное право",
    "адм": "Административное право",
    "фин": "Финансовое право",
    "нал": "Налоговое право",
}

_schedule_cache: dict = {}


def get_schedule() -> dict:
    return _schedule_cache


def is_direct_command(text: str) -> bool:
    """Проверяет, это ли прямая команда боту (начинается с пар/расписание)"""
    return text.startswith(("пары", "расписание", "когда", "следующая", "следующий", "обновить"))


async def health_check(request):
    """Endpoint для проверки здоровья сервиса"""
    return web.Response(text="OK", status=200)


async def refresh_schedule_task(context: ContextTypes.DEFAULT_TYPE):
    global _schedule_cache
    logger.info("Фоновое обновление расписания...")
    try:
        data = await scrape_schedule()
        if data:
            _schedule_cache = data
            import json
            from datetime import datetime as dt
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"schedule": data, "updated_at": dt.now().isoformat()}, f, ensure_ascii=False)
            logger.info(f"Расписание обновлено: {len(data)} дней")
        else:
            logger.warning("Обновление не дало результата")
    except Exception as e:
        logger.error(f"Ошибка фонового обновления: {e}", exc_info=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user = update.message.from_user
    username = (user.username or "").lower().strip()
    text = update.message.text.lower().strip()
    now = datetime.now(VLAD_TZ)

    if not is_direct_command(text):
        return

    asks_schedule = any(w in text for w in ["пары", "расписание", "когда", "следующая", "следующий"])
    if username == BANNED_USER:
        if asks_schedule:
            await update.message.reply_text(random.choice(ROAST_REPLIES), parse_mode="HTML")
        return

    sched = get_schedule()

    if any(w in text for w in ["обновить расписание", "обнови расписание", "refresh", "/refresh"]):
        await update.message.reply_text("🔄 Обновляю расписание с сайта, подожди немного...")
        global _schedule_cache
        data = await scrape_schedule()
        if data:
            _schedule_cache = data
            import json
            from datetime import datetime as dt
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"schedule": data, "updated_at": dt.now().isoformat()}, f, ensure_ascii=False)
            await update.message.reply_text(f"✅ Готово! Загружено {len(data)} дней расписания.")
        else:
            await update.message.reply_text("❌ Не удалось получить расписание с сайта.")
        return

    if "когда" in text or "следующая" in text or "следующий" in text:
        target = None
        for alias, full in SUBJECT_ALIASES.items():
            if alias in text or full.lower() in text:
                target = (alias, full)
                break

        if target:
            alias, full = target
            for i in range(1, 45):
                d = (now + timedelta(days=i)).date()
                day_lessons = sched.get(d.strftime("%Y-%m-%d"), [])
                for lesson in day_lessons:
                    if full.lower() in lesson.lower() or alias in lesson.lower():
                        day_name = WEEKDAYS_RUS[d.weekday()]
                        await update.message.reply_text(
                            f"🔎 <b>Ближайшая пара — {full}:</b>\n"
                            f"📅 {d.strftime('%d.%m')} ({day_name})\n{lesson}",
                            parse_mode="HTML",
                        )
                        return
            await update.message.reply_text(f"❌ «{full}» не нашёл в расписании на ближайшие 6 недель.")
            return

        if not any(w in text for w in ["пары", "расписание"]):
            for i in range(0, 14):
                d = (now + timedelta(days=i)).date()
                day_lessons = sched.get(d.strftime("%Y-%m-%d"), [])
                if day_lessons:
                    day_name = WEEKDAYS_RUS[d.weekday()]
                    msg = f"📅 <b>Ближайший учебный день — {d.strftime('%d.%m')} ({day_name}):</b>\n\n"
                    msg += "\n".join(day_lessons)
                    await update.message.reply_text(msg, parse_mode="HTML")
                    return
            await update.message.reply_text("📭 В ближайшие 2 недели пар не нашёл.")
            return

    if any(w in text for w in ["пары", "расписание"]):
        target_date = None

        if "сегодня" in text:
            target_date = now.date()
        elif "завтра" in text:
            target_date = (now + timedelta(days=1)).date()
        elif "послезавтра" in text:
            target_date = (now + timedelta(days=2)).date()
        elif any(day in text for day in ["понедельник", "пн"]):
            days_ahead = 0 - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            target_date = now.date() + timedelta(days=days_ahead)
        elif any(day in text for day in ["вторник", "вт"]):
            days_ahead = 1 - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            target_date = now.date() + timedelta(days=days_ahead)
        elif any(day in text for day in ["среда", "ср"]):
            days_ahead = 2 - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            target_date = now.date() + timedelta(days=days_ahead)
        elif any(day in text for day in ["четверг", "чт"]):
            days_ahead = 3 - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            target_date = now.date() + timedelta(days=days_ahead)
        elif any(day in text for day in ["пятница", "пт"]):
            days_ahead = 4 - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            target_date = now.date() + timedelta(days=days_ahead)
        elif "неделю" in text:
            monday = now.date() - timedelta(days=now.weekday())
            await _send_week(update, sched, monday, "Текущая неделя")
            return
        else:
            dm = re.search(r"(\d{1,2})\s+([а-яё]+)", text)
            if dm:
                day = int(dm.group(1))
                month = MONTHS_MAP.get(dm.group(2))
                if month:
                    target_date = date(now.year, month, day)
            else:
                dm2 = re.search(r"(\d{1,2})\.(\d{1,2})", text)
                if dm2:
                    target_date = date(now.year, int(dm2.group(2)), int(dm2.group(1)))

        if target_date:
            await _send_day(update, sched, target_date)
        else:
            await _send_day(update, sched, now.date())
        return


async def _send_day(update: Update, sched: dict, target_date: date):
    day_name = WEEKDAYS_RUS[target_date.weekday()]
    date_fmt = target_date.strftime("%d.%m")
    lessons = sched.get(target_date.strftime("%Y-%m-%d"), [])

    msg = f"📅 <b>Расписание на {date_fmt} ({day_name}):</b>\n\n"
    msg += "\n".join(lessons) if lessons else "Пар нет — отдыхаем! ✨"

    if not sched:
        msg += "\n\n⚠️ <i>Расписание не загружено. Напиши «обновить расписание»</i>"

    await update.message.reply_text(msg, parse_mode="HTML")


async def _send_week(update: Update, sched: dict, monday: date, title: str):
    lines = [f"📆 <b>{title}:</b>\n"]
    has_any = False
    for i in range(6):
        d = monday + timedelta(days=i)
        lessons = sched.get(d.strftime("%Y-%m-%d"), [])
        day_name = WEEKDAYS_RUS[d.weekday()]
        lines.append(f"<b>— {d.strftime('%d.%m')} {day_name}:</b>")
        if lessons:
            has_any = True
            lines.extend(lessons)
        else:
            lines.append("  Пар нет")
        lines.append("")

    if not has_any:
        lines.append("⚠️ Расписание на эту неделю не загружено.")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот расписания группы <b>Ю 16 ПОНБ 2022</b>.\n\n"
        "Что умею:\n"
        "• <code>пары сегодня</code> / <code>пары завтра</code>\n"
        "• <code>пары 15 мая</code> / <code>пары 15.05</code>\n"
        "• <code>расписание на неделю</code>\n"
        "• <code>когда фп</code> / <code>следующая тсп</code>\n"
        "• <code>обновить расписание</code>\n\n"
        f"Расписание загружено: <b>{len(get_schedule())} дней</b>",
        parse_mode="HTML",
    )


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_message(update, context)


async def post_init(application):
    global _schedule_cache

    logger.info("Загружаю расписание при старте...")
    data = await load_or_refresh_cache(CACHE_PATH, max_age_hours=CACHE_REFRESH_HOURS)
    if data:
        _schedule_cache = data
        logger.info(f"Расписание загружено: {len(data)} дней")
    else:
        logger.warning("Расписание пустое — запускаю скрапинг")
        data = await scrape_schedule()
        _schedule_cache = data or {}

    job_queue = application.job_queue
    job_queue.run_repeating(
        refresh_schedule_task,
        interval=CACHE_REFRESH_HOURS * 3600,
        first=CACHE_REFRESH_HOURS * 3600,
        name="auto_refresh",
    )
    logger.info(f"Авто-обновление каждые {CACHE_REFRESH_HOURS} часов настроено.")


if __name__ == "__main__":
    import os
    import threading
    
    # Запускаем веб-сервер в отдельном потоке
    def run_web_server():
        async def web_app():
            port = int(os.getenv("PORT", 8080))
            app_web = web.Application()
            app_web.router.add_get('/health', health_check)
            runner = web.AppRunner(app_web)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', port)
            await site.start()
            logger.info(f"Health check сервер запущен на порту {port}")
            # Держим сервер запущенным
            await asyncio.Event().wait()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(web_app())
    
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    
    # Запускаем Telegram бота в основном потоке
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Бот запущен!")
    app.run_polling(drop_pending_updates=True, timeout=60)
