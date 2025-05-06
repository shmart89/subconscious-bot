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

load_dotenv()

# --- კონფიგურაცია ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEONAMES_USERNAME = os.getenv("GEONAMES_USERNAME")
DB_FILE = "user_data.db"
TELEGRAM_MESSAGE_LIMIT = 4096
DEFAULT_UNKNOWN_TIME = dt_time(12, 0)

ASPECT_PLANETS = ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'Pluto', 'Ascendant', 'Midheaven']
MAJOR_ASPECTS_TYPES = ['conjunction', 'opposition', 'square', 'trine', 'sextile']
ASPECT_ORBS = {'Sun': 8, 'Moon': 8, 'Ascendant': 5, 'Midheaven': 5, 'default': 6}

# --- თარგმანები ---
translations = {
    "ka": {
        "language_chosen": "თქვენ აირჩიეთ ქართული ენა.",
        "welcome_new_user": "პირველ რიგში უნდა შევადგინოთ თქვენი ნატალური რუკა, რათა ჩვენი მიმოწერა უფრო პერსონალური და ზუსტი გახდეს.",
        "create_chart_button_text": "📜 რუკის შედგენა",
        "welcome_existing_user_1": "თქვენი შენახული მონაცემებია:",
        "welcome_existing_user_2": "გამოიყენეთ /createchart ახალი რუკის შესადგენად (შეგიძლიათ აირჩიოთ შენახული მონაცემების გამოყენება).",
        "menu_mydata": "/mydata - შენახული მონაცემების ჩვენება.",
        "menu_deletedata": "/deletedata - შენახული მონაცემების წაშლა.",
        "start_createchart_no_data": "ნატალური რუკის შესაქმნელად გამოიყენეთ /createchart ბრძანება.",
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
        "cancel_button_text": "/cancel",
        "saved_data_exists_1": "თქვენ უკვე შენახული გაქვთ რუკა ({name}, {day}/{month}/{year}...).",
        "saved_data_exists_2": "გსურთ მისი ნახვა თუ ახლის შედგენა?",
        "use_saved_chart_button": "კი, ვნახოთ შენახული რუკა",
        "enter_new_data_button": "არა, შევიყვანოთ ახალი მონაცემები",
        "cancel_creation_button": "გაუქმება",
        "using_saved_chart": "აი, თქვენი შენახული ნატალური რუკა:",
        "chart_generation_cancelled": "რუკის შექმნა გაუქმებულია.",
        "invalid_choice": "არასწორი არჩევანი. გთხოვთ, სცადოთ თავიდან /createchart.",
        "data_saved": "მონაცემები შენახულია.",
        "data_save_error": "მონაცემების შენახვისას მოხდა შეცდომა.",
        "chart_ready_menu_prompt": "თქვენი რუკა მზადაა. ეხლა კი შევუდგეთ თქვენს ყოველდღიურ მომსახურებას:",
        "my_data_header": "თქვენი შენახული მონაცემებია:\n",
        "my_data_name": "  <b>სახელი:</b> {name}\n",
        "my_data_date": "  <b>თარიღი:</b> {day}/{month}/{year}\n",
        "my_data_time": "  <b>დრო:</b> {hour}:{minute}\n",
        "my_data_city": "  <b>ქალაქი:</b> {city}\n",
        "my_data_country": "  <b>ქვეყანა:</b> {nation_or_text}\n",
        "no_data_found": "თქვენ არ გაქვთ შენახული მონაცემები. გამოიყენეთ /createchart დასამატებლად.",
        "data_deleted_success": "თქვენი შენახული მონაცემები და რუკა წარმატებით წაიშალა.",
        "data_delete_error": "მონაცემების წაშლისას მოხდა შეცდომა ან მონაცემები არ არსებობდა.",
        "processing_kerykeion": "მონაცემები მიღებულია, ვიწყებ ასტროლოგიური მონაცემების გამოთვლას...",
        "geonames_warning_user": "⚠️ გაფრთხილება: GeoNames მომხმარებლის სახელი არ არის დაყენებული. ქალაქის ძებნა შეიძლება ვერ მოხერხდეს ან არასწორი იყოს. რეკომენდებულია მისი დამატება.",
        "kerykeion_city_error": "შეცდომა: Kerykeion-მა ვერ იპოვა მონაცემები ქალაქისთვის '{city}'. გთხოვთ, შეამოწმოთ ქალაქის სახელი და სცადოთ თავიდან /createchart.",
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
        # Gemini Prompts
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
    "en": {
        "language_chosen": "You have selected English.",
        "welcome_new_user": "First, we need to create your natal chart to make our interaction more personal and accurate.",
        "create_chart_button_text": "📜 Create Chart",
        "welcome_existing_user_1": "Your saved data is:",
        "welcome_existing_user_2": "Use /createchart to generate a new chart (you can choose to use saved data).",
        "menu_mydata": "/mydata - Show saved data.",
        "menu_deletedata": "/deletedata - Delete saved data.",
        "start_createchart_no_data": "Use the /createchart command to generate your natal chart.",
        "chart_creation_prompt": "To create your natal chart, I need your birth details.\nYou can cancel at any time by sending /cancel.",
        "ask_name": "Please enter the name for whom the chart is being made:",
        "name_thanks": "Thank you, {name}.\nNow, please enter the full date of birth in the format: <b>YYYY/MM/DD</b> (e.g., <code>1989/11/29</code>):",
        "invalid_name": "Name must contain at least 2 characters. Please try again:",
        "invalid_date_format": "Incorrect date format. Please enter in <b>YYYY/MM/DD</b> format (e.g., <code>1989/11/29</code>):",
        "invalid_year_range": "Year must be between {start_year} and {end_year}. Please enter the date in <b>YYYY/MM/DD</b> format:",
        "ask_time": "Thank you. Now, please enter the time of birth in <b>HH:MM</b> format (e.g., <code>15:30</code>), or press the 'Time Unknown' button.",
        "time_unknown_button": "Time Unknown (12:00)",
        "invalid_time_format": "Incorrect time format. Please enter in <b>HH:MM</b> format (e.g., <code>15:30</code>) or press 'Time Unknown'.",
        "ask_country": "Enter the country of birth (e.g., Georgia, Germany):",
        "invalid_country": "Please enter a valid country name.",
        "ask_city": "Enter the city of birth (in {country}):",
        "invalid_city": "Please enter a valid city name.",
        "data_collection_complete": "Data collection complete. Starting chart generation...",
        "cancel_button_text": "/cancel",
        "saved_data_exists_1": "You already have a saved chart ({name}, {day}/{month}/{year}...).",
        "saved_data_exists_2": "Would you like to view it or create a new one?",
        "use_saved_chart_button": "Yes, view saved chart",
        "enter_new_data_button": "No, enter new data",
        "cancel_creation_button": "Cancel",
        "using_saved_chart": "Here is your saved natal chart:",
        "chart_generation_cancelled": "Chart creation cancelled.",
        "invalid_choice": "Invalid choice. Please try /createchart again.",
        "data_saved": "Data saved.",
        "data_save_error": "Error saving data.",
        "chart_ready_menu_prompt": "Your chart is ready. Now we can proceed with your daily services:",
        "my_data_header": "Your saved data:\n",
        "my_data_name": "  <b>Name:</b> {name}\n",
        "my_data_date": "  <b>Date:</b> {day}/{month}/{year}\n",
        "my_data_time": "  <b>Time:</b> {hour}:{minute}\n",
        "my_data_city": "  <b>City:</b> {city}\n",
        "my_data_country": "  <b>Country:</b> {nation_or_text}\n",
        "no_data_found": "You have no saved data. Use /createchart to add it.",
        "data_deleted_success": "Your saved data and chart have been successfully deleted.",
        "data_delete_error": "Error deleting data, or no data existed.",
        "processing_kerykeion": "Data received, starting astrological calculations...",
        "geonames_warning_user": "⚠️ Warning: GEONAMES_USERNAME is not set. City lookup might fail or be inaccurate. Adding it is recommended.",
        "kerykeion_city_error": "Error: Kerykeion could not find data for the city '{city}'. Please check the city name and try /createchart again.",
        "kerykeion_general_error": "An error occurred during astrological data calculation.",
        "aspect_calculation_error_user": "⚠️ Warning: An error occurred during aspect calculation.",
        "gemini_prompt_start": "Astrological data calculated. Starting generation of detailed interpretations with Gemini...\n⏳ This may take 1-3 minutes.",
        "gemini_interpretation_failed": "Failed to generate interpretations. Please try again later.",
        "chart_error_generic": "An unexpected error occurred during chart generation.",
        "main_menu_button_view_chart": "📜 View Chart",
        "main_menu_button_dream": "🌙 Dream Interpretation",
        "main_menu_button_horoscope": "🔮 Horoscope",
        "main_menu_button_palmistry": "🖐️ Palmistry",
        "main_menu_button_coffee": "☕ Coffee Reading",
        "main_menu_button_delete_data": "🗑️ Delete My Data",
        "main_menu_button_help": "❓ Help",
        "feature_coming_soon": "The '{feature_name}' feature will be added soon. Please choose another action:",
        # Gemini Prompts for English
        "gemini_main_prompt_intro": "You are an experienced, professional astrologer writing an in-depth and detailed natal chart analysis in {language}.",
        "gemini_main_prompt_instruction_1": "Follow the requested structure and for each point, write at least 3-5 detailed sentences explaining its significance for the given person ({name}).",
        "gemini_main_prompt_instruction_2": "Use professional, yet warm and understandable language, as if talking to a friend. Avoid clichéd phrases.",
        "gemini_main_prompt_instruction_3": "Be as accurate and detailed as possible, similar to the PDF sample.",
        "gemini_data_header": "**Birth Data:**",
        "gemini_name": "Name: {name}",
        "gemini_birth_date_time": "Date of Birth: {day}/{month}/{year}, {hour:02d}h {minute:02d}m",
        "gemini_birth_location": "Place of Birth: {city}{location_nation_suffix}",
        "gemini_systems_used": "Systems Used: Zodiac - Tropical, Houses - Placidus",
        "gemini_planet_positions_header": "**Planetary Positions (Sign, Degree, House, Retrograde):**",
        "gemini_aspects_header": "**Significant Aspects (Planet1, Aspect, Planet2, Orb):**",
        "gemini_task_header": "**Task:**",
        "gemini_task_instruction_1": "Write a full analysis, divided into the following sections. Use these exact section names and formatting (e.g., `[SECTION: PlanetsInSignsStart]`):",
        "gemini_section_pis_start": "[SECTION: PlanetsInSignsStart]",
        "gemini_pis_instruction": "(Planets in Signs begin here. For each planet (Sun-Pluto), write a detailed analysis in its sign. For example: \"Sun in Aries: ...\")",
        "gemini_section_pis_end": "[SECTION: PlanetsInSignsEnd]",
        "gemini_section_pih_start": "[SECTION: PlanetsInHousesStart]",
        "gemini_pih_instruction": "(Planets in Houses begin here. For each planet (Sun-Pluto), write a detailed analysis in its house, if the house number is known. For example: \"Moon in 5th House: ...\")",
        "gemini_section_pih_end": "[SECTION: PlanetsInHousesEnd]",
        "gemini_section_aspects_start": "[SECTION: AspectsStart]",
        "gemini_aspects_instruction": "(Aspects begin here. For each listed aspect, write a detailed analysis. For example: \"Sun conjunct Jupiter: ...\")",
        "gemini_section_aspects_end": "[SECTION: AspectsEnd]",
        "gemini_final_instruction": "Please return the text for these three sections only, between the tags. Do not add an introduction or concluding remarks."
    },
    "ru": { # რუსული თარგმანები (საჭიროებს შევსებას)
        "language_chosen": "Вы выбрали русский язык.",
        "welcome_new_user": "Прежде всего, нам нужно составить вашу натальную карту, чтобы наше общение было более персонализированным и точным.",
        "create_chart_button_text": "📜 Составить карту",
        "ask_name": "Пожалуйста, введите имя, для кого составляется карта:",
        "name_thanks": "Спасибо, {name}.\nТеперь, пожалуйста, введите полную дату рождения в формате: <b>ГГГГ/ММ/ДД</b> (например, <code>1989/11/29</code>):",
        # ... დანარჩენი რუსული თარგმანები ...
        "main_menu_text": "Выберите действие:",
        "view_chart_button": "📜 Посмотреть карту",
        "dream_button": "🌙 Толкование снов",
        "feature_coming_soon": "Функция '{feature_name}' скоро будет добавлена. Пожалуйста, выберите другое действие:",
        # Gemini Prompts for Russian
        "gemini_main_prompt_intro": "Вы опытный, профессиональный астролог, составляющий глубокий и подробный анализ натальной карты на {language} языке.",
        "gemini_main_prompt_instruction_1": "Следуйте запрошенной структуре и по каждому пункту напишите не менее 3-5 подробных предложений, объясняющих его значение для данного человека ({name}).",
        "gemini_main_prompt_instruction_2": "Используйте профессиональный, но в то же время теплый и понятный язык, как будто разговариваете с другом. Избегайте шаблонных фраз.",
        "gemini_main_prompt_instruction_3": "Будьте максимально точны и подробны, как в примере PDF.",
        "gemini_data_header": "**Данные рождения:**",
        "gemini_name": "Имя: {name}",
        "gemini_birth_date_time": "Дата рождения: {day}/{month}/{year}, {hour:02d} ч {minute:02d} мин",
        "gemini_birth_location": "Место рождения: {city}{location_nation_suffix}",
        "gemini_systems_used": "Используемые системы: Зодиак - Тропический, Дома - Плацидус",
        "gemini_planet_positions_header": "**Положения планет (Знак, Градус, Дом, Ретроградность):**",
        "gemini_aspects_header": "**Значимые аспекты (Планета1, Аспект, Планета2, Орбис):**",
        "gemini_task_header": "**Задание:**",
        "gemini_task_instruction_1": "Напишите полный анализ, разделенный на следующие секции. Используйте точно эти названия секций и форматирование (например, `[SECTION: PlanetsInSignsStart]`):",
        "gemini_section_pis_start": "[SECTION: PlanetsInSignsStart]",
        "gemini_pis_instruction": "(Здесь начинаются Планеты в Знаках. Для каждой планеты (Солнце-Плутон) напишите подробный анализ в ее знаке. Например: \"Солнце в Овне: ...\")",
        "gemini_section_pis_end": "[SECTION: PlanetsInSignsEnd]",
        "gemini_section_pih_start": "[SECTION: PlanetsInHousesStart]",
        "gemini_pih_instruction": "(Здесь начинаются Планеты в Домах. Для каждой планеты (Солнце-Плутон) напишите подробный анализ в ее доме, если номер дома известен. Например: \"Луна в 5-м Доме: ...\")",
        "gemini_section_pih_end": "[SECTION: PlanetsInHousesEnd]",
        "gemini_section_aspects_start": "[SECTION: AspectsStart]",
        "gemini_aspects_instruction": "(Здесь начинаются Аспекты. Для каждого перечисленного аспекта напишите подробный анализ. Например: \"Солнце соединение Юпитер: ...\")",
        "gemini_section_aspects_end": "[SECTION: AspectsEnd]",
        "gemini_final_instruction": "Пожалуйста, верните текст только для этих трех секций, между тегами. Не добавляйте вступления или заключительные слова."
    }
}
DEFAULT_LANGUAGE = "ka"

def get_text(key: str, lang_code: str | None = None) -> str:
    """აბრუნებს ტექსტს მოთხოვნილი ენისთვის, ან ნაგულისხმევს თუ თარგმანი არ არის."""
    if not lang_code:
        lang_code = DEFAULT_LANGUAGE
    # ვცდილობთ ვიპოვოთ ტექსტი არჩეულ ენაზე, თუ არ არის - ინგლისურზე, თუ არც ის - ქართულზე
    return translations.get(lang_code, translations[DEFAULT_LANGUAGE]).get(key, f"TR_ERROR: Missing translation for '{key}' in lang '{lang_code}'")

# --- ConversationHandler-ის მდგომარეობები ---
(LANG_CHOICE, NAME, BIRTH_DATE, BIRTH_TIME, COUNTRY, CITY, SAVED_DATA_CHOICE_LANG) = range(7)

# --- Handler ფუნქციები ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks for language selection or proceeds if language is known."""
    user_id = update.effective_user.id
    user_data_db = get_user_data(user_id) # ვიღებთ მონაცემებს ბაზიდან (ენის ჩათვლით)

    if user_data_db and user_data_db.get('language_code'):
        lang_code = user_data_db['language_code']
        context.user_data['lang_code'] = lang_code # ვინახავთ სესიისთვის
        logger.info(f"User {user_id} already has language set to: {lang_code}")
        # თუ მონაცემებიც აქვს, ვაჩვენებთ მთავარ მენიუს
        if user_data_db.get('name'): # ვამოწმებთ, თუ ძირითადი მონაცემებიც შენახულია
            welcome_text = get_text("welcome_existing_user_1", lang_code) + \
                           f" <b>{user_data_db.get('name')}</b> ({user_data_db.get('day')}/{user_data_db.get('month')}/{user_data_db.get('year')}).\n\n" + \
                           get_text("welcome_existing_user_2", lang_code) + "\n" + \
                           get_text("menu_mydata", lang_code) + "\n" + \
                           get_text("menu_deletedata", lang_code)
            await update.message.reply_html(welcome_text, reply_markup=get_main_menu_keyboard(lang_code))
            return ConversationHandler.END # ვასრულებთ საუბარს, რადგან მომხმარებელს უკვე აქვს ყველაფერი
        else: # ენა არჩეულია, მაგრამ რუკა არ არის
            await update.message.reply_text(
                get_text("welcome_new_user", lang_code),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text("create_chart_button_text", lang_code), callback_data="initiate_chart_creation")]])
            )
            return LANG_CHOICE # ვრჩებით ენის არჩევის მდგომარეობაში, რათა ღილაკმა იმუშაოს
    else:
        # ენის ასარჩევი ღილაკები
        keyboard = [
            [InlineKeyboardButton("🇬🇪 ქართული", callback_data="lang_ka")],
            [InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")],
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("აირჩიეთ ენა / Choose language / Выберите язык:", reply_markup=reply_markup)
        return LANG_CHOICE

async def handle_language_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles language selection from inline keyboard."""
    query = update.callback_query
    await query.answer()
    lang_code = query.data.split('_')[1] # "lang_ka" -> "ka"
    context.user_data['lang_code'] = lang_code
    user_id = query.from_user.id

    # ვინახავთ ენას ბაზაში (თუ მომხმარებელი უკვე არსებობს, ვანახლებთ, თუ არა - ვქმნით)
    # ამისთვის შეიძლება დაგვჭირდეს save_user_data-ს მცირედი მოდიფიკაცია ან ცალკე ფუნქცია
    # ამ ეტაპზე, დავუშვათ, რომ ენას ვინახავთ user_data-ში სესიისთვის, და ბაზაში შეინახება რუკის მონაცემებთან ერთად
    
    logger.info(f"User {user_id} selected language: {lang_code}")
    await query.edit_message_text(text=get_text("language_chosen", lang_code))

    # შევამოწმოთ, ხომ არ აქვს მომხმარებელს უკვე შენახული რუკა
    user_data_db = get_user_data(user_id)
    if user_data_db and user_data_db.get('full_chart_text'):
        # თუ რუკა არსებობს, ვაჩვენებთ მენიუს
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=get_text("main_menu_text", lang_code),
            reply_markup=get_main_menu_keyboard(lang_code)
        )
        return ConversationHandler.END
    else:
        # თუ რუკა არ არსებობს, ვიწყებთ მისი შექმნის პროცესს
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=get_text("welcome_new_user", lang_code),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text("create_chart_button_text", lang_code), callback_data="initiate_chart_creation")]])
        )
        return LANG_CHOICE # ვრჩებით ამ მდგომარეობაში, სანამ "რუკის შედგენა" ღილაკს არ დააჭერს

async def initiate_chart_creation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Called when 'Create Chart' button is pressed after language selection or if no data."""
    query = update.callback_query
    await query.answer()
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    
    await query.edit_message_text(text=get_text("chart_creation_prompt", lang_code))
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=get_text("ask_name", lang_code),
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True, one_time_keyboard=True)
    )
    return NAME

