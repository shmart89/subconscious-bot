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

# .env ფაილიდან გარემოს ცვლადების ჩატვირთვა (სკრიპტის დასაწყისშივე)
load_dotenv()

# --- გლობალური კონფიგურაცია და ცვლადები ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEONAMES_USERNAME = os.getenv("GEONAMES_USERNAME") # !!! ეს უნდა იყოს .env-ში PythonAnywhere-ზე !!!
DB_FILE = "user_data.db"
TELEGRAM_MESSAGE_LIMIT = 4096
DEFAULT_UNKNOWN_TIME = dt_time(12, 0) # შუადღე, როგორც ნაგულისხმევი

ASPECT_PLANETS = ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'Pluto', 'Ascendant', 'Midheaven']
MAJOR_ASPECTS_TYPES = ['conjunction', 'opposition', 'square', 'trine', 'sextile']
ASPECT_ORBS = {'Sun': 8, 'Moon': 8, 'Ascendant': 5, 'Midheaven': 5, 'default': 6} # ორბისები

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
    logging.warning("GEMINI_API_KEY not found in environment variables. AI features will be disabled.")

# ლოგირების ჩართვა
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
        "welcome_new_user": "პირველ რიგში უნდა შევადგინოთ თქვენი ნატალური რუკა, რათა ჩვენი მიმოწერა უფრო პერსონალური და ზუსტი გახდეს.",
        "create_chart_button_text": "📜 რუკის შედგენა",
        "welcome_existing_user_1": "თქვენი შენახული მონაცემებია:",
        "welcome_existing_user_2": "გამოიყენეთ მენიუს ღილაკი '📜 რუკის შედგენა' ახალი რუკის შესადგენად (შეგიძლიათ აირჩიოთ შენახული მონაცემების გამოყენება).",
        "menu_mydata": "/mydata - შენახული მონაცემების ჩვენება.", # ეს ბრძანებაც შეიძლება ღილაკით ჩანაცვლდეს
        "menu_deletedata": "/deletedata - შენახული მონაცემების წაშლა.", # ესეც
        "start_createchart_no_data": "ნატალური რუკის შესაქმნელად გამოიყენეთ მენიუს ღილაკი '📜 რუკის შედგენა'.",
        "chart_creation_prompt": "ნატალური რუკის შესაქმნელად, მჭირდება თქვენი მონაცემები.\nშეგიძლიათ ნებისმიერ დროს შეწყვიტოთ პროცესი /cancel ბრძანებით.",
        "ask_name": "გთხოვთ, შეიყვანოთ სახელი, ვისთვისაც ვადგენთ რუკას:",
        "name_thanks": "გმადლობთ, {name}.\nახლა გთხოვთ, შეიყვანოთ დაბადების სრული თარიღი ფორმატით: <b>წწწწ/თთ/დდ</b> (მაგალითად, <code>1989/11/29</code>):",
        "invalid_name": "სახელი უნდა შეიცავდეს მინიმუმ 2 სიმბოლოს. სცადეთ თავიდან:",
        "invalid_date_format": "თარიღის ფორმატი არასწორია. გთხოვთ, შეიყვანოთ <b>წწწწ/თთ/დდ</b> ფორმატით (მაგ., <code>1989/11/29</code>):",
        "invalid_year_range": "წელი უნდა იყოს {start_year}-სა და {end_year}-ს შორის. გთხოვთ, შეიყვანოთ თარიღი სწორი ფორმატით <b>წწწწ/თთ/დდ</b>:",
        "ask_time": "გმადლობთ. ახლა გთხოვთ, შეიყვანოთ დაბადების დრო ფორმატით <b>სს:წწ</b> (მაგალითად, <code>15:30</code>), ან დააჭირეთ 'დრო უცნობია' ღილაკს.",
        "time_unknown_button": "დრო უცნობია (12:00)",
        "invalid_time_format": "დროის ფორმატი არასწორია. გთხოვთ, შეიყვანოთ <b>სს:წწ</b> ფორმატით (მაგ., <code>15:30</code>) ან დააჭირეთ 'დრო უცნობია'.",
        "ask_country": "შეიყვანეთ დაბადების ქვეყანა (მაგ., საქართველო, Germany):",
        "invalid_country": "გთხოვთ, შეიყვანოთ კორექტული ქვეყნის სახელი.",
        "ask_city": "შეიყვანეთ დაბადების ქალაქი ({country}-ში):",
        "invalid_city": "გთხოვთ, შეიყვანოთ კორექტული ქალაქის სახელი.",
        "data_collection_complete": "მონაცემების შეგროვება დასრულებულია. ვიწყებ რუკის შედგენას...",
        "cancel_button_text": "/cancel", # ეს ღილაკი ReplyKeyboard-ისთვის
        "saved_data_exists_1": "თქვენ უკვე შენახული გაქვთ რუკა ({name}, {day}/{month}/{year}...).",
        "saved_data_exists_2": "გსურთ მისი ნახვა თუ ახლის შედგენა?",
        "use_saved_chart_button": "კი, ვნახოთ შენახული რუკა",
        "enter_new_data_button": "არა, შევიყვანოთ ახალი მონაცემები",
        "cancel_creation_button": "გაუქმება", # Inline ღილაკისთვის
        "using_saved_chart": "აი, თქვენი შენახული ნატალური რუკა:",
        "chart_generation_cancelled": "რუკის შექმნა გაუქმებულია.",
        "invalid_choice": "არასწორი არჩევანი. გთხოვთ, სცადოთ თავიდან.",
        "data_saved": "მონაცემები შენახულია.",
        "data_save_error": "მონაცემების შენახვისას მოხდა შეცდომა.",
        "chart_ready_menu_prompt": "თქვენი რუკა მზადაა. ეხლა კი შევუდგეთ თქვენს ყოველდღიურ მომსახურებას:",
        "my_data_header": "თქვენი შენახული მონაცემებია:\n",
        "my_data_name": "  <b>სახელი:</b> {name}\n",
        "my_data_date": "  <b>თარიღი:</b> {day}/{month}/{year}\n",
        "my_data_time": "  <b>დრო:</b> {hour}:{minute}\n",
        "my_data_city": "  <b>ქალაქი:</b> {city}\n",
        "my_data_country": "  <b>ქვეყანა:</b> {nation_or_text}\n",
        "not_specified": "არ არის მითითებული",
        "no_data_found": "თქვენ არ გაქვთ შენახული მონაცემები. გამოიყენეთ მენიუს ღილაკი '📜 რუკის შედგენა' დასამატებლად.",
        "data_deleted_success": "თქვენი შენახული მონაცემები და რუკა წარმატებით წაიშალა.",
        "data_delete_error": "მონაცემების წაშლისას მოხდა შეცდომა ან მონაცემები არ არსებობდა.",
        "processing_kerykeion": "მონაცემები მიღებულია, ვიწყებ ასტროლოგიური მონაცემების გამოთვლას...",
        "geonames_warning_user": "⚠️ გაფრთხილება: GeoNames მომხმარებლის სახელი არ არის დაყენებული. ქალაქის ძებნა შეიძლება ვერ მოხერხდეს ან არასწორი იყოს. რეკომენდებულია მისი დამატება.",
        "kerykeion_city_error": "შეცდომა: Kerykeion-მა ვერ იპოვა მონაცემები ქალაქისთვის '{city}'. გთხოვთ, შეამოწმოთ ქალაქის სახელი და სცადოთ თავიდან.",
        "kerykeion_general_error": "შეცდომა მოხდა ასტროლოგიური მონაცემების გამოთვლისას.",
        "aspect_calculation_error_user": "⚠️ გაფრთხილება: ასპექტების გამოთვლისას მოხდა შეცდომა.",
        "gemini_prompt_start": "ასტროლოგიური მონაცემები გამოთვლილია. ვიწყებ დეტალური ინტერპრეტაციების გენერირებას Gemini-სთან...\n⏳ ამას შეიძლება 1-3 წუთი დასჭირდეს.",
        "gemini_interpretation_failed": "ინტერპრეტაციების გენერირება ვერ მოხერხდა. სცადეთ მოგვიანებით.",
        "chart_error_generic": "მოულოდნელი შეცდომა მოხდა რუკის გენერაციისას.",
        "main_menu_button_view_chart": "📜 რუკის ნახვა",
        "main_menu_button_dream": "🌙 სიზმრის ახსნა",
        "main_menu_button_horoscope": "🔮 ჰოროსკოპი",
        "main_menu_button_palmistry": "🖐️ ქირომანტია",
        "main_menu_button_coffee": "☕ ყავაში ჩახედვა",
        "main_menu_button_delete_data": "🗑️ მონაცემების წაშლა",
        "main_menu_button_help": "❓ დახმარება",
        "feature_coming_soon": "ფუნქცია '{feature_name}' მალე დაემატება. გთხოვთ, აირჩიოთ სხვა მოქმედება:",
        "gemini_main_prompt_intro": "შენ ხარ გამოცდილი, პროფესიონალი ასტროლოგი, რომელიც წერს სიღრმისეულ და დეტალურ ნატალური რუკის ანალიზს {language} ენაზე.",
        "gemini_main_prompt_instruction_1": "მიჰყევი მოთხოვნილ სტრუქტურას და თითოეულ პუნქტზე დაწერე 3-5 ვრცელი წინადადება, რომელიც ხსნის მის მნიშვნელობას მოცემული ადამიანისთვის ({name}).",
        "gemini_main_prompt_instruction_2": "გამოიყენე პროფესიონალური, მაგრამ ამავდროულად თბილი და გასაგები ენა. მოერიდე დაზეპირებულ ფრაზებს.",
        "gemini_main_prompt_instruction_3": "იყავი მაქსიმალურად ზუსტი და დეტალური, PDF ნიმუშის მსგავსად.",
        "gemini_data_header": "**მონაცემები:**",
        "gemini_name": "სახელი: {name}",
        "gemini_birth_date_time": "დაბადების თარიღი: {day}/{month}/{year}, {hour:02d} საათი და {minute:02d} წუთი",
        "gemini_birth_location": "დაბადების ადგილი: {city}{location_nation_suffix}",
        "gemini_systems_used": "გამოყენებული სისტემები: ზოდიაქო - ტროპიკული, სახლები - პლაციდუსი",
        "gemini_planet_positions_header": "**პლანეტების მდებარეობა (ნიშანი, გრადუსი, სახლი, რეტროგრადულობა):**",
        "gemini_aspects_header": "**მნიშვნელოვანი ასპექტები (პლანეტა1, ასპექტი, პლანეტა2, ორბისი):**",
        "gemini_task_header": "**დავალება:**",
        "gemini_task_instruction_1": "დაწერე სრული ანალიზი, დაყოფილი შემდეგ სექციებად. გამოიყენე ზუსტად ეს სექციების სახელები და ფორმატირება (მაგ., `[SECTION: PlanetsInSignsStart]`):",
        "gemini_section_pis_start": "[SECTION: PlanetsInSignsStart]",
        "gemini_pis_instruction": "(აქ იწყება პლანეტები ნიშნებში. თითოეული პლანეტისთვის (Sun-Pluto) დაწერე დეტალური ანალიზი მის ნიშანში. მაგალითად: \"მზე ვერძში: ...\")",
        "gemini_section_pis_end": "[SECTION: PlanetsInSignsEnd]",
        "gemini_section_pih_start": "[SECTION: PlanetsInHousesStart]",
        "gemini_pih_instruction": "(აქ იწყება პლანეტები სახლებში. თითოეული პლანეტისთვის (Sun-Pluto) დაწერე დეტალური ანალიზი მის სახლში, თუ სახლის ნომერი ცნობილია. მაგალითად: \"მთვარე მე-5 სახლში: ...\")",
        "gemini_section_pih_end": "[SECTION: PlanetsInHousesEnd]",
        "gemini_section_aspects_start": "[SECTION: AspectsStart]",
        "gemini_aspects_instruction": "(აქ იწყება ასპექტები. თითოეული ჩამოთვლილი ასპექტისთვის დაწერე დეტალური ანალიზი. მაგალითად: \"მზე შეერთება იუპიტერი: ...\")",
        "gemini_section_aspects_end": "[SECTION: AspectsEnd]",
        "gemini_final_instruction": "გთხოვ, პასუხი დააბრუნო მხოლოდ ამ სამი სექციის ტექსტით, ტეგებს შორის. არ დაამატო შესავალი ან დასკვნითი სიტყვები."
    },
    # დაამატეთ en და ru თარგმანები აქ, ka-ს მსგავსად
    "en": { "language_chosen": "You have selected English.", "welcome_new_user": "First, we need to create your natal chart...", "create_chart_button_text": "📜 Create Chart", "ask_name": "Please enter the name:", "name_thanks": "Thank you, {name}.\nNow, please enter the full date of birth (YYYY/MM/DD):", "invalid_name": "Invalid name.", "invalid_date_format": "Invalid date format. Use YYYY/MM/DD.", "invalid_year_range": "Invalid year. Use YYYY/MM/DD.", "ask_time": "Enter birth time (HH:MM) or click 'Time Unknown'.", "time_unknown_button": "Time Unknown (12:00)", "invalid_time_format": "Invalid time format. Use HH:MM or 'Time Unknown'.", "ask_country": "Enter country of birth:", "invalid_country": "Invalid country.", "ask_city": "Enter city of birth (in {country}):", "invalid_city": "Invalid city.", "data_collection_complete": "Data collection complete. Generating chart...", "cancel_button_text": "/cancel", "main_menu_text": "Choose an action:", "view_chart_button": "📜 View Chart", "dream_button": "🌙 Dream Interpretation", "horoscope_button": "🔮 Horoscope", "palmistry_button": "🖐️ Palmistry", "coffee_button": "☕ Coffee Reading", "delete_data_button": "🗑️ Delete Data", "help_button": "❓ Help", "feature_coming_soon": "Feature '{feature_name}' coming soon!", "data_saved": "Data saved.", "data_save_error":"Error saving data.", "chart_ready_menu_prompt": "Your chart is ready. Main menu:", "welcome_existing_user_1": "Your saved data:", "welcome_existing_user_2": "Use 'Create Chart' menu button.", "menu_mydata": "/mydata - Show data", "menu_deletedata": "/deletedata - Delete data", "start_createchart_no_data":"Use 'Create Chart' menu button.", "chart_creation_prompt": "To create chart, I need your data. /cancel anytime.", "saved_data_exists_1":"Chart already exists for {name} ({day}/{month}/{year}).", "saved_data_exists_2":"View it or create new?", "use_saved_chart_button":"View saved", "enter_new_data_button":"Create new", "cancel_creation_button":"Cancel", "using_saved_chart":"Here's your saved chart:", "chart_generation_cancelled":"Chart creation cancelled.", "invalid_choice":"Invalid choice.", "my_data_header":"Your saved data:\n", "my_data_name":"  <b>Name:</b> {name}\n", "my_data_date":"  <b>Date:</b> {day}/{month}/{year}\n", "my_data_time":"  <b>Time:</b> {hour}:{minute}\n", "my_data_city":"  <b>City:</b> {city}\n", "my_data_country":"  <b>Country:</b> {nation_or_text}\n", "not_specified":"Not specified", "no_data_found":"No data found. Use 'Create Chart'.", "data_deleted_success":"Data deleted successfully.", "data_delete_error":"Error deleting data.", "processing_kerykeion":"Processing astrological data...", "geonames_warning_user":"Warning: GEONAMES_USERNAME not set.", "kerykeion_city_error":"Error: City '{city}' not found.", "kerykeion_general_error":"Error calculating astro data.", "aspect_calculation_error_user":"Warning: Aspect calculation error.", "gemini_prompt_start":"Generating interpretations...\n⏳ This may take 1-3 minutes.", "gemini_interpretation_failed":"Failed to generate interpretations.", "chart_error_generic":"Unexpected error generating chart.",
        "gemini_main_prompt_intro": "You are an experienced, professional astrologer writing an in-depth and detailed natal chart analysis in {language}.",
        "gemini_main_prompt_instruction_1": "Follow the requested structure and for each point, write at least 3-5 detailed sentences explaining its significance for the given person ({name}).",
        "gemini_main_prompt_instruction_2": "Use professional, yet warm and understandable language. Avoid clichéd phrases.",
        "gemini_main_prompt_instruction_3": "Be as accurate and detailed as possible, similar to the PDF sample.",
        "gemini_data_header": "**Birth Data:**", "gemini_name": "Name: {name}", "gemini_birth_date_time": "Date of Birth: {day}/{month}/{year}, {hour:02d}h {minute:02d}m", "gemini_birth_location": "Place of Birth: {city}{location_nation_suffix}", "gemini_systems_used": "Systems Used: Zodiac - Tropical, Houses - Placidus", "gemini_planet_positions_header": "**Planetary Positions (Sign, Degree, House, Retrograde):**", "gemini_aspects_header": "**Significant Aspects (Planet1, Aspect, Planet2, Orb):**", "gemini_task_header": "**Task:**", "gemini_task_instruction_1": "Write a full analysis, divided into the following sections. Use these exact section names and formatting (e.g., `[SECTION: PlanetsInSignsStart]`):", "gemini_section_pis_start": "[SECTION: PlanetsInSignsStart]", "gemini_pis_instruction": "(Planets in Signs begin here. For each planet (Sun-Pluto), write a detailed analysis in its sign. For example: \"Sun in Aries: ...\")", "gemini_section_pis_end": "[SECTION: PlanetsInSignsEnd]", "gemini_section_pih_start": "[SECTION: PlanetsInHousesStart]", "gemini_pih_instruction": "(Planets in Houses begin here. For each planet (Sun-Pluto), write a detailed analysis in its house, if the house number is known. For example: \"Moon in 5th House: ...\")", "gemini_section_pih_end": "[SECTION: PlanetsInHousesEnd]", "gemini_section_aspects_start": "[SECTION: AspectsStart]", "gemini_aspects_instruction": "(Aspects begin here. For each listed aspect, write a detailed analysis. For example: \"Sun conjunct Jupiter: ...\")", "gemini_section_aspects_end": "[SECTION: AspectsEnd]", "gemini_final_instruction": "Please return the text for these three sections only, between the tags. Do not add an introduction or concluding remarks."
     },
    "ru": { "language_chosen": "Вы выбрали русский язык.", "welcome_new_user": "Сначала нам нужно составить вашу натальную карту...", "create_chart_button_text": "📜 Составить карту", "ask_name": "Пожалуйста, введите имя:", "name_thanks": "Спасибо, {name}.\nТеперь введите дату (ГГГГ/ММ/ДД):", "invalid_name": "Неверное имя.", "invalid_date_format": "Неверный формат даты. Используйте ГГГГ/ММ/ДД.", "invalid_year_range": "Неверный год. Используйте ГГГГ/ММ/ДД.", "ask_time": "Введите время (ЧЧ:ММ) или 'Время неизвестно'.", "time_unknown_button": "Время неизвестно (12:00)", "invalid_time_format": "Неверный формат времени. Используйте ЧЧ:ММ или 'Время неизвестно'.", "ask_country": "Введите страну рождения:", "invalid_country": "Неверная страна.", "ask_city": "Введите город рождения (в {country}):", "invalid_city": "Неверный город.", "data_collection_complete": "Сбор данных завершен. Генерация карты...", "cancel_button_text": "/cancel", "main_menu_text": "Выберите действие:", "view_chart_button": "📜 Посмотреть карту", "dream_button": "🌙 Толкование снов", "horoscope_button": "🔮 Гороскоп", "palmistry_button": "🖐️ Хиромантия", "coffee_button": "☕ Гадание на кофе", "delete_data_button": "🗑️ Удалить данные", "help_button": "❓ Помощь", "feature_coming_soon": "Функция '{feature_name}' скоро появится!", "data_saved": "Данные сохранены.", "data_save_error":"Ошибка сохранения.", "chart_ready_menu_prompt": "Ваша карта готова. Главное меню:", "welcome_existing_user_1": "Ваши сохраненные данные:", "welcome_existing_user_2": "Используйте 'Составить карту'.", "menu_mydata": "/mydata - Показать данные", "menu_deletedata": "/deletedata - Удалить данные", "start_createchart_no_data":"Используйте 'Составить карту'.", "chart_creation_prompt": "Для карты нужны данные. /cancel в любое время.", "saved_data_exists_1":"Карта уже есть для {name} ({day}/{month}/{year}).", "saved_data_exists_2":"Посмотреть или создать новую?", "use_saved_chart_button":"Посмотреть", "enter_new_data_button":"Создать новую", "cancel_creation_button":"Отмена", "using_saved_chart":"Ваша сохраненная карта:", "chart_generation_cancelled":"Создание отменено.", "invalid_choice":"Неверный выбор.", "my_data_header":"Ваши данные:\n", "my_data_name":"  <b>Имя:</b> {name}\n", "my_data_date":"  <b>Дата:</b> {day}/{month}/{year}\n", "my_data_time":"  <b>Время:</b> {hour}:{minute}\n", "my_data_city":"  <b>Город:</b> {city}\n", "my_data_country":"  <b>Страна:</b> {nation_or_text}\n", "not_specified":"Не указано", "no_data_found":"Нет данных. Используйте 'Составить карту'.", "data_deleted_success":"Данные удалены.", "data_delete_error":"Ошибка удаления.", "processing_kerykeion":"Обработка данных...", "geonames_warning_user":"Внимание: GEONAMES_USERNAME не установлен.", "kerykeion_city_error":"Ошибка: Город '{city}' не найден.", "kerykeion_general_error":"Ошибка расчета.", "aspect_calculation_error_user":"Внимание: Ошибка расчета аспектов.", "gemini_prompt_start":"Генерация интерпретаций...\n⏳ Это может занять 1-3 минуты.", "gemini_interpretation_failed":"Ошибка генерации.", "chart_error_generic":"Ошибка генерации карты.",
        "gemini_main_prompt_intro": "Вы опытный, профессиональный астролог, составляющий глубокий и подробный анализ натальной карты на {language} языке.",
        "gemini_main_prompt_instruction_1": "Следуйте запрошенной структуре и по каждому пункту напишите не менее 3-5 подробных предложений, объясняющих его значение для данного человека ({name}).",
        "gemini_main_prompt_instruction_2": "Используйте профессиональный, но в то же время теплый и понятный язык. Избегайте шаблонных фраз.",
        "gemini_main_prompt_instruction_3": "Будьте максимально точны и подробны, как в примере PDF.",
        "gemini_data_header": "**Данные рождения:**", "gemini_name": "Имя: {name}", "gemini_birth_date_time": "Дата рождения: {day}/{month}/{year}, {hour:02d} ч {minute:02d} мин", "gemini_birth_location": "Место рождения: {city}{location_nation_suffix}", "gemini_systems_used": "Используемые системы: Зодиак - Тропический, Дома - Плацидус", "gemini_planet_positions_header": "**Положения планет (Знак, Градус, Дом, Ретроградность):**", "gemini_aspects_header": "**Значимые аспекты (Планета1, Аспект, Планета2, Орбис):**", "gemini_task_header": "**Задание:**", "gemini_task_instruction_1": "Напишите полный анализ, разделенный на следующие секции. Используйте точно эти названия секций и форматирование (например, `[SECTION: PlanetsInSignsStart]`):", "gemini_section_pis_start": "[SECTION: PlanetsInSignsStart]", "gemini_pis_instruction": "(Здесь начинаются Планеты в Знаках. Для каждой планеты (Солнце-Плутон) напишите подробный анализ в ее знаке. Например: \"Солнце в Овне: ...\")", "gemini_section_pis_end": "[SECTION: PlanetsInSignsEnd]", "gemini_section_pih_start": "[SECTION: PlanetsInHousesStart]", "gemini_pih_instruction": "(Здесь начинаются Планеты в Домах. Для каждой планеты (Солнце-Плутон) напишите подробный анализ в ее доме, если номер дома известен. Например: \"Луна в 5-м Доме: ...\")", "gemini_section_pih_end": "[SECTION: PlanetsInHousesEnd]", "gemini_section_aspects_start": "[SECTION: AspectsStart]", "gemini_aspects_instruction": "(Здесь начинаются Аспекты. Для каждого перечисленного аспекта напишите подробный анализ. Например: \"Солнце соединение Юпитер: ...\")", "gemini_section_aspects_end": "[SECTION: AspectsEnd]", "gemini_final_instruction": "Пожалуйста, верните текст только для этих трех секций, между тегами. Не добавляйте вступления или заключительные слова."
    }
}
DEFAULT_LANGUAGE = "ka"

