import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz

from scraper import MONTHS_MAP, WEEKDAYS_RUS


VLAD_TZ = pytz.timezone("Asia/Vladivostok")
MATERIALS_PATH = Path(__file__).with_name("materials.json")

SUBJECT_ALIASES = {
    "tax_law": [
        "налог",
        "налоговое",
        "налоговое право",
        "нп",
    ],
    "drug_crimes": [
        "наркотики",
        "наркот",
        "нон",
        "психотроп",
        "наркотических",
    ],
    "personal_property_crimes": [
        "личность",
        "собственность",
        "личности",
        "рплис",
        "против личности",
    ],
    "business_law": [
        "предприним",
        "предпринимательское",
        "предпринимательское право",
        "пп",
    ],
}


def load_materials(path: Path = MATERIALS_PATH) -> dict:
    if not path.exists():
        return {"subjects": {}, "items": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def norm(value: str) -> str:
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"[^а-яa-z0-9.]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def compact_topic(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def normalize_class_type(value: str) -> str:
    value = norm(value)
    if value.startswith("пр") or "практи" in value:
        return "пр"
    if value.startswith("сем") or "семинар" in value:
        return "сем"
    return value


def find_subject_id(text: str, materials: dict | None = None) -> str | None:
    text_norm = norm(text)
    for subject_id, aliases in SUBJECT_ALIASES.items():
        if any(norm(alias) in text_norm for alias in aliases):
            return subject_id

    if materials:
        for subject_id, subject in materials.get("subjects", {}).items():
            if norm(subject.get("discipline", "")) in text_norm:
                return subject_id
    return None


def subject_discipline(subject_id: str, materials: dict) -> str:
    return materials.get("subjects", {}).get(subject_id, {}).get("discipline", subject_id)


def lesson_matches_subject(lesson: dict, subject_id: str, materials: dict) -> bool:
    discipline = norm(lesson.get("discipline", ""))
    wanted = norm(subject_discipline(subject_id, materials))
    return wanted and wanted in discipline


def parse_requested_date(text: str, now: datetime | None = None) -> date | None:
    now = now or datetime.now(VLAD_TZ)
    text_norm = norm(text)

    if "послезавтра" in text_norm:
        return (now + timedelta(days=2)).date()
    if "сегодня" in text_norm:
        return now.date()
    if "завтра" in text_norm:
        return (now + timedelta(days=1)).date()

    weekdays = {
        "понедельник": 0,
        "пн": 0,
        "вторник": 1,
        "вт": 1,
        "среда": 2,
        "ср": 2,
        "четверг": 3,
        "чт": 3,
        "пятница": 4,
        "пт": 4,
        "суббота": 5,
        "сб": 5,
    }
    for name, weekday in weekdays.items():
        if name in text_norm.split():
            days_ahead = weekday - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            return now.date() + timedelta(days=days_ahead)

    match = re.search(r"(\d{1,2})\s+([а-яе]+)", text_norm)
    if match:
        day = int(match.group(1))
        month = MONTHS_MAP.get(match.group(2).replace("е", "ё"), MONTHS_MAP.get(match.group(2)))
        if month:
            return date(now.year, month, day)

    match = re.search(r"(\d{1,2})\.(\d{1,2})", text_norm)
    if match:
        return date(now.year, int(match.group(2)), int(match.group(1)))

    return None


def find_material_for_lesson(lesson: dict, materials: dict) -> dict | None:
    lesson_discipline = norm(lesson.get("discipline", ""))
    lesson_class_type = normalize_class_type(lesson.get("class_type") or lesson.get("class_type_name", ""))
    lesson_topic = compact_topic(lesson.get("topic_code") or lesson.get("topic_name"))
    lesson_theme = lesson_topic.split("-")[0].split(".")[0] if lesson_topic else ""

    candidates = []
    for item in materials.get("items", []):
        if norm(item.get("discipline", "")) not in lesson_discipline:
            continue
        if normalize_class_type(item.get("class_type", "")) != lesson_class_type:
            continue
        candidates.append(item)

    for item in candidates:
        if compact_topic(item.get("topic_code")) == lesson_topic:
            return item

    for item in candidates:
        if compact_topic(item.get("theme_code")) == lesson_theme:
            return item

    return None


def find_lessons(schedule: dict, subject_id: str, materials: dict, target_date: date | None) -> list[dict]:
    now = datetime.now(VLAD_TZ).date()
    if target_date:
        lessons = schedule.get(target_date.strftime("%Y-%m-%d"), [])
        return [lesson for lesson in lessons if lesson_matches_subject(lesson, subject_id, materials)]

    for offset in range(0, 60):
        day = now + timedelta(days=offset)
        lessons = [
            lesson
            for lesson in schedule.get(day.strftime("%Y-%m-%d"), [])
            if lesson_matches_subject(lesson, subject_id, materials)
        ]
        if lessons:
            return lessons
    return []


def format_material_message(lesson: dict, material: dict, lesson_date: date) -> str:
    day_name = WEEKDAYS_RUS[lesson_date.weekday()]
    class_type = normalize_class_type(lesson.get("class_type", ""))
    class_title = "Практическое занятие" if class_type == "пр" else "Семинар"
    topic = lesson.get("topic_code") or material.get("topic_code") or material.get("theme_code")

    lines = [
        "📚 <b>Задания на пару</b>",
        f"📅 {lesson_date.strftime('%d.%m')} ({day_name}) · {lesson.get('lesson_time', '')}",
        f"📖 <b>{lesson.get('discipline', material.get('discipline'))}</b>",
        f"{class_title} · тема {topic}",
        f"<b>{material.get('title', '')}</b>",
        "",
    ]

    for idx, task in enumerate(material.get("tasks", []), start=1):
        lines.append(f"{idx}. {task}")

    return "\n".join(lines)


def get_exam_questions(subject_id: str, materials: dict) -> list[str]:
    return materials.get("exam_questions", {}).get(subject_id, [])


def format_exam_questions_message(discipline: str, questions: list[str]) -> str:
    lines = [
        "🎓 <b>Вопросы на зачёт</b>",
        f"📖 <b>{discipline}</b>",
        f"Всего вопросов: {len(questions)}",
        "",
    ]
    for idx, question in enumerate(questions, start=1):
        lines.append(f"{idx}. {question}")

    return "\n".join(lines)


def split_telegram_message(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks = []
    current = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks
