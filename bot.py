# -*- coding: utf-8 -*-
import os
import json
import logging
import sqlite3
from datetime import datetime, time as dt_time
from pathlib import Path
import asyncio
import re

import google.generativeai as genai
from google.generativeai.types import generation_types

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackQueryHandler,
)

from kerykeion import AstrologicalSubject, NatalAspects
from kerykeion.kr_types import KerykeionException

# .env ფაილიდან გარემოს ცვლადების ჩატვირთვა
load_dotenv()

# --- გლობალური კონფიგურაცია და ცვლადები ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEONAMES_USERNAME = os.getenv("GEONAMES_USERNAME")
DB_FILE = "user_data.db"
TELEGRAM_MESSAGE_LIMIT = 4096
DEFAULT_UNKNOWN_TIME = dt_time(12, 0)

ASPECT_PLANETS = ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'Pluto', 'Ascendant', 'Midheaven']
MAJOR_ASPECTS_TYPES = ['conjunction', 'opposition', 'square', 'trine', 'sextile']
ASPECT_ORBS = {'Sun': 8, 'Moon': 8, 'Ascendant': 5, 'Midheaven': 5, 'default': 6}

# --- Gemini კონფიგურაცია ---
gemini_model = None
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    try:
        gemini_model = genai.GenerativeModel(
            'gemini-1.5-flash-latest',
            safety_settings=safety_settings
        )
        logging.info("Gemini model loaded successfully.")
    except Exception as e:
        logging.error(f"Failed to load Gemini model: {e}", exc_info=True)
else:
    logging.warning("GEMINI_API_KEY not found. AI features disabled.")

