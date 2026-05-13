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

# ============ HARDCODED TOKEN И GROUP_ID ============
API_TOKEN = "c68d8b66-34eb-4525-b71b-0d1b62d777f0"
HARDCODED_GROUP_ID = "553"
# ====================================================

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
    # Очистка данных от лишних пробелов и None
    name = str(name).strip() if name else "Неизвестный предмет"
    kind = f"({str(kind).strip()})" if kind else ""
    teacher = f"👨‍🏫 {str(teacher).strip()}" if teacher else ""
    room = f"🚪 {str(room).strip()}" if room else ""
    
    return f"<b>{num} пара:</b> {icon} {name} <i>{kind}</i> {teacher} {room}".strip()


def get_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://av.dvuimvd.ru/",
    }


async def scrape_schedule_api(group_id: str, months_count: int = 2) -> dict:
    schedule = {}
    
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            now = datetime.now(VLAD_TZ)
            
            for month_offset in range(months_count):
                target_date = now + timedelta(days=30 * month_offset)
                month = target_date.month
                year = target_date.year
                
                logger.info(f"[API] Запрос расписания на {month}/{year}...")
                
                url = f"{API_BASE}/call/schedule-schedule/student"
                params = {
                    "token": API_TOKEN,
                    "group_id": group_id,
                    "month": month,
                    "year": year,
                }
                
                resp = await client.get(url, params=params, headers=get_headers())
                resp.raise_for_status()
                data = resp.json()

                # --- ОТЛАДКА СТРУКТУРЫ ---
                logger.info(f"[DEBUG] Ключи в ответе: {list(data.keys()) if isinstance(data, dict) else 'Data is list'}")
                
                # Пытаемся найти, где лежат уроки. Обычно в ключе 'data'
                lessons_raw = None
                if isinstance(data, dict):
                    if "data" in data:
                        lessons_raw = data["data"]
                    else:
                        # Если ключа 'data' нет, возможно уроки в корне
                        lessons_raw = data
                
                if not lessons_raw:
                    logger.warning(f"[API] Данные для {month}/{year} пусты или не распознаны")
                    continue

                # Обработка если уроки сгруппированы по датам (словарь)
                if isinstance(lessons_raw, dict):
                    for date_str, lessons in lessons_raw.items():
                        # Проверяем, что ключ похож на дату (гггг-мм-дд)
                        if "-" in date_str and isinstance(lessons, list):
                            day_lessons = []
                            for les in lessons:
                                if isinstance(les, dict):
                                    name = les.get("name") or les.get("subject")
                                    if not name: continue
                                    
                                    day_lessons.append(format_lesson(
                                        num=les.get("num") or les.get("number") or "?",
                                        name=name,
                                        kind=les.get("type") or les.get("kind") or "",
                                        teacher=les.get("teacher") or les.get("fio") or "",
                                        room=les.get("room") or les.get("auditorium") or ""
                                    ))
                            if day_lessons:
                                schedule[date_str] = day_lessons

                # Обработка если уроки идут списком объектов
                elif isinstance(lessons_raw, list):
                    for les in lessons_raw:
                        if isinstance(les, dict):
                            date_str = les.get("date")
                            name = les.get("name") or les.get("subject")
                            if not date_str or not name: continue
                            
                            if date_str not in schedule:
                                schedule[date_str] = []
                            
                            schedule[date_str].append(format_lesson(
                                num=les.get("num") or les.get("number") or "?",
                                name=name,
                                kind=les.get("type") or les.get("kind") or "",
                                teacher=les.get("teacher") or les.get("fio") or "",
                                room=les.get("room") or les.get("auditorium") or ""
                            ))
                
                await asyncio.sleep(1) # Не спамим API слишком быстро
    
    except Exception as e:
        logger.error(f"[API] Критическая ошибка: {e}", exc_info=True)
    
    logger.info(f"[API] Итого загружено дней: {len(schedule)}")
    return schedule


async def scrape_schedule(weeks_ahead: int = 3, debug_dir: str = ".") -> dict:
    try:
        logger.info(f"[Scraper] Старт для группы ID: {HARDCODED_GROUP_ID}")
        # Грузим за 2 месяца, чтобы наверняка захватить хвосты
        return await scrape_schedule_api(HARDCODED_GROUP_ID, months_count=2)
    except Exception as e:
        logger.error(f"[Scraper] Ошибка: {e}")
        return {}


async def load_or_refresh_cache(cache_path: str, max_age_hours: int = 6) -> dict:
    import os
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            updated_at = datetime.fromisoformat(data.get("updated_at", "2000-01-01"))
            if (datetime.now() - updated_at).total_seconds() < max_age_hours * 3600:
                logger.info("Использую актуальный кэш.")
                return data.get("schedule", {})
        except:
            pass
    
    logger.info("Кэш устарел или отсутствует. Обновляю...")
    new_data = await scrape_schedule()
    if new_data:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"schedule": new_data, "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка записи кэша: {e}")
    return new_data