# --- ფუნქციების განსაზღვრებები ---
# (init_db, save_user_data, get_user_data, delete_user_data)
# (planet_emojis, aspect_translations, aspect_symbols)
# (get_gemini_interpretation, split_text)
# (generate_and_send_chart)
# (start_command, handle_language_choice, initiate_chart_creation_callback)
# (create_chart_start_conv, handle_saved_data_choice_conv)
# (handle_name_conv, handle_birth_date_conv, handle_birth_time_conv, handle_country_conv, handle_city_conv)
# (cancel_conv)
# (my_data_command, view_my_chart_command, delete_data_command, handle_other_menu_buttons)
# (get_main_menu_keyboard)
# --- ეს ფუნქციები უნდა იყოს main() ფუნქციის წინ ---

def get_text(key: str, lang_code: str | None = None, context: ContextTypes.DEFAULT_TYPE | None = None) -> str:
    """Gets translated text. Prioritizes lang_code, then context.user_data, then default."""
    final_lang_code = DEFAULT_LANGUAGE
    if lang_code:
        final_lang_code = lang_code
    elif context and 'lang_code' in context.user_data:
        final_lang_code = context.user_data['lang_code']

    # ვცდილობთ ვიპოვოთ ტექსტი არჩეულ ენაზე
    primary_translation_dict = translations.get(final_lang_code, {})
    text = primary_translation_dict.get(key)

    # თუ არ არის, ვცდილობთ ინგლისურს (როგორც fallback, ქართულის გარდა)
    if text is None and final_lang_code != "en":
        english_translation_dict = translations.get("en", {})
        text = english_translation_dict.get(key)
    
    # თუ არც ინგლისურია, ვცდილობთ ქართულს (როგორც საბოლოო fallback)
    if text is None and final_lang_code != "ka":
        georgian_translation_dict = translations.get("ka", {})
        text = georgian_translation_dict.get(key)
        
    return text if text is not None else f"TR_ERROR['{key}':'{final_lang_code}']"

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
        logger.info(f"Database {DB_FILE} initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")