# ლოგირება
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("kerykeion").setLevel(logging.INFO)
logging.getLogger("google.generativeai").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- თარგმანები ---
translations = {
    "ka": {
        "language_chosen": "თქვენ აირჩიეთ ქართული ენა.",
        "welcome_new_user": "პირველ რიგში უნდა შევადგინოთ თქვენი ნატალური რუკა.",
        "create_chart_button_text": "📜 რუკის შედგენა",
        "welcome_existing_user_1": "თქვენი შენახული მონაცემებია:",
        "welcome_existing_user_2": "გამოიყენეთ '📜 რუკის შედგენა' ახალი რუკისთვის.",
        "menu_mydata": "/mydata - მონაცემების ჩვენება.",
        "menu_deletedata": "/deletedata - მონაცემების წაშლა.",
        "start_createchart_no_data": "გამოიყენეთ '📜 რუკის შედგენა'.",
        "chart_creation_prompt": "რუკის შესაქმნელად მჭირდება თქვენი მონაცემები.\nშეგიძლიათ გაუქმოთ /cancel-ით.",
        "ask_name": "შეიყვანეთ სახელი:",
        "name_thanks": "გმადლობთ, {name}.\nშეიყვანეთ დაბადების თარიღი: <b>წწწწ/თთ/დდ</b> (მაგ., <code>1989/11/29</code>):",
        "invalid_name": "სახელი უნდა იყოს მინ. 2 სიმბოლო. სცადეთ თავიდან:",
        "invalid_date_format": "თარიღის ფორმატი არასწორია. გამოიყენეთ <b>წწწწ/თთ/დდ</b>:",
        "invalid_year_range": "წელი უნდა იყოს {start_year}-დან {end_year}-მდე.",
        "ask_time": "შეიყვანეთ დაბადების დრო: <b>სს:წწ</b> (მაგ., <code>15:30</code>) ან 'დრო უცნობია'.",
        "time_unknown_button": "დრო უცნობია (12:00)",
        "invalid_time_format": "დროის ფორმატი არასწორია. გამოიყენეთ <b>სს:წწ</b> ან 'დრო უცნობია'.",
        "ask_country": "შეიყვანეთ ქვეყანა:",
        "invalid_country": "შეიყვანეთ სწორი ქვეყანა.",
        "ask_city": "შეიყვანეთ ქალაქი ({country}-ში):",
        "invalid_city": "შეიყვანეთ სწორი ქალაქი.",
        "data_collection_complete": "მონაცემები შეგროვდა. ვქმნი რუკას...",
        "cancel_button_text": "/cancel",
        "saved_data_exists_1": "რუკა უკვე არსებობს ({name}, {day}/{month}/{year}).",
        "saved_data_exists_2": "ნახვა თუ ახალი მონაცემები?",
        "use_saved_chart_button": "კი, ვნახოთ შენახული",
        "enter_new_data_button": "არა, ახალი მონაცემები",
        "cancel_creation_button": "გაუქმება",
        "using_saved_chart": "თქვენი შენახული რუკა:",
        "chart_generation_cancelled": "რუკის შექმნა გაუქმდა.",
        "invalid_choice": "არასწორი არჩევანი.",
        "data_saved": "მონაცემები შენახულია.",
        "data_save_error": "მონაცემების შენახვის შეცდომა.",
        "chart_ready_menu_prompt": "რუკა მზადაა. აირჩიეთ მოქმედება:",
        "my_data_header": "თქვენი მონაცემები:\n",
        "my_data_name": "  <b>სახელი:</b> {name}\n",
        "my_data_date": "  <b>თარიღი:</b> {day}/{month}/{year}\n",
        "my_data_time": "  <b>დრო:</b> {hour}:{minute}\n",
        "my_data_city": "  <b>ქალაქი:</b> {city}\n",
        "my_data_country": "  <b>ქვეყანა:</b> {nation_or_text}\n",
        "not_specified": "არ არის მითითებული",
        "no_data_found": "მონაცემები არ მოიძებნა. გამოიყენეთ '📜 რუკის შედგენა'.",
        "data_deleted_success": "მონაცემები წაიშალა.",
        "data_delete_error": "მონაცემების წაშლის შეცდომა.",
        "processing_kerykeion": "ვამუშავებ ასტროლოგიურ მონაცემებს...",
        "geonames_warning_user": "⚠️ GeoNames სახელი არ არის დაყენებული.",
        "kerykeion_city_error": "შეცდომა: '{city}' ვერ მოიძებნა.",
        "kerykeion_general_error": "ასტროლოგიური მონაცემების გამოთვლის შეცდომა.",
        "aspect_calculation_error_user": "⚠️ ასპექტების გამოთვლის შეცდომა.",
        "gemini_prompt_start": "ვქმნი ინტერპრეტაციებს...\n⏳ 1-3 წუთი.",
        "gemini_interpretation_failed": "ინტერპრეტაციების გენერაცია ჩაიშალა.",
        "chart_error_generic": "რუკის გენერაციის შეცდომა.",
        "main_menu_button_view_chart": "📜 რუკის ნახვა",
        "main_menu_button_dream": "🌙 სიზმრის ახსნა",
        "main_menu_button_horoscope": "🔮 ჰოროსკოპი",
        "main_menu_button_palmistry": "🖐️ ქირომანტია",
        "main_menu_button_coffee": "☕ ყავაში ჩახედვა",
        "main_menu_button_delete_data": "🗑️ მონაცემების წაშლა",
        "main_menu_button_help": "❓ დახმარება",
        "feature_coming_soon": "ფუნქცია '{feature_name}' მალე დაემატება.",
        "gemini_main_prompt_intro": "შენ ხარ გამოცდილი ასტროლოგი, რომელიც ქმნის დეტალურ ნატალურ რუკას {language} ენაზე.",
        "gemini_main_prompt_instruction_1": "მიჰყევი სტრუქტურას და თითოეულ პუნქტზე დაწერე 3-5 წინადადება ({name}).",
        "gemini_main_prompt_instruction_2": "გამოიყენე თბილი, გასაგები ენა.",
        "gemini_main_prompt_instruction_3": "იყავი ზუსტი, PDF ნიმუშის მსგავსად.",
        "gemini_data_header": "**მონაცემები:**",
        "gemini_name": "სახელი: {name}",
        "gemini_birth_date_time": "დაბადება: {day}/{month}/{year}, {hour:02d}:{minute:02d}",
        "gemini_birth_location": "ადგილი: {city}{location_nation_suffix}",
        "gemini_systems_used": "სისტემები: ზოდიაქო - ტროპიკული, სახლები - პლაციდუსი",
        "gemini_planet_positions_header": "**პლანეტები (ნიშანი, გრადუსი, სახლი, რეტრო):**",
        "gemini_aspects_header": "**ასპექტები (პლანეტა1, ასპექტი, პლანეტა2, ორბისი):**",
        "gemini_task_header": "**დავალება:**",
        "gemini_task_instruction_1": "დაწერე ანალიზი სექციებად:",
        "gemini_section_pis_start": "[SECTION: PlanetsInSignsStart]",
        "gemini_pis_instruction": "(პლანეტები ნიშნებში. თითოეულისთვის დაწერე ანალიზი.)",
        "gemini_section_pis_end": "[SECTION: PlanetsInSignsEnd]",
        "gemini_section_pih_start": "[SECTION: PlanetsInHousesStart]",
        "gemini_pih_instruction": "(პლანეტები სახლებში. თითოეულისთვის ანალიზი.)",
        "gemini_section_pih_end": "[SECTION: PlanetsInHousesEnd]",
        "gemini_section_aspects_start": "[SECTION: AspectsStart]",
        "gemini_aspects_instruction": "(ასპექტები. თითოეულისთვის ანალიზი.)",
        "gemini_section_aspects_end": "[SECTION: AspectsEnd]",
        "gemini_final_instruction": "დაწერე მხოლოდ სექციების ტექსტი, შესავლის/დასკვნის გარეშე."
    },
    "en": {
        "language_chosen": "You have selected English.",
        "welcome_new_user": "First, we need to create your natal chart...",
        "create_chart_button_text": "📜 Create Chart",
        "ask_name": "Please enter the name:",
        "name_thanks": "Thank you, {name}.\nEnter birth date (YYYY/MM/DD):",
        "invalid_name": "Invalid name.",
        "invalid_date_format": "Invalid date format. Use YYYY/MM/DD.",
        "invalid_year_range": "Invalid year. Use YYYY/MM/DD.",
        "ask_time": "Enter birth time (HH:MM) or 'Time Unknown'.",
        "time_unknown_button": "Time Unknown (12:00)",
        "invalid_time_format": "Invalid time format. Use HH:MM or 'Time Unknown'.",
        "ask_country": "Enter country of birth:",
        "invalid_country": "Invalid country.",
        "ask_city": "Enter city of birth (in {country}):",
        "invalid_city": "Invalid city.",
        "data_collection_complete": "Data collected. Generating chart...",
        "cancel_button_text": "/cancel",
        "main_menu_text": "Choose an action:",
        "view_chart_button": "📜 View Chart",
        "dream_button": "🌙 Dream Interpretation",
        "horoscope_button": "🔮 Horoscope",
        "palmistry_button": "🖐️ Palmistry",
        "coffee_button": "☕ Coffee Reading",
        "delete_data_button": "🗑️ Delete Data",
        "help_button": "❓ Help",
        "feature_coming_soon": "Feature '{feature_name}' coming soon!",
        "data_saved": "Data saved.",
        "data_save_error": "Error saving data.",
        "chart_ready_menu_prompt": "Chart ready. Main menu:",
        "welcome_existing_user_1": "Your saved data:",
        "welcome_existing_user_2": "Use 'Create Chart' menu button.",
        "menu_mydata": "/mydata - Show data",
        "menu_deletedata": "/deletedata - Delete data",
        "start_createchart_no_data": "Use 'Create Chart' menu button.",
        "chart_creation_prompt": "To create chart, I need your data. /cancel anytime.",
        "saved_data_exists_1": "Chart exists for {name} ({day}/{month}/{year}).",
        "saved_data_exists_2": "View it or create new?",
        "use_saved_chart_button": "View saved",
        "enter_new_data_button": "Create new",
        "cancel_creation_button": "Cancel",
        "using_saved_chart": "Here's your saved chart:",
        "chart_generation_cancelled": "Chart creation cancelled.",
        "invalid_choice": "Invalid choice.",
        "my_data_header": "Your saved data:\n",
        "my_data_name": "  <b>Name:</b> {name}\n",
        "my_data_date": "  <b>Date:</b> {day}/{month}/{year}\n",
        "my_data_time": "  <b>Time:</b> {hour}:{minute}\n",
        "my_data_city": "  <b>City:</b> {city}\n",
        "my_data_country": "  <b>Country:</b> {nation_or_text}\n",
        "not_specified": "Not specified",
        "no_data_found": "No data found. Use 'Create Chart'.",
        "data_deleted_success": "Data deleted successfully.",
        "data_delete_error": "Error deleting data.",
        "processing_kerykeion": "Processing astrological data...",
        "geonames_warning_user": "Warning: GEONAMES_USERNAME not set.",
        "kerykeion_city_error": "Error: City '{city}' not found.",
        "kerykeion_general_error": "Error calculating astro data.",
        "aspect_calculation_error_user": "Warning: Aspect calculation error.",
        "gemini_prompt_start": "Generating interpretations...\n⏳ 1-3 minutes.",
        "gemini_interpretation_failed": "Failed to generate interpretations.",
        "chart_error_generic": "Unexpected error generating chart.",
        "gemini_main_prompt_intro": "You are an experienced astrologer writing a detailed natal chart in {language}.",
        "gemini_main_prompt_instruction_1": "Follow structure, write 3-5 sentences per point for {name}.",
        "gemini_main_prompt_instruction_2": "Use warm, clear language.",
        "gemini_main_prompt_instruction_3": "Be precise, like the PDF sample.",
        "gemini_data_header": "**Birth Data:**",
        "gemini_name": "Name: {name}",
        "gemini_birth_date_time": "Date: {day}/{month}/{year}, {hour:02d}h {minute:02d}m",
        "gemini_birth_location": "Place: {city}{location_nation_suffix}",
        "gemini_systems_used": "Systems: Zodiac - Tropical, Houses - Placidus",
        "gemini_planet_positions_header": "**Planets (Sign, Degree, House, Retro):**",
        "gemini_aspects_header": "**Aspects (Planet1, Aspect, Planet2, Orb):**",
        "gemini_task_header": "**Task:**",
        "gemini_task_instruction_1": "Write analysis in sections:",
        "gemini_section_pis_start": "[SECTION: PlanetsInSignsStart]",
        "gemini_pis_instruction": "(Planets in Signs. Analyze each.)",
        "gemini_section_pis_end": "[SECTION: PlanetsInSignsEnd]",
        "gemini_section_pih_start": "[SECTION: PlanetsInHousesStart]",
        "gemini_pih_instruction": "(Planets in Houses. Analyze each.)",
        "gemini_section_pih_end": "[SECTION: PlanetsInHousesEnd]",
        "gemini_section_aspects_start": "[SECTION: AspectsStart]",
        "gemini_aspects_instruction": "(Aspects. Analyze each.)",
        "gemini_section_aspects_end": "[SECTION: AspectsEnd]",
        "gemini_final_instruction": "Return only section texts, no intro/conclusion."
    },
    "ru": {
        "language_chosen": "Вы выбрали русский язык.",
        "welcome_new_user": "Сначала создадим вашу натальную карту...",
        "create_chart_button_text": "📜 Составить карту",
        "ask_name": "Введите имя:",
        "name_thanks": "Спасибо, {name}.\nВведите дату (ГГГГ/ММ/ДД):",
        "invalid_name": "Неверное имя.",
        "invalid_date_format": "Неверный формат. Используйте ГГГГ/ММ/ДД.",
        "invalid_year_range": "Неверный год.",
        "ask_time": "Введите время (ЧЧ:ММ) или 'Время неизвестно'.",
        "time_unknown_button": "Время неизвестно (12:00)",
        "invalid_time_format": "Неверный формат времени. Используйте ЧЧ:ММ.",
        "ask_country": "Введите страну:",
        "invalid_country": "Неверная страна.",
        "ask_city": "Введите город ({country}):",
        "invalid_city": "Неверный город.",
        "data_collection_complete": "Данные собраны. Генерация карты...",
        "cancel_button_text": "/cancel",
        "main_menu_text": "Выберите действие:",
        "view_chart_button": "📜 Посмотреть карту",
        "dream_button": "🌙 Толкование снов",
        "horoscope_button": "🔮 Гороскоп",
        "palmistry_button": "🖐️ Хиромантия",
        "coffee_button": "☕ Гадание на кофе",
        "delete_data_button": "🗑️ Удалить данные",
        "help_button": "❓ Помощь",
        "feature_coming_soon": "Функция '{feature_name}' скоро появится!",
        "data_saved": "Данные сохранены.",
        "data_save_error": "Ошибка сохранения.",
        "chart_ready_menu_prompt": "Карта готова. Главное меню:",
        "welcome_existing_user_1": "Ваши данные:",
        "welcome_existing_user_2": "Используйте 'Составить карту'.",
        "menu_mydata": "/mydata - Показать данные",
        "menu_deletedata": "/deletedata - Удалить данные",
        "start_createchart_no_data": "Используйте 'Составить карту'.",
        "chart_creation_prompt": "Для карты нужны данные. /cancel для отмены.",
        "saved_data_exists_1": "Карта есть для {name} ({day}/{month}/{year}).",
        "saved_data_exists_2": "Посмотреть или новая?",
        "use_saved_chart_button": "Посмотреть",
        "enter_new_data_button": "Создать новую",
        "cancel_creation_button": "Отмена",
        "using_saved_chart": "Ваша карта:",
        "chart_generation_cancelled": "Создание отменено.",
        "invalid_choice": "Неверный выбор.",
        "my_data_header": "Ваши данные:\n",
        "my_data_name": "  <b>Имя:</b> {name}\n",
        "my_data_date": "  <b>Дата:</b> {day}/{month}/{year}\n",
        "my_data_time": "  <b>Время:</b> {hour}:{minute}\n",
        "my_data_city": "  <b>Город:</b> {city}\n",
        "my_data_country": "  <b>Страна:</b> {nation_or_text}\n",
        "not_specified": "Не указано",
        "no_data_found": "Данные не найдены. Используйте 'Составить карту'.",
        "data_deleted_success": "Данные удалены.",
        "data_delete_error": "Ошибка удаления.",
        "processing_kerykeion": "Обработка данных...",
        "geonames_warning_user": "Внимание: GEONAMES_USERNAME не задан.",
        "kerykeion_city_error": "Ошибка: Город '{city}' не найден.",
        "kerykeion_general_error": "Ошибка расчета.",
        "aspect_calculation_error_user": "Ошибка расчета аспектов.",
        "gemini_prompt_start": "Генерация...\n⏳ 1-3 минуты.",
        "gemini_interpretation_failed": "Ошибка генерации.",
        "chart_error_generic": "Ошибка генерации карты.",
        "gemini_main_prompt_intro": "Вы астролог, создающий анализ карты на {language}.",
        "gemini_main_prompt_instruction_1": "Следуйте структуре, 3-5 предложений для {name}.",
        "gemini_main_prompt_instruction_2": "Используйте понятный язык.",
        "gemini_main_prompt_instruction_3": "Будьте точны, как в PDF.",
        "gemini_data_header": "**Данные:**",
        "gemini_name": "Имя: {name}",
        "gemini_birth_date_time": "Дата: {day}/{month}/{year}, {hour:02d}:{minute:02d}",
        "gemini_birth_location": "Место: {city}{location_nation_suffix}",
        "gemini_systems_used": "Системы: Зодиак - Тропический, Дома - Плацидус",
        "gemini_planet_positions_header": "**Планеты (Знак, Градус, Дом, Ретро):**",
        "gemini_aspects_header": "**Аспекты (Планета1, Аспект, Планета2, Орб):**",
        "gemini_task_header": "**Задание:**",
        "gemini_task_instruction_1": "Напишите анализ по секциям:",
        "gemini_section_pis_start": "[SECTION: PlanetsInSignsStart]",
        "gemini_pis_instruction": "(Планеты в знаках. Анализ для каждой.)",
        "gemini_section_pis_end": "[SECTION: PlanetsInSignsEnd]",
        "gemini_section_pih_start": "[SECTION: PlanetsInHousesStart]",
        "gemini_pih_instruction": "(Планеты в домах. Анализ для каждой.)",
        "gemini_section_pih_end": "[SECTION: PlanetsInHousesEnd]",
        "gemini_section_aspects_start": "[SECTION: AspectsStart]",
        "gemini_aspects_instruction": "(Аспекты. Анализ для каждого.)",
        "gemini_section_aspects_end": "[SECTION: AspectsEnd]",
        "gemini_final_instruction": "Верните только текст секций."
    }
}
DEFAULT_LANGUAGE = "ka"

