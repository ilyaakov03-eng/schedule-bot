"""
scraper.py — финальная версия парсинга
"""

import asyncio
import json
import logging
import httpx
from datetime import date, datetime, timedelta
import pytz

logger = logging.getLogger(__name__)

VLAD_TZ = pytz.timezone("Asia/Vladivostok")
API_BASE = "https://av.dvuimvd.ru/api"

# ============ HARDCODED TOKEN И GROUP_ID ============
API_TOKEN = "c68d8b66-34eb-4525-b71b-0d1b62d777f0"
HARDCODED_GROUP_ID = "553"
TARGET_GROUP_NAME = "Ю 16 ПОНБ 2022"
# ====================================================

WEEKDAYS_RUS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS_MAP = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

CLASS_TYPE_MAP = {
    "лк": "лек",
    "пр": "пр",
    "сем": "сем",
    "лаб": "лаб",
    "кр": "кр",
}

CLASS_TYPE_ICONS = {
    "лек": "📖",
    "пр": "📝",
    "сем": "📚",
    "лаб": "🔬",
    "кр": "✍️",
}

# Сокращения для дисциплин
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
    """Сокращает название дисциплины"""
    for full, short in DISCIPLINE_SHORTCUTS.items():
        if full.lower() in name.lower():
            return short
    # Если нет сокращения и содержит "право" — оставляем оригинальное
    if "право" in name.lower():
        return name
    # Иначе берём первые 30 символов
    return name[:30] if len(name) > 30 else name

def shorten_teacher(name: str) -> str:
    """Сокращает ФИО преподавателя, оставляет только фамилию"""
    if not name:
        return ""
    
    name = name.strip()
    
    # Список всех ранговых сокращений и должностей
    ranks = [
        "п/п-к.", "п-к.", "ст. л-т", "п/п-к", "п-к", "ст.", "л-т",
        "пол.", "полковник", "майор", "капитан", "старший", "младший",
        "лейтенант", "старше-лейтенант", "ст.л-т", "сержант"
    ]
    
    # Убираем каждый ранг
    for rank in ranks:
        name = name.replace(rank, "").strip()
    
    # Удаляем лишние пробелы
    name = " ".join(name.split())
    
    # Берём только фамилию (первое слово)
    parts = name.split()
    if parts and parts[0]:
        return parts[0]
    
    return name

def get_lesson_time_minutes(lesson_time: str) -> int:
    """Преобразует время '09:00 - 10:30' в минуты с начала дня для сортировки"""
    if not lesson_time:
        return 0
    try:
        start_time = lesson_time.split(" - ")[0]
        hours, minutes = map(int, start_time.split(":"))
        return hours * 60 + minutes
    except:
        return 0

def format_lesson(les: dict, lesson_num: int) -> str:
    """Форматирует пару в минималистичный формат"""
    discipline = les.get("discipline", "Предмет")
    class_type_name = les.get("class_type_name", "")
    staff_names = les.get("staffNames", [])
    
    # Сокращаем дисциплину
    short_discipline = shorten_discipline(discipline)
    
    # Сокращаем тип занятия
    short_class_type = CLASS_TYPE_MAP.get(class_type_name, class_type_name) if class_type_name else ""
    
    # Берём иконку для типа занятия
    icon = CLASS_TYPE_ICONS.get(short_class_type, "📝")
    
    # Если ФП — используем силу мышц
    if "фп" in short_discipline.lower():
        icon = "💪"
    
    # Берём всех преподавателей и сокращаем
    teachers = [shorten_teacher(t) for t in staff_names if t and shorten_teacher(t)]
    short_teacher = ", ".join(teachers) if teachers else ""
    
    # Формируем минималистичный вывод
    res = f"{lesson_num}. {icon} {short_discipline}"
    
    if short_class_type:
        res += f" ({short_class_type})"
    if short_teacher:
        res += f" 👨‍🏫 {short_teacher}"
    
    return res

def convert_date_format(date_str: str) -> str:
    """Преобразует ДД.МММ.ГГГГ в ГГГГ-ММ-ДД"""
    if not date_str or "." not in date_str:
        return date_str
    
    parts = date_str.split(".")
    if len(parts) == 3:
        try:
            return f"{parts[2]}-{parts[1]}-{parts[0]}"
        except:
            return date_str
    return date_str

def is_lesson_for_our_group(lesson: dict) -> bool:
    """Проверяет, относится ли пара к нашей группе"""
    # Если group_id совпадает — это точно наша пара
    if lesson.get("group_id") == int(HARDCODED_GROUP_ID):
        return True
    
    # Если это поток (flow), проверяем по названию группы
    group_name = lesson.get("groupName", "")
    if TARGET_GROUP_NAME in group_name:
        return True
    
    return False

async def scrape_schedule_api(group_id: str, months_count: int = 2) -> dict:
    schedule = {}
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            now = datetime.now(VLAD_TZ)
            
            for offset in range(months_count):
                target_date = now + timedelta(days=30 * offset)
                m, y = target_date.month, target_date.year
                
                url = f"{API_BASE}/call/schedule-schedule/student"
                params = {"token": API_TOKEN, "group_id": group_id, "month": m, "year": y}
                
                logger.info(f"[API] Запрос {m}/{y}")
                resp = await client.get(url, params=params, headers=headers)
                data = resp.json()

                payload = data.get("data", {})
                
                # Берём lessons — это список всех пар
                lessons_list = payload.get("lessons", [])
                
                if isinstance(lessons_list, list):
                    logger.info(f"[API] Получено {len(lessons_list)} пар для {m}/{y}")
                    
                    for lesson in lessons_list:
                        if isinstance(lesson, dict):
                            # Фильтруем по группе
                            if not is_lesson_for_our_group(lesson):
                                continue
                            
                            date_str = lesson.get("date")
                            if not date_str:
                                continue
                            
                            # Преобразуем формат даты из ДД.МММ.ГГГГ в ГГГГ-ММ-ДД
                            date_str_converted = convert_date_format(date_str)
                            
                            if date_str_converted not in schedule:
                                schedule[date_str_converted] = []
                            
                            # Добавляем пару со временем для сортировки
                            schedule[date_str_converted].append(lesson)

                await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"[API] Ошибка: {e}", exc_info=True)
    
    # Сортируем пары по времени и форматируем
    sorted_schedule = {}
    for date_key, lessons in schedule.items():
        # Сортируем по времени начала
        sorted_lessons = sorted(lessons, key=lambda x: get_lesson_time_minutes(x.get("lessonTime", "")))
        # Форматируем
        formatted_lessons = [format_lesson(les, idx + 1) for idx, les in enumerate(sorted_lessons)]
        sorted_schedule[date_key] = formatted_lessons
    
    logger.info(f"[API] ИТОГО загружено дней: {len(sorted_schedule)}")
    return sorted_schedule

async def scrape_schedule(weeks_ahead: int = 3, debug_dir: str = ".") -> dict:
    return await scrape_schedule_api(HARDCODED_GROUP_ID)

async def load_or_refresh_cache(cache_path: str, max_age_hours: int = 6) -> dict:
    import os
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("schedule", {})
        except: pass
    
    new_data = await scrape_schedule()
    if new_data:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"schedule": new_data, "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False)
        except: pass
    return new_data