def save_user_data(user_id: int, data: dict, chart_text: str | None = None):
    try:
        # დარწმუნდით, რომ ენა ყოველთვის ინახება
        lang_code_to_save = data.get('lang_code', DEFAULT_LANGUAGE)
        if 'context' in globals() and hasattr(context, 'user_data') and context.user_data.get('lang_code'):
             lang_code_to_save = context.user_data.get('lang_code')

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO user_birth_data
            (user_id, name, year, month, day, hour, minute, city, nation, language_code, full_chart_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            data.get('name'), data.get('year'), data.get('month'), data.get('day'),
            data.get('hour'), data.get('minute'), data.get('city'), data.get('nation'),
            lang_code_to_save, # შევინახოთ არჩეული ენა
            chart_text 
        ))
        conn.commit()
        conn.close()
        logger.info(f"Data (lang: {lang_code_to_save}) and chart text saved for user {user_id}")
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
        if row:
            logger.info(f"Data retrieved for user {user_id}: {dict(row)}")
            return dict(row)
        else:
            logger.info(f"No data found for user {user_id}")
            return None
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
        request_options = {"timeout": 180}
        response = await gemini_model.generate_content_async(
            prompt,
            generation_config={"response_mime_type": "text/plain"}, # მოვითხოვოთ სუფთა ტექსტი
            request_options=request_options
            )
        if not response.candidates:
            feedback = response.prompt_feedback if hasattr(response, 'prompt_feedback') else None
            block_reason = feedback.block_reason if hasattr(feedback, 'block_reason') else 'Unknown'
            safety_ratings = feedback.safety_ratings if hasattr(feedback, 'safety_ratings') else 'N/A'
            logger.warning(f"Gemini response blocked or empty. Prompt: '{prompt[:100]}...'. Reason: {block_reason}, Ratings: {safety_ratings}")
            return f"(Gemini-მ პასუხი დაბლოკა ან ცარიელია. მიზეზი: {block_reason})"
        if hasattr(response.candidates[0].content, 'parts') and response.candidates[0].content.parts:
             full_text = "".join(part.text for part in response.candidates[0].content.parts)
             return full_text.strip()
        else:
            logger.warning(f"Gemini response candidate did not contain valid parts. Prompt: '{prompt[:100]}...'. Response: {response}")
            return "(Gemini-მ სტრუქტურული პასუხი არ დააბრუნა)"
    except generation_types.StopCandidateException as e:
         logger.warning(f"Gemini generation stopped: {e}. Prompt: '{prompt[:100]}...'")
         return "(Gemini-მ პასუხის გენერაცია შეწყვიტა)"
    except Exception as e:
        logger.error(f"Gemini API error ({type(e).__name__}): {e}", exc_info=True)
        return f"(ინტერპრეტაციის გენერირებისას მოხდა შეცდომა: {type(e).__name__})"

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
                limit_bytes = limit - 10
                search_text_bytes = temp_text.encode('utf-8')[:limit_bytes]
                split_pos_byte = search_text_bytes.rfind(b'\n')
                if split_pos_byte == -1: split_pos_byte = search_text_bytes.rfind(b'. ')
                if split_pos_byte == -1 or split_pos_byte < limit_bytes // 2 :
                     split_pos_byte = search_text_bytes.rfind(b' ')
                     if split_pos_byte == -1: split_pos_byte = limit_bytes
                split_pos_char = len(search_text_bytes[:split_pos_byte].decode('utf-8', errors='ignore'))
                final_parts.append(temp_text[:split_pos_char])
                temp_text = temp_text[split_pos_char:].lstrip()
            if temp_text: final_parts.append(temp_text)
        elif part:
            final_parts.append(part)
            
    return [p for p in final_parts if p]