# --- ფუნქციები ---
def get_text(key: str, lang_code: str | None = None, context: ContextTypes.DEFAULT_TYPE | None = None) -> str:
    final_lang_code = lang_code or (context.user_data.get('lang_code') if context and context.user_data else DEFAULT_LANGUAGE)
    primary_translation_dict = translations.get(final_lang_code, {})
    text = primary_translation_dict.get(key)
    if text is None and final_lang_code != "en":
        text = translations.get("en", {}).get(key)
    if text is None and final_lang_code != "ka":
        text = translations.get("ka", {}).get(key)
    return text or f"TR_ERROR['{key}':'{final_lang_code}']"

def init_db():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_birth_data (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                year INTEGER,
                month INTEGER,
                day INTEGER,
                hour INTEGER,
                minute INTEGER,
                city TEXT,
                nation TEXT,
                language_code TEXT,
                full_chart_text TEXT
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"Database {DB_FILE} initialized.")
    except sqlite3.Error as e:
        logger.error(f"Database init error: {e}")

def save_user_data(user_id: int, data: dict, chart_text: str | None = None):
    try:
        lang_code_to_save = data.get('lang_code', DEFAULT_LANGUAGE)
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO user_birth_data
            (user_id, name, year, month, day, hour, minute, city, nation, language_code, full_chart_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, data.get('name'), data.get('year'), data.get('month'), data.get('day'),
            data.get('hour'), data.get('minute'), data.get('city'), data.get('nation'),
            lang_code_to_save, chart_text
        ))
        conn.commit()
        conn.close()
        logger.info(f"Data saved for user {user_id}")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error saving data for user {user_id}: {e}")
        return False

