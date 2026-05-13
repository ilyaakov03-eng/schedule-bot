"""
scraper.py — парсинг расписания через API ДВЮИ МВД России
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
TARGET_GROUP = "Ю 16 ПОНБ 2022"

MONTHS_MAP = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

WEEKDAYS_RUS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


def get_lesson_icon(name: str, kind: str) -> str:
    name_l, kind_l = name.lower(), kind.lower()
    if "физическ" in name_l or " фп" in name_l:
        return "💪"
    if "огнев" in name_l:
        return "🔫"
    if "тсп" in name_l or "тактико-спец" in name_l:
        return "🗺️"
    if "лек" in kind_l:
        return "📖"
    return "📝"


def format_lesson(num, name, kind, teacher="", room="") -> str:
    icon = get_lesson_icon(name, kind)
    parts = [f"<b>{num} пара:</b> {icon} {name}"]
    if kind:
        parts.append(f"<i>({kind})</i>")
    if teacher:
        parts.append(f"👨‍🏫 {teacher}")
    if room:
        parts.append(f"🚪 {room}")
    return " ".join(parts)


def get_headers():
    """
    Headers как в браузере для доступа к API.
    """
    return {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://av.dvuimvd.ru/plugins/schedule-student/index.html",
        "Origin": "https://av.dvuimvd.ru",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }


async def get_group_id(client: httpx.AsyncClient) -> str | None:
    """
    Получает список групп и ищет group_id для нужной группы.
    """
    try:
        logger.info(f"[API] Ищу group_id для '{TARGET_GROUP}'...")
        
        resp = await client.get(
            f"{API_BASE}/call/schedule-schedule/structure",
            headers=get_headers(),
            follow_redirects=True
        )
        
        if resp.status_code == 403:
            logger.warning("[API] 403 Forbidden, пробую без токена...")
        
        resp.raise_for_status()
        data = resp.json()
        
        logger.info(f"[API] Получена структура, ищу группу...")
        
        def find_group(obj, target_name):
            if isinstance(obj, dict):
                if obj.get("name") == target_name and obj.get("id"):
                    return obj.get("id")
                for key in ["children", "items", "groups", "courses"]:
                    if key in obj:
                        result = find_group(obj[key], target_name)
                        if result:
                            return result
            elif isinstance(obj, list):
                for item in obj:
                    result = find_group(item, target_name)
                    if result:
                        return result
            return None
        
        group_id = find_group(data, TARGET_GROUP)
        
        if group_id:
            logger.info(f"[API] Найден group_id: {group_id}")
            return str(group_id)
        
        logger.error(f"[API] Группа не найдена")
        return None
        
    except Exception as e:
        logger.error(f"[API] Ошибка при получении структуры: {e}")
        return None


async def scrape_schedule_api(group_id: str, months_count: int = 3) -> dict:
    """
    Получает расписание через API.
    """
    schedule = {}
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            now = datetime.now(VLAD_TZ)
            
            for month_offset in range(months_count):
                target_date = now + timedelta(days=30 * month_offset)
                month = target_date.month
                year = target_date.year
                
                logger.info(f"[API] Грузу расписание {month}/{year}...")
                
                try:
                    url = f"{API_BASE}/call/schedule-schedule/student"
                    params = {
                        "group_id": group_id,
                        "month": month,
                        "year": year,
                    }
                    
                    resp = await client.get(url, params=params, headers=get_headers())
                    resp.raise_for_status()
                    
                    data = resp.json()
                    logger.info(f"[API] Получены данные для {month}/{year}: {len(str(data))} байт")
                    
                    if isinstance(data, dict):
                        lessons_data = data.get("data", data.get("lessons", data.get("schedule", [])))
                    elif isinstance(data, list):
                        lessons_data = data
                    else:
                        lessons_data = []
                    
                    if isinstance(lessons_data, dict):
                        for date_str, lessons in lessons_data.items():
                            if isinstance(lessons, list):
                                day_lessons = []
                                for lesson in lessons:
                                    if isinstance(lesson, dict):
                                        num = lesson.get("num") or lesson.get("number") or lesson.get("lesson") or "?"
                                        name = lesson.get("name") or lesson.get("subject") or lesson.get("discipline") or ""
                                        kind = lesson.get("type") or lesson.get("kind") or lesson.get("lessonType") or ""
                                        teacher = lesson.get("teacher") or lesson.get("lecturer") or lesson.get("fio") or ""
                                        room = lesson.get("room") or lesson.get("classroom") or lesson.get("audience") or ""
                                        
                                        if name:
                                            day_lessons.append(format_lesson(num, name, kind, teacher, room))
                                
                                if day_lessons:
                                    schedule[date_str] = day_lessons
                    
                    elif isinstance(lessons_data, list):
                        for lesson in lessons_data:
                            if isinstance(lesson, dict):
                                date_str = lesson.get("date")
                                if not date_str:
                                    continue
                                
                                num = lesson.get("num") or lesson.get("number") or lesson.get("lesson") or "?"
                                name = lesson.get("name") or lesson.get("subject") or lesson.get("discipline") or ""
                                kind = lesson.get("type") or lesson.get("kind") or lesson.get("lessonType") or ""
                                teacher = lesson.get("teacher") or lesson.get("lecturer") or lesson.get("fio") or ""
                                room = lesson.get("room") or lesson.get("classroom") or lesson.get("audience") or ""
                                
                                if name:
                                    if date_str not in schedule:
                                        schedule[date_str] = []
                                    schedule[date_str].append(format_lesson(num, name, kind, teacher, room))
                    
                except httpx.HTTPError as e:
                    logger.error(f"[API] HTTP ошибка {month}/{year}: {e}")
                except Exception as e:
                    logger.error(f"[API] Ошибка обработки {month}/{year}: {e}")
                
                await asyncio.sleep(0.5)
    
    except Exception as e:
        logger.error(f"[API] Ошибка: {e}", exc_info=True)
    
    logger.info(f"[API] Итого дней: {len(schedule)}")
    return schedule


async def scrape_schedule(weeks_ahead: int = 3, debug_dir: str = "/usr/sbin/bot") -> dict:
    """
    Главная функция.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            group_id = await get_group_id(client)
            if not group_id:
                logger.error("[Scraper] Не удалось получить group_id")
                return {}
            
            schedule = await scrape_schedule_api(group_id, months_count=3)
            return schedule
    
    except Exception as e:
        logger.error(f"[Scraper] Ошибка: {e}", exc_info=True)
        return {}


async def load_or_refresh_cache(cache_path: str, max_age_hours: int = 6) -> dict:
    """
    Загружает кэш или обновляет через API.
    """
    import os
    need_refresh = True
    cached = {}
    
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cached = data.get("schedule", {})
            updated_at = datetime.fromisoformat(data.get("updated_at", "2000-01-01"))
            age = datetime.now() - updated_at
            if age.total_seconds() < max_age_hours * 3600:
                need_refresh = False
                logger.info(f"Кэш актуален (возраст: {age})")
        except Exception as e:
            logger.warning(f"Ошибка кэша: {e}")
    
    if need_refresh:
        logger.info("Обновляю расписание через API...")
        schedule = await scrape_schedule()
        if schedule:
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {"schedule": schedule, "updated_at": datetime.now().isoformat()},
                        f,
                        ensure_ascii=False,
                        indent=2,
                    )
                logger.info(f"Кэш сохранён: {cache_path}")
            except Exception as e:
                logger.error(f"Ошибка сохранения: {e}")
            return schedule
        else:
            logger.warning("API вернул пусто, использую старый кэш")
            return cached
    
    return cached
