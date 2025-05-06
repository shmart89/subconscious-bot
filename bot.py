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
        "gemini_final_instruction": "დაწერე მხოლოდ სექციების ტექსტი, შესავლის/დასკვნის გარეშე.",
        "section_title_pis": "პლანეტები ნიშნებში",
        "section_title_pih": "პლანეტები სახლებში",
        "section_title_aspects": "ასპექტები",
        "time_note_12_00": "შენიშვნა: გამოყენებულია ნაგულისხმევი დრო 12:00, რადგან ზუსტი დრო უცნობია."
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
        "gemini_final_instruction": "Return only section texts, no intro/conclusion.",
        "section_title_pis": "Planets in Signs",
        "section_title_pih": "Planets in Houses",
        "section_title_aspects": "Aspects",
        "time_note_12_00": "Note: Default time 12:00 used as exact time is unknown."
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
        "gemini_final_instruction": "Верните только текст секций.",
        "section_title_pis": "Планеты в Знаках",
        "section_title_pih": "Планеты в Домах",
        "section_title_aspects": "Аспекты",
        "time_note_12_00": "Примечание: Использовано время 12:00, так как точное время неизвестно."
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
        return ConversationHandler.END

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
        return ConversationHandler.END

    if not is_new_data and current_user_data.get('full_chart_text'):
        logger.info(f"Displaying saved chart for user {user_id}")
        parts = split_text(current_user_data['full_chart_text'])
        for part in parts:
            await context.bot.send_message(chat_id=chat_id, text=part, parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=chat_id, text=get_text("main_menu_text", lang_code), reply_markup=get_main_menu_keyboard(lang_code))
        return ConversationHandler.END

    logger.info(f"Generating Kerykeion data for: {name}, {day}/{month}/{year} {hour}:{minute}, {city}, {nation}")
    processing_message = await context.bot.send_message(chat_id=chat_id, text=get_text("processing_kerykeion", lang_code))

    try:
        if not GEONAMES_USERNAME:
            logger.warning("GEONAMES_USERNAME not set.")
            await context.bot.send_message(chat_id=chat_id, text=get_text("geonames_warning_user", lang_code))

        subject_instance = await asyncio.to_thread(
            AstrologicalSubject, name, year, month, day, hour, minute, city, nation=nation, geonames_username=GEONAMES_USERNAME
        )
        logger.info(f"Kerykeion data generated for {name}.")

        aspects_data_str_for_prompt = ""
        try:
            aspect_calculator = NatalAspects(
                subject_instance,
                aspects_list=MAJOR_ASPECTS_TYPES,
                planets_to_consider=ASPECT_PLANETS,
                orb_dictionary=ASPECT_ORBS
            )
            all_filtered_aspects = aspect_calculator.get_relevant_aspects()
            if all_filtered_aspects:
                for aspect in all_filtered_aspects:
                    p1 = aspect.get('p1_name')
                    p2 = aspect.get('p2_name')
                    aspect_type = aspect.get('aspect')
                    orb = aspect.get('orbit', 0.0)
                    if p1 and p2 and aspect_type:
                        aspect_name_ge = aspect_translations.get(aspect_type, aspect_type)
                        aspect_symbol_char = aspect_symbols.get(aspect_type, "")
                        p1_emoji = planet_emojis.get(p1, "")
                        p2_emoji = planet_emojis.get(p2, "")
                        aspects_data_str_for_prompt += f"- {p1_emoji}{p1} {aspect_symbol_char} {p2_emoji}{p2} ({aspect_name_ge}, ორბისი {orb:.1f}°)\n"
            if not aspects_data_str_for_prompt:
                aspects_data_str_for_prompt = "- მნიშვნელოვანი ასპექტები ვერ მოიძებნა.\n"
        except Exception as aspect_err:
            logger.error(f"Aspect calculation error: {aspect_err}", exc_info=True)
            aspects_data_str_for_prompt = "- ასპექტების გამოთვლის შეცდომა.\n"
            await context.bot.send_message(chat_id=chat_id, text=get_text("aspect_calculation_error_user", lang_code))

        planets_data_str_for_prompt = ""
        planet_list_for_prompt = ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'Pluto', 'Ascendant', 'Midheaven']
        for planet_name in planet_list_for_prompt:
            try:
                obj_name_in_kerykeion = planet_name.lower()
                if planet_name == "Midheaven": obj_name_in_kerykeion = "mc"
                elif planet_name == "Ascendant": obj_name_in_kerykeion = "ascendant"
                planet_obj = getattr(subject_instance, obj_name_in_kerykeion)
                sign = planet_obj.get('sign', '?')
                pos = planet_obj.get('position', 0.0)
                house_val = planet_obj.get('house')
                house_str = f", {house_val}-ე სახლი" if isinstance(house_val, int) else ""
                retro = " (R)" if planet_obj.get('isRetro') == 'true' else ""
                planets_data_str_for_prompt += f"- {planet_name}: {sign} {pos:.2f}°{house_str}{retro}\n"
            except AttributeError:
                if planet_name == "Ascendant": planet_obj = getattr(subject_instance, "first_house", None)
                elif planet_name == "Midheaven": planet_obj = getattr(subject_instance, "tenth_house", None)
                else: planet_obj = None
                if planet_obj:
                    sign = planet_obj.get('sign', '?')
                    pos = planet_obj.get('position', 0.0)
                    planets_data_str_for_prompt += f"- {planet_name}: {sign} {pos:.2f}°\n"
                else:
                    logger.error(f"Error getting data for {planet_name}")
                    planets_data_str_for_prompt += f"- {planet_name}: მონაცემების შეცდომა\n"
            except Exception as e:
                logger.error(f"Error getting data for {planet_name}: {e}")
                planets_data_str_for_prompt += f"- {planet_name}: სრული მონაცემების შეცდომა\n"

        gemini_lang_name = "ქართულ" if lang_code == "ka" else "ინგლისურ" if lang_code == "en" else "რუსულ"
        large_prompt = (
            get_text("gemini_main_prompt_intro", lang_code).format(language=gemini_lang_name) + "\n" +
            get_text("gemini_main_prompt_instruction_1", lang_code).format(name=name) + "\n" +
            get_text("gemini_main_prompt_instruction_2", lang_code) + "\n" +
            get_text("gemini_main_prompt_instruction_3", lang_code) + "\n\n" +
            get_text("gemini_data_header", lang_code) + "\n" +
            get_text("gemini_name", lang_code).format(name=name) + "\n" +
            get_text("gemini_birth_date_time", lang_code).format(day=day, month=month, year=year, hour=hour, minute=minute) + "\n" +
            get_text("gemini_birth_location", lang_code).format(city=city, location_nation_suffix=(f', {nation}' if nation else '')) + "\n" +
            get_text("gemini_systems_used", lang_code) + "\n\n" +
            get_text("gemini_planet_positions_header", lang_code) + "\n" +
            planets_data_str_for_prompt + "\n" +
            get_text("gemini_aspects_header", lang_code) + "\n" +
            aspects_data_str_for_prompt + "\n" +
            get_text("gemini_task_header", lang_code) + "\n" +
            get_text("gemini_task_instruction_1", lang_code) + "\n" +
            get_text("gemini_section_pis_start", lang_code) + "\n" +
            get_text("gemini_pis_instruction", lang_code) + "\n" +
            get_text("gemini_section_pis_end", lang_code) + "\n\n" +
            get_text("gemini_section_pih_start", lang_code) + "\n" +
            get_text("gemini_pih_instruction", lang_code) + "\n" +
            get_text("gemini_section_pih_end", lang_code) + "\n\n" +
            get_text("gemini_section_aspects_start", lang_code) + "\n" +
            get_text("gemini_aspects_instruction", lang_code) + "\n" +
            get_text("gemini_section_aspects_end", lang_code) + "\n\n" +
            get_text("gemini_final_instruction", lang_code)
        )

        await processing_message.edit_text(text=get_text("gemini_prompt_start", lang_code), parse_mode=ParseMode.HTML)
        full_interpretation_text = await get_gemini_interpretation(large_prompt)
        logger.info(f"Received interpretation for user {chat_id}. Length: {len(full_interpretation_text)}")

        save_user_data(user_id, current_user_data, chart_text=full_interpretation_text)
        current_user_data['full_chart_text'] = full_interpretation_text

        final_report_parts = []
        base_info_text = (
            f"✨ {name}-ს ნატალური რუკა ✨\n\n"
            f"<b>დაბადების მონაცემები:</b> {day}/{month}/{year}, {hour:02d}:{minute:02d}, {city}{f', {nation}' if nation else ''}\n"
            f"<b>{get_text('gemini_systems_used', lang_code)}</b>\n\n"
        )
        try:
            sun_info = subject_instance.sun
            base_info_text += f"{planet_emojis.get('Sun')} <b>მზე:</b> {sun_info['sign']} (<code>{sun_info['position']:.2f}°</code>)\n"
        except:
            pass
        try:
            asc_info = subject_instance.ascendant
            base_info_text += f"{planet_emojis.get('Ascendant')} <b>ასცედენტი:</b> {asc_info['sign']} (<code>{asc_info['position']:.2f}°</code>)\n"
        except:
            pass
        time_note = f"\n<i>{get_text('time_note_12_00', lang_code)}</i>" if hour == 12 and minute == 0 else ""
        base_info_text += time_note + "\n"
        final_report_parts.append(base_info_text)

        pis_text = re.search(r"\[SECTION:\s*PlanetsInSignsStart\](.*?)\[SECTION:\s*PlanetsInSignsEnd\]", full_interpretation_text, re.DOTALL | re.IGNORECASE)
        pih_text = re.search(r"\[SECTION:\s*PlanetsInHousesStart\](.*?)\[SECTION:\s*PlanetsInHousesEnd\]", full_interpretation_text, re.DOTALL | re.IGNORECASE)
        asp_text_match = re.search(r"\[SECTION:\s*AspectsStart\](.*?)\[SECTION:\s*AspectsEnd\]", full_interpretation_text, re.DOTALL | re.IGNORECASE)

        if pis_text and pis_text.group(1).strip():
            final_report_parts.append(f"\n--- 🪐 <b>{get_text('section_title_pis', lang_code)}</b> ---\n\n{pis_text.group(1).strip()}")
        if pih_text and pih_text.group(1).strip():
            final_report_parts.append(f"\n--- 🏠 <b>{get_text('section_title_pih', lang_code)}</b> ---\n\n{pih_text.group(1).strip()}")
        if asp_text_match and asp_text_match.group(1).strip():
            final_report_parts.append(f"\n--- ✨ <b>{get_text('section_title_aspects', lang_code)}</b> ---\n\n{asp_text_match.group(1).strip()}")

        if len(final_report_parts) == 1 and full_interpretation_text.startswith("("):
            final_report_parts.append(f"\n<b>ინტერპრეტაცია ვერ მოხერხდა:</b>\n{full_interpretation_text}")

        full_response_text = "".join(final_report_parts).strip()
        if not full_response_text or full_response_text == base_info_text.strip():
            await processing_message.edit_text(text=get_text("gemini_interpretation_failed", lang_code))
            return ConversationHandler.END

        parts = split_text(full_response_text)
        await processing_message.edit_text(text=parts[0], parse_mode=ParseMode.HTML)
        for part in parts[1:]:
            await context.bot.send_message(chat_id=chat_id, text=part, parse_mode=ParseMode.HTML)

    except KerykeionException as ke:
        logger.error(f"KerykeionException: {ke}", exc_info=False)
        await processing_message.edit_text(text=get_text("kerykeion_city_error", lang_code).format(city=city))
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        await processing_message.edit_text(text=get_text("chart_error_generic", lang_code))
        return ConversationHandler.END

    await context.bot.send_message(chat_id=chat_id, text=get_text("chart_ready_menu_prompt", lang_code), reply_markup=get_main_menu_keyboard(lang_code))
    return ConversationHandler.END