def get_user_data(user_id: int) -> dict | None:
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_birth_data WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except sqlite3.Error as e:
        logger.error(f"Error retrieving data for user {user_id}: {e}")
        return None

def delete_user_data(user_id: int):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_birth_data WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        logger.info(f"Data deleted for user {user_id}")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error deleting data for user {user_id}: {e}")
        return False

planet_emojis = {
    "Sun": "☀️", "Moon": "🌙", "Mercury": "☿️", "Venus": "♀️", "Mars": "♂️",
    "Jupiter": "♃", "Saturn": "♄", "Uranus": "♅", "Neptune": "♆", "Pluto": "♇",
    "Ascendant": "⬆️", "Midheaven": " Mᶜ",
}
aspect_translations = {
    "conjunction": "შეერთება", "opposition": "ოპოზიცია", "square": "კვადრატი",
    "trine": "ტრიგონი", "sextile": "სექსტილი"
}
aspect_symbols = {
    "conjunction": "☌", "opposition": "☍", "square": "□",
    "trine": "△", "sextile": "∗"
}

async def get_gemini_interpretation(prompt: str) -> str:
    if not gemini_model:
        return "(Gemini API მიუწვდომელია)"
    try:
        response = await gemini_model.generate_content_async(
            prompt,
            generation_config={"response_mime_type": "text/plain"},
            request_options={"timeout": 180}
        )
        if not response.candidates:
            feedback = getattr(response, 'prompt_feedback', None)
            block_reason = getattr(feedback, 'block_reason', 'Unknown') if feedback else 'Unknown'
            logger.warning(f"Gemini response blocked. Reason: {block_reason}")
            return f"(Gemini-მ დაბლოკა: {block_reason})"
        if hasattr(response.candidates[0].content, 'parts') and response.candidates[0].content.parts:
            return "".join(part.text for part in response.candidates[0].content.parts).strip()
        logger.warning(f"Gemini response invalid.")
        return "(Gemini-მ არასწორი პასუხი დააბრუნა)"
    except Exception as e:
        logger.error(f"Gemini error: {e}", exc_info=True)
        return f"(შეცდომა: {type(e).__name__})"