# Conversation states
# (NAME, BIRTH_DATE, BIRTH_TIME, COUNTRY, CITY, SAVED_DATA_CHOICE_CONV) = range(LANG_CHOICE + 1, LANG_CHOICE + 1 + 6)
# უფრო მარტივად
NAME_CONV, BIRTH_DATE_CONV, BIRTH_TIME_CONV, COUNTRY_CONV, CITY_CONV, SAVED_DATA_CHOICE_CONV = range(LANG_CHOICE + 1, LANG_CHOICE + 1 + 6)


async def create_chart_start_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation to create a natal chart (called by /createchart or menu button)."""
    user_id = update.effective_user.id
    lang_code = context.user_data.get('lang_code') # ენა უკვე არჩეული უნდა იყოს /start-ით
    if not lang_code: # თუ /start არ გამოუყენებია და პირდაპირ /createchart მოვიდა
        await update.message.reply_text("გთხოვთ, ჯერ გამოიყენოთ /start ბრძანება ენის ასარჩევად.")
        return ConversationHandler.END

    logger.info(f"User {user_id} started chart creation process (lang: {lang_code}).")
    context.user_data.clear() # ვასუფთავებთ წინა დროებით მონაცემებს (ენის გარდა)
    context.user_data['lang_code'] = lang_code # აღვადგენთ ენას

    saved_data = get_user_data(user_id)
    if saved_data and saved_data.get('full_chart_text'):
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(get_text("use_saved_chart_button", lang_code), callback_data="use_saved_chart_conv")],
            [InlineKeyboardButton(get_text("enter_new_data_button", lang_code), callback_data="enter_new_data_conv")],
            [InlineKeyboardButton(get_text("cancel_creation_button", lang_code), callback_data="cancel_creation_conv")],
        ])
        await update.message.reply_text(
            get_text("saved_data_exists_1", lang_code).format(name=saved_data.get('name','?'), day=saved_data.get('day','?'), month=saved_data.get('month','?'), year=saved_data.get('year','?')) + "\n" +
            get_text("saved_data_exists_2", lang_code),
            reply_markup=reply_markup
        )
        return SAVED_DATA_CHOICE_CONV
    else:
        await update.message.reply_text(
            get_text("chart_creation_prompt", lang_code) + "\n\n" +
            get_text("ask_name", lang_code),
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True, one_time_keyboard=True)
        )
        return NAME_CONV

async def handle_saved_data_choice_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    choice = query.data
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)

    if choice == "use_saved_chart_conv":
        saved_data = get_user_data(user_id)
        if saved_data and saved_data.get('full_chart_text'):
            await query.edit_message_text(get_text("using_saved_chart", lang_code))
            parts = split_text(saved_data['full_chart_text'])
            for part in parts:
                await context.bot.send_message(chat_id=query.message.chat_id, text=part, parse_mode=ParseMode.HTML)
            await context.bot.send_message(chat_id=query.message.chat_id, text=get_text("main_menu_text", lang_code), reply_markup=get_main_menu_keyboard(lang_code))
            return ConversationHandler.END
        else: # ეს არ უნდა მოხდეს ლოგიკურად
            await query.edit_message_text("შენახული რუკა ვერ მოიძებნა. ვიწყებ ახალი მონაცემების შეგროვებას.")
            await query.message.reply_text(get_text("ask_name", lang_code), reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True, one_time_keyboard=True))
            return NAME_CONV
    elif choice == "enter_new_data_conv":
        await query.edit_message_text(get_text("enter_new_data_button", lang_code) + "...") # "არა, შევიყვანოთ ახალი მონაცემები." -> "კარგი, შევიყვანოთ..."
        await query.message.reply_text(get_text("ask_name", lang_code), reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True, one_time_keyboard=True))
        return NAME_CONV
    elif choice == "cancel_creation_conv":
        await query.edit_message_text(get_text("chart_generation_cancelled", lang_code))
        await query.message.reply_text(get_text("main_menu_text", lang_code), reply_markup=get_main_menu_keyboard(lang_code))
        return ConversationHandler.END
    return ConversationHandler.END

async def handle_name_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    user_name_input = update.message.text
    if not user_name_input or len(user_name_input.strip()) < 2:
         await update.message.reply_text(get_text("invalid_name", lang_code))
         return NAME_CONV
    context.user_data['name'] = user_name_input.strip()
    logger.info(f"User {update.effective_user.id} entered name: {context.user_data['name']}")
    await update.message.reply_text(get_text("name_thanks", lang_code).format(name=context.user_data['name']), parse_mode=ParseMode.HTML)
    return BIRTH_DATE_CONV

async def handle_birth_date_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    date_text = update.message.text.strip()
    try:
        dt_obj = None
        possible_formats = ["%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"]
        for fmt in possible_formats:
            try:
                dt_obj = datetime.strptime(date_text, fmt)
                break
            except ValueError:
                continue
        if not dt_obj: raise ValueError("Date format not recognized")

        current_year = datetime.now().year
        if not (1900 <= dt_obj.year <= current_year):
            await update.message.reply_text(get_text("invalid_year_range", lang_code).format(start_year=1900, end_year=current_year), parse_mode=ParseMode.HTML)
            return BIRTH_DATE_CONV

        context.user_data['year'] = dt_obj.year
        context.user_data['month'] = dt_obj.month
        context.user_data['day'] = dt_obj.day
        logger.info(f"User {update.effective_user.id} entered date: Y:{dt_obj.year}, M:{dt_obj.month}, D:{dt_obj.day}")
        reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton(get_text("time_unknown_button", lang_code)), KeyboardButton(get_text("cancel_button_text", lang_code))]],
            resize_keyboard=True, one_time_keyboard=True
        )
        await update.message.reply_text(get_text("ask_time", lang_code), reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        return BIRTH_TIME_CONV
    except ValueError:
        await update.message.reply_text(get_text("invalid_date_format", lang_code), parse_mode=ParseMode.HTML)
        return BIRTH_DATE_CONV

async def handle_birth_time_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    time_text = update.message.text.strip()

    if time_text == get_text("time_unknown_button", lang_code):
        context.user_data['hour'] = DEFAULT_UNKNOWN_TIME.hour
        context.user_data['minute'] = DEFAULT_UNKNOWN_TIME.minute
        logger.info(f"User {update.effective_user.id} selected unknown time, using default: {DEFAULT_UNKNOWN_TIME.hour}:{DEFAULT_UNKNOWN_TIME.minute}")
    else:
        try:
            time_obj = datetime.strptime(time_text, "%H:%M").time()
            context.user_data['hour'] = time_obj.hour
            context.user_data['minute'] = time_obj.minute
            logger.info(f"User {update.effective_user.id} entered time: H:{time_obj.hour}, M:{time_obj.minute}")
        except ValueError:
            await update.message.reply_text(get_text("invalid_time_format", lang_code), parse_mode=ParseMode.HTML)
            return BIRTH_TIME_CONV
    await update.message.reply_text(get_text("ask_country", lang_code), reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True, one_time_keyboard=True))
    return COUNTRY_CONV

async def handle_country_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    country_text = update.message.text.strip()
    if not country_text or len(country_text) < 2 :
        await update.message.reply_text(get_text("invalid_country", lang_code))
        return COUNTRY_CONV
    context.user_data['nation_full_name'] = country_text # ვინახავთ სრულ სახელს
    context.user_data['nation'] = None # Kerykeion-ი შეეცდება გამოიცნოს, ან შეგვიძლია დავამატოთ კოდის ძებნა
    logger.info(f"User {update.effective_user.id} entered country: {country_text}")
    await update.message.reply_text(get_text("ask_city", lang_code).format(country=country_text), reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True, one_time_keyboard=True))
    return CITY_CONV

async def handle_city_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    city = update.message.text.strip()
    if not city or len(city) < 2:
         await update.message.reply_text(get_text("invalid_city", lang_code))
         return CITY_CONV
    context.user_data['city'] = city
    logger.info(f"User {user_id} entered city: {city}")

    await update.message.reply_text(get_text("data_collection_complete", lang_code), reply_markup=get_main_menu_keyboard(lang_code))

    save_user_data(user_id, context.user_data, chart_text=None) # ვინახავთ საბაზისო მონაცემებს, რუკა ჯერ არ არის
    # რუკის გენერაცია
    await generate_and_send_chart(user_id, update.message.chat_id, context, is_new_data=True)
    # context.user_data.clear() # ვასუფთავებთ მხოლოდ კონვერსაციის ბოლოს
    # logger.info(f"Conversation ended for user {user_id}.")
    return ConversationHandler.END

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    logger.info(f"User {user.id} canceled the conversation.")
    context.user_data.clear() # ვასუფთავებთ დროებით მონაცემებს
    await update.message.reply_text(
        get_text("chart_generation_cancelled", lang_code),
        reply_markup=get_main_menu_keyboard(lang_code)
    )
    return ConversationHandler.END

# --- სხვა ბრძანებები ---
async def my_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
     user_id = update.effective_user.id
     lang_code = context.user_data.get('lang_code')
     # ვცდილობთ ენის წამოღებას ბაზიდან, თუ სესიაში არ არის
     if not lang_code:
         user_db_data = get_user_data(user_id)
         if user_db_data and user_db_data.get('language_code'):
             lang_code = user_db_data['language_code']
             context.user_data['lang_code'] = lang_code # ვინახავთ სესიისთვისაც
         else:
             lang_code = DEFAULT_LANGUAGE

     user_data = get_user_data(user_id) # ვკითხულობთ მონაცემებს ბაზიდან
     if user_data:
         text = get_text("my_data_header", lang_code)
         text += get_text("my_data_name", lang_code).format(name=user_data.get('name', '-'))
         text += get_text("my_data_date", lang_code).format(day=user_data.get('day', '-'), month=user_data.get('month', '-'), year=user_data.get('year', '-'))
         text += get_text("my_data_time", lang_code).format(hour=user_data.get('hour', '-'), minute=user_data.get('minute', '-'))
         text += get_text("my_data_city", lang_code).format(city=user_data.get('city', '-'))
         text += get_text("my_data_country", lang_code).format(nation_or_text=user_data.get('nation') or get_text("not_specified", lang_code))
         await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard(lang_code))
     else:
         await update.message.reply_text(get_text("no_data_found", lang_code), reply_markup=get_main_menu_keyboard(lang_code))

async def view_my_chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE) # ენა სესიიდან ან დეფოლტი
    user_data_from_db = get_user_data(user_id)

    if user_data_from_db and user_data_from_db.get('full_chart_text'):
        await update.message.reply_text(get_text("using_saved_chart", lang_code), reply_markup=get_main_menu_keyboard(lang_code))
        parts = split_text(user_data_from_db['full_chart_text'])
        for part in parts:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=part, parse_mode=ParseMode.HTML)
    elif user_data_from_db:
        await update.message.reply_text("თქვენი მონაცემები შენახულია, მაგრამ რუკა ჯერ არ არის გენერირებული. ვიწყებ გენერაციას...", reply_markup=get_main_menu_keyboard(lang_code))
        await generate_and_send_chart(user_id, update.effective_chat.id, context, is_new_data=True)
    else:
        await update.message.reply_text("ჯერ რუკა უნდა შეადგინოთ. გთხოვთ, აირჩიეთ '📜 რუკის შედგენა' ან გამოიყენეთ /createchart.", reply_markup=get_main_menu_keyboard(lang_code))


async def delete_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    if delete_user_data(user_id):
        await update.message.reply_text(get_text("data_deleted_success", lang_code), reply_markup=get_main_menu_keyboard(lang_code))
    else:
        await update.message.reply_text(get_text("data_delete_error", lang_code), reply_markup=get_main_menu_keyboard(lang_code))

async def handle_other_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    button_text = update.message.text
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    # ვცდილობთ ვიპოვოთ ღილაკის ტექსტის შესაბამისი გასაღები, რომ ავიღოთ ინგლისური სახელი
    feature_name_en = button_text # Default
    for lc, trans_dict in translations.items():
        for key, value in trans_dict.items():
            if value == button_text:
                 feature_name_en = translations["en"].get(key, button_text) # ვიღებთ ინგლისურს
                 break
        if feature_name_en != button_text:
            break
            
    await update.message.reply_text(
        get_text("feature_coming_soon", lang_code).format(feature_name=feature_name_en),
        reply_markup=get_main_menu_keyboard(lang_code)
    )

# --- მთავარი ფუნქცია ---
def main() -> None:
    """Start the bot in polling mode."""
    init_db()
    if not TELEGRAM_BOT_TOKEN:
        logger.critical("Error: TELEGRAM_BOT_TOKEN environment variable not set. Bot cannot start.")
        return
    if not gemini_model:
         logger.warning("Gemini model not loaded (check API key and safety settings?). AI features will be disabled in responses.")

    logger.info("Creating application...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Conversation Handler for language selection AND chart creation
    # LANG_CHOICE will be the first state for new users or if /start is called
    # Chart creation flow (NAME_CONV, etc.) will follow after language is set
    # or if user directly requests chart and language is already known.

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start_command), # /start ბრძანება იწყებს ენის არჩევის პროცესს
            CommandHandler('createchart', create_chart_start_conv), # /createchart პირდაპირ იწყებს რუკის შედგენას (თუ ენა ცნობილია)
            # CallbackQueryHandler-ი "რუკის შედგენა" ღილაკისთვის /start-ის შემდეგ
            CallbackQueryHandler(initiate_chart_creation_callback, pattern='^initiate_chart_creation$'),
            MessageHandler(filters.Regex(f'^{re.escape(get_text("create_chart_button_text", "ka"))}$|^{re.escape(get_text("create_chart_button_text", "en"))}$|^{re.escape(get_text("create_chart_button_text", "ru"))}$'), create_chart_start_conv)
        ],
        states={
            LANG_CHOICE: [
                CallbackQueryHandler(handle_language_choice, pattern='^lang_(ka|en|ru)$'),
                # ესეც საჭიროა, თუ /start-ის მერე ღილაკს დააჭერენ
                CallbackQueryHandler(initiate_chart_creation_callback, pattern='^initiate_chart_creation$')
            ],
            SAVED_DATA_CHOICE_CONV: [
                 CallbackQueryHandler(handle_saved_data_choice_conv, pattern='^(use_saved_chart_conv|enter_new_data_conv|cancel_creation_conv)$')
            ],
            NAME_CONV: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name_conv)],
            BIRTH_DATE_CONV: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_birth_date_conv)],
            BIRTH_TIME_CONV: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_birth_time_conv)],
            COUNTRY_CONV: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_country_conv)],
            CITY_CONV: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_city_conv)],
        },
        fallbacks=[CommandHandler('cancel', cancel_conv)],
        # persistent=True, name="main_conversation" # მოგვიანებით შეგვიძლია დავამატოთ
        allow_reentry=True # მნიშვნელოვანია, რომ /start და /createchart ხელახლა მუშაობდეს
    )

    application.add_handler(conv_handler)
    # Commands outside conversation (start is an entry point now)
    application.add_handler(CommandHandler("mydata", my_data_command))
    application.add_handler(CommandHandler("deletedata", delete_data_command))

    # Handlers for main menu buttons (using Regex to match text)
    application.add_handler(MessageHandler(filters.Regex(f'^{re.escape(get_text("main_menu_button_view_chart", "ka"))}$|^{re.escape(get_text("main_menu_button_view_chart", "en"))}$|^{re.escape(get_text("main_menu_button_view_chart", "ru"))}$'), view_my_chart_command))
    # დანარჩენი მენიუს ღილაკები
    other_buttons_texts = [
        get_text("main_menu_button_dream", lang) for lang in ["ka", "en", "ru"]
    ] + [
        get_text("main_menu_button_horoscope", lang) for lang in ["ka", "en", "ru"]
    ] + [
        get_text("main_menu_button_palmistry", lang) for lang in ["ka", "en", "ru"]
    ] + [
        get_text("main_menu_button_coffee", lang) for lang in ["ka", "en", "ru"]
    ] + [
        get_text("main_menu_button_help", lang) for lang in ["ka", "en", "ru"]
    ]
    # გავაერთიანოთ Regex OR-ით
    other_buttons_regex = '^(' + '|'.join(re.escape(text) for text in set(other_buttons_texts)) + ')$'
    application.add_handler(MessageHandler(filters.Regex(other_buttons_regex), handle_other_menu_buttons))


    logger.info("Handlers registered.")
    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    load_dotenv()
    main()