# --- ConversationHandler-ის მდგომარეობები ---
(LANG_CHOICE, SAVED_DATA_OR_NAME, NAME_CONV, BIRTH_DATE_CONV, BIRTH_TIME_CONV, COUNTRY_CONV, CITY_CONV) = range(7)

# --- Handler ფუნქციები ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    context.user_data['lang_code'] = lang_code

    user_data = get_user_data(user_id)
    if user_data:
        reply_text = (
            get_text("welcome_existing_user_1", lang_code) + "\n" +
            get_text("welcome_existing_user_2", lang_code)
        )
        keyboard = [
            [InlineKeyboardButton(get_text("create_chart_button_text", lang_code), callback_data='initiate_chart_creation')]
        ]
    else:
        reply_text = get_text("welcome_new_user", lang_code)
        keyboard = [
            [InlineKeyboardButton(get_text("create_chart_button_text", lang_code), callback_data='initiate_chart_creation')]
        ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(reply_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    return LANG_CHOICE

async def handle_language_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang_code = query.data.split('_')[1]
    context.user_data['lang_code'] = lang_code

    await query.edit_message_text(text=get_text("language_chosen", lang_code))
    user_data = get_user_data(update.effective_user.id)
    if user_data:
        keyboard = [
            [InlineKeyboardButton(get_text("use_saved_chart_button", lang_code), callback_data='use_saved_chart_conv')],
            [InlineKeyboardButton(get_text("enter_new_data_button", lang_code), callback_data='enter_new_data_conv')],
            [InlineKeyboardButton(get_text("cancel_creation_button", lang_code), callback_data='cancel_creation_conv')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(
            get_text("saved_data_exists_1", lang_code).format(
                name=user_data['name'], day=user_data['day'], month=user_data['month'], year=user_data['year']
            ) + "\n" + get_text("saved_data_exists_2", lang_code),
            reply_markup=reply_markup
        )
        return SAVED_DATA_OR_NAME
    else:
        await query.message.reply_text(
            get_text("chart_creation_prompt", lang_code),
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
        )
        await query.message.reply_text(get_text("ask_name", lang_code))
        return NAME_CONV

async def initiate_chart_creation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    user_data = get_user_data(update.effective_user.id)

    if user_data:
        keyboard = [
            [InlineKeyboardButton(get_text("use_saved_chart_button", lang_code), callback_data='use_saved_chart_conv')],
            [InlineKeyboardButton(get_text("enter_new_data_button", lang_code), callback_data='enter_new_data_conv')],
            [InlineKeyboardButton(get_text("cancel_creation_button", lang_code), callback_data='cancel_creation_conv')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            get_text("saved_data_exists_1", lang_code).format(
                name=user_data['name'], day=user_data['day'], month=user_data['month'], year=user_data['year']
            ) + "\n" + get_text("saved_data_exists_2", lang_code),
            reply_markup=reply_markup
        )
        return SAVED_DATA_OR_NAME
    else:
        await query.edit_message_text(text=get_text("chart_creation_prompt", lang_code))
        await query.message.reply_text(
            get_text("ask_name", lang_code),
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
        )
        return NAME_CONV

async def create_chart_start_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    user_data = get_user_data(user_id)

    if user_data:
        keyboard = [
            [InlineKeyboardButton(get_text("use_saved_chart_button", lang_code), callback_data='use_saved_chart_conv')],
            [InlineKeyboardButton(get_text("enter_new_data_button", lang_code), callback_data='enter_new_data_conv')],
            [InlineKeyboardButton(get_text("cancel_creation_button", lang_code), callback_data='cancel_creation_conv')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            get_text("saved_data_exists_1", lang_code).format(
                name=user_data['name'], day=user_data['day'], month=user_data['month'], year=user_data['year']
            ) + "\n" + get_text("saved_data_exists_2", lang_code),
            reply_markup=reply_markup
        )
        return SAVED_DATA_OR_NAME
    else:
        await update.message.reply_text(
            get_text("chart_creation_prompt", lang_code),
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
        )
        await update.message.reply_text(get_text("ask_name", lang_code))
        return NAME_CONV

async def handle_saved_data_choice_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if query.data == 'use_saved_chart_conv':
        await query.edit_message_text(text=get_text("using_saved_chart", lang_code))
        await generate_and_send_chart(user_id, chat_id, context)
        return ConversationHandler.END
    elif query.data == 'enter_new_data_conv':
        await query.edit_message_text(text=get_text("chart_creation_prompt", lang_code))
        await query.message.reply_text(
            get_text("ask_name", lang_code),
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
        )
        return NAME_CONV
    else:
        await query.edit_message_text(text=get_text("chart_generation_cancelled", lang_code))
        await query.message.reply_text(
            get_text("main_menu_text", lang_code),
            reply_markup=get_main_menu_keyboard(lang_code)
        )
        return ConversationHandler.END

async def handle_name_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text(
            get_text("invalid_name", lang_code),
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
        )
        return NAME_CONV
    context.user_data['chart_data'] = {'name': name, 'lang_code': lang_code}
    await update.message.reply_text(
        get_text("name_thanks", lang_code).format(name=name),
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
    )
    return BIRTH_DATE_CONV

async def handle_birth_date_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    text = update.message.text.strip()
    try:
        year, month, day = map(int, text.split('/'))
        if not (1900 <= year <= 2025):
            await update.message.reply_text(
                get_text("invalid_year_range", lang_code).format(start_year=1900, end_year=2025),
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
            )
            return BIRTH_DATE_CONV
        context.user_data['chart_data'].update({'year': year, 'month': month, 'day': day})
        keyboard = [[KeyboardButton(get_text("time_unknown_button", lang_code))], [KeyboardButton(get_text("cancel_button_text", lang_code))]]
        await update.message.reply_text(
            get_text("ask_time", lang_code),
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return BIRTH_TIME_CONV
    except ValueError:
        await update.message.reply_text(
            get_text("invalid_date_format", lang_code),
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
        )
        return BIRTH_DATE_CONV

async def handle_birth_time_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    text = update.message.text.strip()
    if text == get_text("time_unknown_button", lang_code):
        hour, minute = 12, 0
    else:
        try:
            hour, minute = map(int, text.split(':'))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                get_text("invalid_time_format", lang_code),
                reply_markup=ReplyKeyboardMarkup([
                    [KeyboardButton(get_text("time_unknown_button", lang_code))],
                    [KeyboardButton(get_text("cancel_button_text", lang_code))]
                ], resize_keyboard=True)
            )
            return BIRTH_TIME_CONV
    context.user_data['chart_data'].update({'hour': hour, 'minute': minute})
    await update.message.reply_text(
        get_text("ask_country", lang_code),
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
    )
    return COUNTRY_CONV

async def handle_country_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    country = update.message.text.strip()
    if len(country) < 2:
        await update.message.reply_text(
            get_text("invalid_country", lang_code),
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
        )
        return COUNTRY_CONV
    context.user_data['chart_data']['nation'] = country
    await update.message.reply_text(
        get_text("ask_city", lang_code).format(country=country),
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
    )
    return CITY_CONV

async def handle_city_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    city = update.message.text.strip()
    if len(city) < 2:
        await update.message.reply_text(
            get_text("invalid_city", lang_code),
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
        )
        return CITY_CONV
    context.user_data['chart_data']['city'] = city
    await update.message.reply_text(get_text("data_collection_complete", lang_code))
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    await generate_and_send_chart(user_id, chat_id, context, is_new_data=True, data_to_process=context.user_data['chart_data'])
    return ConversationHandler.END

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    await update.message.reply_text(
        get_text("chart_generation_cancelled", lang_code),
        reply_markup=get_main_menu_keyboard(lang_code)
    )
    return ConversationHandler.END

async def my_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    user_data = get_user_data(user_id)
    if not user_data:
        await update.message.reply_text(
            get_text("no_data_found", lang_code),
            reply_markup=get_main_menu_keyboard(lang_code)
        )
        return
    reply_text = (
        get_text("my_data_header", lang_code) +
        get_text("my_data_name", lang_code).format(name=user_data['name']) +
        get_text("my_data_date", lang_code).format(day=user_data['day'], month=user_data['month'], year=user_data['year']) +
        get_text("my_data_time", lang_code).format(hour=user_data['hour'], minute=user_data['minute']) +
        get_text("my_data_city", lang_code).format(city=user_data['city']) +
        get_text("my_data_country", lang_code).format(nation_or_text=user_data['nation'] or get_text("not_specified", lang_code))
    )
    await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard(lang_code))

async def view_my_chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    user_data = get_user_data(user_id)
    if not user_data:
        await update.message.reply_text(
            get_text("no_data_found", lang_code),
            reply_markup=get_main_menu_keyboard(lang_code)
        )
        return
    await update.message.reply_text(get_text("using_saved_chart", lang_code))
    await generate_and_send_chart(user_id, chat_id, context)

async def delete_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    if delete_user_data(user_id):
        await update.message.reply_text(
            get_text("data_deleted_success", lang_code),
            reply_markup=get_main_menu_keyboard(lang_code)
        )
    else:
        await update.message.reply_text(
            get_text("data_delete_error", lang_code),
            reply_markup=get_main_menu_keyboard(lang_code)
        )

async def handle_other_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    user_message = update.message.text
    feature_name = user_message
    await update.message.reply_text(
        get_text("feature_coming_soon", lang_code).format(feature_name=feature_name),
        reply_markup=get_main_menu_keyboard(lang_code)
    )

async def ask_for_name_direct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    await query.edit_message_text(text=get_text("chart_creation_prompt", lang_code))
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=get_text("ask_name", lang_code),
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
    )
    return NAME_CONV

async def prompt_for_name_after_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    await update.message.reply_text(
        get_text("ask_name", lang_code),
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True)
    )
    return NAME_CONV

# --- მთავარი ფუნქცია ---
def main() -> None:
    init_db()
    if not TELEGRAM_BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN not set.")
        return

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    main_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            LANG_CHOICE: [
                CallbackQueryHandler(handle_language_choice, pattern='^lang_(ka|en|ru)$'),
                CallbackQueryHandler(initiate_chart_creation_callback, pattern='^initiate_chart_creation$')
            ],
            SAVED_DATA_OR_NAME: [
                CallbackQueryHandler(handle_saved_data_choice_conv, pattern='^(use_saved_chart_conv|enter_new_data_conv|cancel_creation_conv)$'),
                CallbackQueryHandler(ask_for_name_direct, pattern='^initiate_chart_creation_direct$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_for_name_after_lang)
            ],
            NAME_CONV: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name_conv)],
            BIRTH_DATE_CONV: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_birth_date_conv)],
            BIRTH_TIME_CONV: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_birth_time_conv)],
            COUNTRY_CONV: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_country_conv)],
            CITY_CONV: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_city_conv)],
        },
        fallbacks=[CommandHandler('cancel', cancel_conv)],
        allow_reentry=True
    )

    application.add_handler(main_conv_handler)
    application.add_handler(CommandHandler("createchart", create_chart_start_conv))
    application.add_handler(CommandHandler("mydata", my_data_command))
    application.add_handler(CommandHandler("deletedata", delete_data_command))

    main_menu_buttons_regex_parts = []
    for lang_code_iter in ["ka", "en", "ru"]:
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_view_chart", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_delete_data", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_dream", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_horoscope", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_palmistry", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_coffee", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_help", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("create_chart_button_text", lang_code_iter)))

    unique_button_texts = set(main_menu_buttons_regex_parts)
    combined_regex = '^(' + '|'.join(unique_button_texts) + ')$'

    async def general_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_message = update.message.text
        lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
        if user_message == get_text("main_menu_button_view_chart", lang_code):
            await view_my_chart_command(update, context)
        elif user_message == get_text("main_menu_button_delete_data", lang_code):
            await delete_data_command(update, context)
        elif user_message == get_text("create_chart_button_text", lang_code):
            await create_chart_start_conv(update, context)
        else:
            await handle_other_menu_buttons(update, context)

    application.add_handler(MessageHandler(filters.Regex(combined_regex) & filters.TEXT & ~filters.COMMAND, general_menu_handler))

    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    load_dotenv()
    main()