def split_text(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT - 100) -> list[str]:
    parts = []
    current_part = ""
    for paragraph in text.split('\n\n'):
        if len((current_part + paragraph + '\n\n').encode('utf-8')) > limit:
            if current_part:
                parts.append(current_part.strip())
            current_part = paragraph + '\n\n'
        else:
            current_part += paragraph + '\n\n'
    if current_part:
        parts.append(current_part.strip())
    
    final_parts = []
    for part in parts:
        if len(part.encode('utf-8')) > limit:
            temp_text = part
            while len(temp_text.encode('utf-8')) > limit:
                split_pos_char = len(temp_text[:limit-10].encode('utf-8').decode('utf-8', errors='ignore'))
                final_parts.append(temp_text[:split_pos_char])
                temp_text = temp_text[split_pos_char:].lstrip()
            if temp_text:
                final_parts.append(temp_text)
        elif part:
            final_parts.append(part)
    return final_parts

def get_main_menu_keyboard(lang_code: str):
    keyboard = [
        [KeyboardButton(get_text("main_menu_button_view_chart", lang_code)), KeyboardButton(get_text("main_menu_button_dream", lang_code))],
        [KeyboardButton(get_text("main_menu_button_horoscope", lang_code)), KeyboardButton(get_text("main_menu_button_palmistry", lang_code))],
        [KeyboardButton(get_text("main_menu_button_coffee", lang_code))],
        [KeyboardButton(get_text("main_menu_button_delete_data", lang_code)), KeyboardButton(get_text("main_menu_button_help", lang_code))],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def generate_and_send_chart(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, is_new_data: bool = False, data_to_process: dict | None = None):
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    current_user_data = data_to_process or get_user_data(user_id)

    if not current_user_data:
        await context.bot.send_message(chat_id=chat_id, text=get_text("no_data_found", lang_code))
        return

    name = current_user_data.get('name', 'User')
    year = current_user_data.get('year')
    month = current_user_data.get('month')
    day = current_user_data.get('day')
    hour = current_user_data.get('hour')
    minute = current_user_data.get('minute')
    city = current_user_data.get('city')
    nation = current_user_data.get('nation')

    if not all([name, year, month, day, isinstance(hour, int), isinstance(minute, int), city]):
        await context.bot.send_message(chat_id=chat_id, text="მონაცემები არასრულია.")
        return

    if not is_new_data and current_user_data.get('full_chart_text'):
        parts = split_text(current_user_data['full_chart_text'])
        for part in parts:
            await context.bot.send_message(chat_id=chat_id, text=part, parse TRIGGER_WARNING: You have reached the context length limit. The conversation will now be reset. Please provide any additional information or ask a new question to continue.