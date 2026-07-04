"""
Парсер расписания ДВЮИ МВД России.

Возвращает структурированные пары, чтобы бот мог искать задания по связке:
дисциплина + тип занятия + номер темы.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta

import httpx
import pytz

logger = logging.getLogger(__name__)

VLAD_TZ = pytz.timezone("Asia/Vladivostok")
API_BASE = "https://av.dvuimvd.ru/api"

SCHEDULE_API_TOKEN = os.getenv("SCHEDULE_API_TOKEN", "").strip()
GROUP_ID = os.getenv("GROUP_ID", "553").strip()
TARGET_GROUP_NAME = os.getenv("GROUP_NAME", "Ю 16 ПОНБ 2022").strip()

WEEKDAYS_RUS = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]

MONTHS_MAP = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

CLASS_TYPE_MAP = {
    "лк": "лек",
    "лек": "лек",
    "пр": "пр",
    "сем": "сем",
    "лаб": "лаб",
    "кр": "кр",
    "зач.": "зач",
    "зач": "зач",
}

CLASS_TYPE_ICONS = {
    "лек": "📖",
    "пр": "📝",
    "сем": "📚",
    "лаб": "🔬",
    "кр": "✍️",
    "зач": "✅",
}

DISCIPLINE_SHORTCUTS = {
    "Основы оперативно-розыскной деятельности органов внутренних дел": "ОРД",
    "Основы оперативно-розыскной деятельности": "ОРД",
    "Расследование преступлений против личности и собственности": "Личность",
    "Расследование преступлений, связанных с незаконным оборотом наркотических средств и психотропных веществ": "Наркотики",
    "Предупреждение преступлений и административных правонарушений органами внутренних дел": "Сырник",
    "Физическая подготовка": "ФП",
    "Тактико-специальная подготовка": "ТСП",
    "Огневая подготовка": "Огневая",
    "Криминалистика": "Крим",
}


def shorten_discipline(name: str) -> str:
    for full, short in DISCIPLINE_SHORTCUTS.items():
        if full.lower() in name.lower():
            return short
    if "право" in name.lower():
        return name
    return name[:30] if len(name) > 30 else name


def shorten_teacher(name: str) -> str:
    if not name:
        return ""

    name = name.lower().strip()
    ranks = [
        "п/п-к.",
        "п/п-к",
        "п-к.",
        "п-к",
        "к-н.",
        "к-н",
        "м-р.",
        "м-р",
        "ст. л-т",
        "ст.л-т",
        "л-т.",
        "л-т",
        "ст.",
        "ст",
        "пол.",
        "пол",
        "полиции",
        "л-т",
        "полковник",
        "подполковник",
        "майор",
        "капитан",
        "старший",
        "младший",
        "лейтенант",
        "старше-лейтенант",
        "ст.л-т",
        "сержант",
    ]
    for rank in ranks:
        name = name.replace(rank, "").strip()
    name = " ".join(name.split())
    parts = []
    for part in name.split():
        cleaned = part.strip(".,;:()")
        if not cleaned or len(cleaned) <= 1:
            continue
        if re.match(r"^[а-яё]\.[а-яё]\.?$", cleaned):
            continue
        parts.append(cleaned)
    return parts[0].capitalize() if parts else ""


def normalize_class_type(class_type_name: str) -> str:
    value = (class_type_name or "").strip().lower()
    return CLASS_TYPE_MAP.get(value, value)


def get_lesson_time_minutes(lesson_time: str) -> int:
    if not lesson_time:
        return 0
    try:
        start_time = lesson_time.split(" - ")[0]
        hours, minutes = map(int, start_time.split(":"))
        return hours * 60 + minutes
    except Exception:
        return 0


def convert_date_format(date_str: str) -> str:
    if not date_str or "." not in date_str:
        return date_str

    parts = date_str.split(".")
    if len(parts) == 3:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return date_str


def is_lesson_for_our_group(lesson: dict) -> bool:
    try:
        if lesson.get("group_id") == int(GROUP_ID):
            return True
    except ValueError:
        pass

    group_name = lesson.get("groupName", "")
    return bool(TARGET_GROUP_NAME and TARGET_GROUP_NAME in group_name)


def format_lesson(lesson: dict, lesson_num: int | None = None) -> str:
    discipline = lesson.get("discipline", "Предмет")
    class_type = lesson.get("class_type") or normalize_class_type(lesson.get("class_type_name", ""))
    teachers = lesson.get("teachers") or [
        shorten_teacher(t) for t in lesson.get("staffNames", []) if t and shorten_teacher(t)
    ]
    topic_code = lesson.get("topic_code") or lesson.get("topic_name") or ""

    icon = CLASS_TYPE_ICONS.get(class_type, "📝")
    short_discipline = shorten_discipline(discipline)
    if "фп" in short_discipline.lower():
        icon = "💪"

    prefix = f"{lesson_num}. " if lesson_num else ""
    result = f"{prefix}{icon} {short_discipline}"
    if class_type:
        result += f" ({class_type})"
    if topic_code:
        result += f" · тема {topic_code}"
    if teachers:
        result += f" 👨‍🏫 {', '.join(teachers)}"
    return result


def normalize_lesson(raw: dict, lesson_num: int) -> dict:
    class_type = normalize_class_type(raw.get("class_type_name", ""))
    teachers = [shorten_teacher(t) for t in raw.get("staffNames", []) if t and shorten_teacher(t)]
    lesson = {
        "id": raw.get("id"),
        "date": convert_date_format(raw.get("date", "")),
        "lesson_time": raw.get("lessonTime", ""),
        "discipline": raw.get("discipline", "Предмет"),
        "class_type": class_type,
        "class_type_name": raw.get("class_type_name", ""),
        "topic_code": str(raw.get("topic_code") or "").strip(),
        "topic_name": str(raw.get("topic_name") or "").strip(),
        "teachers": teachers,
        "classroom": raw.get("classroom", ""),
        "group_id": raw.get("group_id"),
        "group_name": raw.get("groupName", ""),
    }
    lesson["text"] = format_lesson(lesson, lesson_num)
    return lesson


async def scrape_schedule_api(group_id: str = GROUP_ID, months_count: int = 2) -> dict:
    if not SCHEDULE_API_TOKEN:
        logger.error("SCHEDULE_API_TOKEN не задан в переменных окружения")
        return {}

    schedule: dict[str, list[dict]] = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            now = datetime.now(VLAD_TZ)
            seen_months = set()

            for offset in range(months_count):
                target_date = now + timedelta(days=30 * offset)
                m, y = target_date.month, target_date.year
                if (m, y) in seen_months:
                    continue
                seen_months.add((m, y))

                url = f"{API_BASE}/call/schedule-schedule/student"
                params = {
                    "token": SCHEDULE_API_TOKEN,
                    "group_id": group_id,
                    "month": m,
                    "year": y,
                }

                logger.info("[API] Запрос %s/%s", m, y)
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                payload = resp.json().get("data", {})
                lessons_list = payload.get("lessons", [])

                if isinstance(lessons_list, list):
                    logger.info("[API] Получено %s пар для %s/%s", len(lessons_list), m, y)
                    for lesson in lessons_list:
                        if not isinstance(lesson, dict) or not is_lesson_for_our_group(lesson):
                            continue
                        date_key = convert_date_format(lesson.get("date", ""))
                        if not date_key:
                            continue
                        schedule.setdefault(date_key, []).append(lesson)

                await asyncio.sleep(1)

    except Exception as e:
        logger.error("[API] Ошибка: %s", e, exc_info=True)

    sorted_schedule = {}
    for date_key, lessons in schedule.items():
        sorted_lessons = sorted(lessons, key=lambda x: get_lesson_time_minutes(x.get("lessonTime", "")))
        sorted_schedule[date_key] = [
            normalize_lesson(lesson, idx + 1) for idx, lesson in enumerate(sorted_lessons)
        ]

    logger.info("[API] ИТОГО загружено дней: %s", len(sorted_schedule))
    return sorted_schedule


async def scrape_schedule(weeks_ahead: int = 3, debug_dir: str = ".") -> dict:
    return await scrape_schedule_api(GROUP_ID)


async def load_or_refresh_cache(cache_path: str, max_age_hours: int = 6) -> dict:
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)

            updated_at = cached.get("updated_at")
            if updated_at:
                age = datetime.now() - datetime.fromisoformat(updated_at)
                if age <= timedelta(hours=max_age_hours):
                    return cached.get("schedule", {})
        except Exception:
            logger.warning("Не удалось прочитать кэш расписания", exc_info=True)

    new_data = await scrape_schedule()
    if new_data:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"schedule": new_data, "updated_at": datetime.now().isoformat()},
                    f,
                    ensure_ascii=False,
                )
        except Exception:
            logger.warning("Не удалось сохранить кэш расписания", exc_info=True)
    return new_data
