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
# ====================================================

WEEKDAYS_RUS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS_MAP = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

def get_lesson_icon(name: str) -> str:
    n = str(name).lower()
    if "физическ" in n or " фп" in n: return "💪"
    if "огнев" in n: return "🔫"
    if "тсп" in n or "тактико" in n: return "🗺️"
    return "📝"

def format_lesson(les: dict) -> str:
    num = les.get("num") or les.get("number") or "?"
    name = les.get("name") or les.get("subject") or les.get("discipline") or "Предмет"
    kind = les.get("type") or les.get("kind") or ""
    teacher = les.get("teacher") or les.get("fio") or ""
    room = les.get("room") or les.get("auditorium") or ""
    
    icon = get_lesson_icon(name)
    
    res = f"<b>{num} пара:</b> {icon} {name}"
    if kind: res += f" <i>({kind})</i>"
    if teacher: res += f"\n   👨‍🏫 {teacher}"
    if room: res += f"\n   🚪 {room}"
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
                            date_str = lesson.get("date")
                            if not date_str:
                                continue
                            
                            # Преобразуем формат даты из ДД.МММ.ГГГГ в ГГГГ-ММ-ДД
                            date_str = convert_date_format(date_str)
                            
                            if date_str not in schedule:
                                schedule[date_str] = []
                            
                            formatted = format_lesson(lesson)
                            schedule[date_str].append(formatted)
                            logger.info(f"[API] Добавлена пара на {date_str}")

                await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"[API] Ошибка: {e}", exc_info=True)
    
    logger.info(f"[API] ИТОГО загружено дней: {len(schedule)}")
    return schedule

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