def get_main_menu_keyboard(lang_code: str):
    keyboard = [
        [KeyboardButton(get_text("main_menu_button_view_chart", lang_code)), KeyboardButton(get_text("main_menu_button_dream", lang_code))],
        [KeyboardButton(get_text("main_menu_button_horoscope", lang_code)), KeyboardButton(get_text("main_menu_button_palmistry", lang_code))],
        [KeyboardButton(get_text("main_menu_button_coffee", lang_code))],
        [KeyboardButton(get_text("main_menu_button_delete_data", lang_code)), KeyboardButton(get_text("main_menu_button_help", lang_code))],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def generate_and_send_chart(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE, is_new_data: bool = False, data_to_process: dict | None = None):
    lang_code = context.user_data.get('lang_code') # ვიღებთ ენას კონტექსტიდან
    if not lang_code: # თუ კონტექსტში არ არის, ვცდილობთ ბაზიდან
        user_db_data_for_lang = get_user_data(user_id)
        if user_db_data_for_lang and user_db_data_for_lang.get('language_code'):
            lang_code = user_db_data_for_lang['language_code']
        else:
            lang_code = DEFAULT_LANGUAGE # თუ ვერსად ვიპოვეთ
    context.user_data['lang_code'] = lang_code # დავრწმუნდეთ, რომ კონტექსტშია

    # ვიყენებთ data_to_process თუ მოწოდებულია (ახალი მონაცემების შეგროვებისას), სხვა შემთხვევაში ბაზიდან
    current_user_data = data_to_process if data_to_process else get_user_data(user_id)

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

    if not all([name, year, month, day, isinstance(hour, int), isinstance(minute, int), city]): # hour/minute should be int
         await context.bot.send_message(chat_id=chat_id, text="მონაცემები არასრულია რუკის შესადგენად.")
         return

    # თუ რუკა უკვე გენერირებულია და შენახული (და არა ახალი მონაცემების იძულებითი გენერაცია)
    if not is_new_data and current_user_data.get('full_chart_text'):
        logger.info(f"Displaying saved chart for user {user_id}")
        parts = split_text(current_user_data['full_chart_text'])
        for part in parts:
            await context.bot.send_message(chat_id=chat_id, text=part, parse_mode=ParseMode.HTML)
        await context.bot.send_message(chat_id=chat_id, text=get_text("main_menu_text", lang_code), reply_markup=get_main_menu_keyboard(lang_code))
        return

    logger.info(f"Generating Kerykeion data for: {name}, {day}/{month}/{year} {hour}:{minute}, {city}, {nation}")
    processing_message = await context.bot.send_message(chat_id=chat_id, text=get_text("processing_kerykeion", lang_code))

    try:
        if not GEONAMES_USERNAME:
            logger.warning("GEONAMES_USERNAME not set in .env. Kerykeion will use default, which can be unreliable.")
            await context.bot.send_message(chat_id=chat_id, text=get_text("geonames_warning_user", lang_code))

        try:
            logger.info(f"Calling AstrologicalSubject with: name='{name}', year={year}, month={month}, day={day}, hour={hour}, minute={minute}, city='{city}', nation='{nation}', geonames_username='{GEONAMES_USERNAME}'")
            subject_instance = await asyncio.to_thread(
                AstrologicalSubject, name, year, month, day, hour, minute, city, nation=nation, geonames_username=GEONAMES_USERNAME
            )
        except RuntimeError as e:
             logger.warning(f"asyncio.to_thread failed ({e}), calling Kerykeion directly.")
             subject_instance = AstrologicalSubject(name, year, month, day, hour, minute, city, nation=nation, geonames_username=GEONAMES_USERNAME)
        logger.info(f"Kerykeion data generated for {name}. Sun at {subject_instance.sun['position']:.2f} {subject_instance.sun['sign']}.")

        aspects_data_str_for_prompt = ""
        try:
            aspect_calculator = NatalAspects(
                subject_instance,
                aspects_list=MAJOR_ASPECTS_TYPES,
                planets_to_consider=ASPECT_PLANETS,
                orb_dictionary=ASPECT_ORBS
            )
            all_filtered_aspects = aspect_calculator.get_relevant_aspects()
            logger.info(f"Found {len(all_filtered_aspects)} major aspects based on configuration.")
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
                 aspects_data_str_for_prompt = "- მნიშვნელოვანი მაჟორული ასპექტები ვერ მოიძებნა მითითებული პარამეტრებით.\n"
        except Exception as aspect_err:
             logger.error(f"Error calculating aspects for {name}: {aspect_err}", exc_info=True)
             aspects_data_str_for_prompt = "- ასპექტების გამოთვლისას მოხდა შეცდომა.\n"
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
                     logger.error(f"Error getting data for {planet_name} (attribute not found)")
                     planets_data_str_for_prompt += f"- {planet_name}: მონაცემების წაკითხვის შეცდომა\n"
            except Exception as e:
                 logger.error(f"Error getting full data for {planet_name}: {e}")
                 planets_data_str_for_prompt += f"- {planet_name}: მონაცემების სრული წაკითხვის შეცდომა\n"
        
        # ენის კოდის განსაზღვრა Gemini-სთვის
        gemini_lang_name = "ქართულ" # Default
        if lang_code == "en": gemini_lang_name = "ინგლისურ"
        elif lang_code == "ru": gemini_lang_name = "რუსულ"

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
        logger.info(f"Sending large prompt to Gemini for user {chat_id}. Prompt length approx: {len(large_prompt)}")
        full_interpretation_text = await get_gemini_interpretation(large_prompt)
        logger.info(f"Received full interpretation from Gemini for user {chat_id}. Length: {len(full_interpretation_text)}")

        # შენახვა ბაზაში
        save_user_data(user_id, current_user_data, chart_text=full_interpretation_text)
        current_user_data['full_chart_text'] = full_interpretation_text # განვაახლოთ მიმდინარე user_data

        final_report_parts = []
        base_info_text = (
            f"✨ {name}-ს ნატალური რუკა ✨\n\n"
            f"<b>დაბადების მონაცემები:</b> {day}/{month}/{year}, {hour:02d}:{minute:02d}, {city}{f', {nation}' if nation else ''}\n"
            f"<b>{get_text('gemini_systems_used', lang_code)}</b>\n\n" # ვიყენებთ თარგმნილს
        )
        try: sun_info = subject_instance.sun; base_info_text += f"{planet_emojis.get('Sun')} <b>მზე:</b> {sun_info['sign']} (<code>{sun_info['position']:.2f}°</code>)\n"
        except: pass
        try: asc_info = subject_instance.ascendant; base_info_text += f"{planet_emojis.get('Ascendant')} <b>ასცედენტი:</b> {asc_info['sign']} (<code>{asc_info['position']:.2f}°</code>)\n"
        except: pass
        time_note_key = "time_note_12_00" # დაგვჭირდება თარგმანებში დამატება
        time_note = f"\n<i>{get_text(time_note_key, lang_code)}</i>" if hour == 12 and minute == 0 else ""
        base_info_text += time_note + "\n"
        final_report_parts.append(base_info_text)
        
        # სექციების ძებნა Gemini-ს პასუხში
        pis_text = re.search(r"\[SECTION:\s*PlanetsInSignsStart\](.*?)\[SECTION:\s*PlanetsInSignsEnd\]", full_interpretation_text, re.DOTALL | re.IGNORECASE)
        pih_text = re.search(r"\[SECTION:\s*PlanetsInHousesStart\](.*?)\[SECTION:\s*PlanetsInHousesEnd\]", full_interpretation_text, re.DOTALL | re.IGNORECASE)
        asp_text_match = re.search(r"\[SECTION:\s*AspectsStart\](.*?)\[SECTION:\s*AspectsEnd\]", full_interpretation_text, re.DOTALL | re.IGNORECASE)

        if pis_text and pis_text.group(1).strip():
            final_report_parts.append(f"\n--- 🪐 <b>{get_text('section_title_pis', lang_code)}</b> ---\n\n{pis_text.group(1).strip()}")
        else: logger.warning("Could not parse [SECTION: PlanetsInSigns] or it was empty.")

        if pih_text and pih_text.group(1).strip():
            final_report_parts.append(f"\n--- 🏠 <b>{get_text('section_title_pih', lang_code)}</b> ---\n\n{pih_text.group(1).strip()}")
        else: logger.warning("Could not parse [SECTION: PlanetsInHouses] or it was empty.")

        if asp_text_match and asp_text_match.group(1).strip():
            final_report_parts.append(f"\n--- ✨ <b>{get_text('section_title_aspects', lang_code)}</b> ---\n\n{asp_text_match.group(1).strip()}")
        else: logger.warning("Could not parse [SECTION: Aspects] or it was empty.")
        
        if len(final_report_parts) == 1:
            if full_interpretation_text.startswith("("): # Gemini error
                 final_report_parts.append(f"\n<b>ინტერპრეტაცია ვერ მოხერხდა:</b>\n{full_interpretation_text}")
            elif len(full_interpretation_text) > 10:
                 logger.warning("Could not parse sections, showing raw Gemini text.")
                 final_report_parts.append(f"\n<b>ინტერპრეტაცია (დაუმუშავებელი):</b>\n{full_interpretation_text}")

        full_response_text = "".join(final_report_parts).strip()
        if not full_response_text or full_response_text == base_info_text.strip():
            await processing_message.edit_text(text=get_text("gemini_interpretation_failed", lang_code))
            return ConversationHandler.END

        parts = split_text(full_response_text)
        logger.info(f"Sending response in {len(parts)} parts.")
        await processing_message.edit_text(text=parts[0], parse_mode=ParseMode.HTML)
        for part in parts[1:]:
            await context.bot.send_message(chat_id=chat_id, text=part, parse_mode=ParseMode.HTML)
        logger.info(f"Full detailed chart sent for {name}.")

    except KerykeionException as ke:
        logger.error(f"KerykeionException for {name}: {ke}", exc_info=False)
        await processing_message.edit_text(text=get_text("kerykeion_city_error", lang_code).format(city=city))
        return ConversationHandler.END # დავასრულოთ კონვერსაცია, თუ Kerykeion-მა ვერ იპოვა ქალაქი
    except ConnectionError as ce:
        logger.error(f"ConnectionError during chart generation for {name}: {ce}")
        await processing_message.edit_text(text=f"კავშირის შეცდომა მოხდა. სცადეთ მოგვიანებით.")
    except Exception as e:
        logger.error(f"An unexpected error occurred generating chart for {name}: {e}", exc_info=True)
        try:
            await processing_message.edit_text(text=get_text("chart_error_generic", lang_code) + f" ({type(e).__name__})")
        except Exception:
             await context.bot.send_message(chat_id=chat_id, text=get_text("chart_error_generic", lang_code))
    
    # მენიუს გამოტანა
    await context.bot.send_message(chat_id=chat_id, text=get_text("chart_ready_menu_prompt", lang_code), reply_markup=get_main_menu_keyboard(lang_code))
    return ConversationHandler.END


# --- ConversationHandler-ის მდგომარეობები ---
(LANG_CHOICE, SAVED_DATA_OR_NAME, NAME_CONV, BIRTH_DATE_CONV, BIRTH_TIME_CONV, COUNTRY_CONV, CITY_CONV) = range(7)

# --- ConversationHandler-ის ფუნქციები ---
# (start_command, handle_language_choice, initiate_chart_creation_callback, create_chart_start_conv, handle_saved_data_choice_conv, handle_name_conv, handle_birth_date_conv, handle_birth_time_conv, handle_country_conv, handle_city_conv, cancel_conv - უცვლელია)
# (my_data_command, view_my_chart_command, delete_data_command, handle_other_menu_buttons - უცვლელია)
# (აქ აღარ ჩავსვი ეს ფუნქციები, რადგან გრძელია და უცვლელი რჩება. გამოიყენეთ წინა ვერსიიდან.)
# --- მთავარი ფუნქცია ---
def main() -> None:
    """Start the bot in polling mode."""
    init_db() # ეს უნდა იყოს main ფუნქციის დასაწყისშივე

    if not TELEGRAM_BOT_TOKEN:
        logger.critical("Error: TELEGRAM_BOT_TOKEN environment variable not set. Bot cannot start.")
        return
    if not gemini_model:
         logger.warning("Gemini model not loaded (check API key and safety settings?). AI features might be less effective or disabled.")

    logger.info("Creating application...")
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
                CallbackQueryHandler(ask_for_name_direct, pattern='^initiate_chart_creation_direct$'), # ახალი callback მონაცემებისთვის
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

    # დამატებითი ფუნქციები ConversationHandler-ისთვის, რომლებიც წინა კოდში იყო
    async def ask_for_name_direct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
        await query.edit_message_text(text=get_text("chart_creation_prompt", lang_code)) # ან უბრალოდ წავშალოთ ეს მესიჯი
        await context.bot.send_message(chat_id=query.message.chat_id, text=get_text("ask_name", lang_code), reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True, one_time_keyboard=True))
        return NAME_CONV

    async def prompt_for_name_after_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        # ეს ფუნქცია შეიძლება დაგვჭირდეს, თუ LANG_CHOICE-ის შემდეგ პირდაპირ ტექსტი მოდის
        lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
        await update.message.reply_text(get_text("ask_name", lang_code), reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True, one_time_keyboard=True))
        return NAME_CONV


    # Handler-ების რეგისტრაცია
    application.add_handler(main_conv_handler)
    application.add_handler(CommandHandler("createchart", create_chart_start_conv)) # /createchart ცალკე
    application.add_handler(CommandHandler("mydata", my_data_command))
    application.add_handler(CommandHandler("deletedata", delete_data_command))

    # ReplyKeyboard-ის ღილაკები (თარგმნილი)
    main_menu_buttons_regex_parts = []
    for lang_code_iter in ["ka", "en", "ru"]:
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_view_chart", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_delete_data", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_dream", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_horoscope", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_palmistry", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_coffee", lang_code_iter)))
        main_menu_buttons_regex_parts.append(re.escape(get_text("main_menu_button_help", lang_code_iter)))
        # დავამატოთ "რუკის შედგენა" ღილაკიც, თუ მომხმარებელი მენიუდან აირჩევს
        main_menu_buttons_regex_parts.append(re.escape(get_text("create_chart_button_text", lang_code_iter)))


    # დავრწმუნდეთ, რომ ყველა უნიკალურია და შევქმნათ Regex
    unique_button_texts = set(main_menu_buttons_regex_parts)
    combined_regex = '^(' + '|'.join(unique_button_texts) + ')$'

    # ერთი MessageHandler ყველა მენიუს ღილაკისთვის (ტექსტის მიხედვით)
    async def general_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_message = update.message.text
        lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)

        if user_message == get_text("main_menu_button_view_chart", lang_code):
            await view_my_chart_command(update, context)
        elif user_message == get_text("main_menu_button_delete_data", lang_code):
            await delete_data_command(update, context)
        elif user_message == get_text("create_chart_button_text", lang_code): # მენიუდან "რუკის შედგენა"
             await create_chart_start_conv(update, context) # ვიწყებთ კონვერსაციას
        else: # სხვა ღილაკები
            await handle_other_menu_buttons(update, context)

    application.add_handler(MessageHandler(filters.Regex(combined_regex) & filters.TEXT & ~filters.COMMAND, general_menu_handler))

    logger.info("Handlers registered.")
    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    load_dotenv()
    main()