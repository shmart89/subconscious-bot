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
        "not_specified": "არ არის მითითებული",
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
    "en": { # ... (English translations - abbreviated for brevity) ...
        "language_chosen": "You have selected English.",
        "welcome_new_user": "First, we need to create your natal chart...",
        "create_chart_button_text": "📜 Create Chart",
        # ... add all other keys for English ...
        "gemini_main_prompt_intro": "You are an experienced, professional astrologer writing an in-depth and detailed natal chart analysis in {language}.",
        "gemini_final_instruction": "Please return the text for these three sections only, between the tags. Do not add an introduction or concluding remarks."
    },
    "ru": { # ... (Russian translations - abbreviated for brevity) ...
        "language_chosen": "Вы выбрали русский язык.",
        "welcome_new_user": "Прежде всего, нам нужно составить вашу натальную карту...",
        "create_chart_button_text": "📜 Составить карту",
        # ... add all other keys for Russian ...
        "gemini_main_prompt_intro": "Вы опытный, профессиональный астролог, составляющий глубокий и подробный анализ натальной карты на {language} языке.",
        "gemini_final_instruction": "Пожалуйста, верните текст только для этих трех секций, между тегами. Не добавляйте вступления или заключительные слова."
    }
}
DEFAULT_LANGUAGE = "ka"

def get_text(key: str, lang_code: str | None = None) -> str:
    """აბრუნებს ტექსტს მოთხოვნილი ენისთვის, ან ნაგულისხმევს თუ თარგმანი არ არის."""
    lang_to_use = lang_code or context.user_data.get('lang_code', DEFAULT_LANGUAGE) if 'context' in globals() and hasattr(context, 'user_data') else DEFAULT_LANGUAGE

    # ვცდილობთ ვიპოვოთ ტექსტი არჩეულ ენაზე
    primary_translation = translations.get(lang_to_use, {})
    text = primary_translation.get(key)

    # თუ არ არის, ვცდილობთ ინგლისურს (როგორც fallback, ქართულის გარდა)
    if text is None and lang_to_use != "en":
        english_translation = translations.get("en", {})
        text = english_translation.get(key)
    
    # თუ არც ინგლისურია, ვცდილობთ ქართულს (როგორც საბოლოო fallback)
    if text is None and lang_to_use != "ka":
        georgian_translation = translations.get("ka", {})
        text = georgian_translation.get(key)
        
    return text if text is not None else f"TR_ERROR: Missing translation for '{key}'"


# --- მონაცემთა ბაზის ფუნქციები ---
# (init_db, save_user_data, get_user_data, delete_user_data - განთავსებულია main() ფუნქციის წინ)
# --- პლანეტების და ასპექტების ემოჯები/თარგმანები ---
# (planet_emojis, aspect_translations, aspect_symbols - განთავსებულია main() ფუნქციის წინ)
# --- Gemini-სთან კომუნიკაციის ფუნქცია ---
# (get_gemini_interpretation - განთავსებულია main() ფუნქციის წინ)
# --- დამხმარე ფუნქცია ტექსტის ნაწილებად დასაყოფად ---
# (split_text - განთავსებულია main() ფუნქციის წინ)

# --- Handler ფუნქციები (განსაზღვრული main-ამდე) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks for language selection or proceeds if language is known."""
    user_id = update.effective_user.id
    user_data_db = get_user_data(user_id)

    if user_data_db and user_data_db.get('language_code'):
        lang_code = user_data_db['language_code']
        context.user_data['lang_code'] = lang_code
        logger.info(f"User {user_id} already has language set to: {lang_code}")
        if user_data_db.get('name'):
            welcome_text = get_text("welcome_existing_user_1", lang_code) + \
                           f" <b>{user_data_db.get('name')}</b> ({user_data_db.get('day')}/{user_data_db.get('month')}/{user_data_db.get('year')}).\n\n" + \
                           get_text("welcome_existing_user_2", lang_code) + "\n" + \
                           get_text("menu_mydata", lang_code) + "\n" + \
                           get_text("menu_deletedata", lang_code)
            await update.message.reply_html(welcome_text, reply_markup=get_main_menu_keyboard(lang_code))
            return ConversationHandler.END
        else:
            await update.message.reply_text(
                get_text("welcome_new_user", lang_code),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text("create_chart_button_text", lang_code), callback_data="initiate_chart_creation")]])
            )
            return LANG_CHOICE
    else:
        keyboard = [
            [InlineKeyboardButton("🇬🇪 ქართული", callback_data="lang_ka")],
            [InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")],
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("აირჩიეთ ენა / Choose language / Выберите язык:", reply_markup=reply_markup)
        return LANG_CHOICE

async def handle_language_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang_code = query.data.split('_')[1]
    context.user_data['lang_code'] = lang_code
    user_id = query.from_user.id

    # ენის შენახვა/განახლება ბაზაში
    user_db_data = get_user_data(user_id) or {} # ვიღებთ არსებულს ან ვქმნით ცარიელს
    user_db_data['user_id'] = user_id # დავრწმუნდეთ user_id არსებობს
    user_db_data['language_code'] = lang_code
    save_user_data(user_id, user_db_data, chart_text=user_db_data.get('full_chart_text')) # ვინახავთ ენას

    logger.info(f"User {user_id} selected language: {lang_code}")
    await query.edit_message_text(text=get_text("language_chosen", lang_code))

    if user_db_data and user_db_data.get('full_chart_text'):
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=get_text("main_menu_text", lang_code),
            reply_markup=get_main_menu_keyboard(lang_code)
        )
        return ConversationHandler.END
    else:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=get_text("welcome_new_user", lang_code),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(get_text("create_chart_button_text", lang_code), callback_data="initiate_chart_creation")]])
        )
        return LANG_CHOICE

async def initiate_chart_creation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    
    await query.edit_message_text(text=get_text("chart_creation_prompt", lang_code))
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=get_text("ask_name", lang_code),
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True, one_time_keyboard=True)
    )
    return NAME_CONV

(LANG_CHOICE, NAME_CONV, BIRTH_DATE_CONV, BIRTH_TIME_CONV, COUNTRY_CONV, CITY_CONV, SAVED_DATA_CHOICE_CONV) = range(7)

async def create_chart_start_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    lang_code = context.user_data.get('lang_code')
    if not lang_code:
        user_db_data = get_user_data(user_id)
        if user_db_data and user_db_data.get('language_code'):
            lang_code = user_db_data['language_code']
            context.user_data['lang_code'] = lang_code
        else: # თუ ენა საერთოდ არ არის არჩეული, ვაიძულებთ /start-ით არჩევას
            await update.message.reply_text("გთხოვთ, ჯერ გამოიყენოთ /start ბრძანება ენის ასარჩევად.")
            return ConversationHandler.END
    lang_code = lang_code or DEFAULT_LANGUAGE # დავრწმუნდეთ, რომ lang_code ყოველთვის არის

    logger.info(f"User {user_id} started chart creation process (lang: {lang_code}).")
    # context.user_data.clear() # არ ვასუფთავებთ, რომ lang_code შევინარჩუნოთ
    temp_user_data = {'lang_code': lang_code} # ვიწყებთ ახალ დროებით მონაცემებს

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
        return SAVED_DATA_CHOICE_CONV # გადავდივართ ახალ მდგომარეობაში
    else:
        await update.message.reply_text(
            get_text("chart_creation_prompt", lang_code) + "\n\n" +
            get_text("ask_name", lang_code),
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True, one_time_keyboard=True)
        )
        context.user_data = temp_user_data # ვიყენებთ ახალ დროებით მონაცემებს
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
        else:
            await query.edit_message_text("შენახული რუკა ვერ მოიძებნა. ვიწყებ ახალი მონაცემების შეგროვებას.")
            await query.message.reply_text(get_text("ask_name", lang_code), reply_markup=ReplyKeyboardMarkup([[KeyboardButton(get_text("cancel_button_text", lang_code))]], resize_keyboard=True, one_time_keyboard=True))
            return NAME_CONV
    elif choice == "enter_new_data_conv":
        await query.edit_message_text(get_text("enter_new_data_button", lang_code) + "...")
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
    context.user_data['nation'] = country_text # ვინახავთ სრულ სახელს, Kerykeion შეეცდება გამოიცნოს კოდი
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
    # შენახვა ბაზაში is_new_data=True-თი, რომ რუკა გენერირდეს და შეინახოს
    temp_data_for_saving = context.user_data.copy() # ვიღებთ ასლს
    await generate_and_send_chart(user_id, update.message.chat_id, context, is_new_data=True, data_to_process=temp_data_for_saving)
    context.user_data.clear() # ვასუფთავებთ დროებით მონაცემებს
    return ConversationHandler.END

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    logger.info(f"User {user.id} canceled the conversation.")
    context.user_data.clear()
    await update.message.reply_text(
        get_text("chart_generation_cancelled", lang_code),
        reply_markup=get_main_menu_keyboard(lang_code)
    )
    return ConversationHandler.END

# --- სხვა ბრძანებები ---
async def my_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
     user_id = update.effective_user.id
     lang_code = context.user_data.get('lang_code')
     if not lang_code:
         user_db_data = get_user_data(user_id)
         if user_db_data and user_db_data.get('language_code'):
             lang_code = user_db_data['language_code']
             context.user_data['lang_code'] = lang_code
         else: lang_code = DEFAULT_LANGUAGE

     user_data_db = get_user_data(user_id)
     if user_data_db:
         text = get_text("my_data_header", lang_code)
         text += get_text("my_data_name", lang_code).format(name=user_data_db.get('name', '-'))
         text += get_text("my_data_date", lang_code).format(day=user_data_db.get('day', '-'), month=user_data_db.get('month', '-'), year=user_data_db.get('year', '-'))
         text += get_text("my_data_time", lang_code).format(hour=user_data_db.get('hour', '-'), minute=user_data_db.get('minute', '-'))
         text += get_text("my_data_city", lang_code).format(city=user_data_db.get('city', '-'))
         text += get_text("my_data_country", lang_code).format(nation_or_text=user_data_db.get('nation') or get_text("not_specified", lang_code))
         await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_main_menu_keyboard(lang_code))
     else:
         await update.message.reply_text(get_text("no_data_found", lang_code), reply_markup=get_main_menu_keyboard(lang_code))

async def view_my_chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    lang_code = context.user_data.get('lang_code', DEFAULT_LANGUAGE)
    user_data_from_db = get_user_data(user_id)

    if user_data_from_db and user_data_from_db.get('full_chart_text'):
        await update.message.reply_text(get_text("using_saved_chart", lang_code), reply_markup=get_main_menu_keyboard(lang_code))
        parts = split_text(user_data_from_db['full_chart_text'])
        for part in parts:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=part, parse_mode=ParseMode.HTML)
    elif user_data_from_db: # მონაცემები არის, მაგრამ რუკა არა
        await update.message.reply_text("თქვენი მონაცემები შენახულია, მაგრამ რუკა ჯერ არ არის გენერირებული. ვიწყებ გენერაციას...", reply_markup=get_main_menu_keyboard(lang_code))
        await generate_and_send_chart(user_id, update.effective_chat.id, context, is_new_data=True)
    else:
        await update.message.reply_text("ჯერ რუკა უნდა შეადგინოთ. გთხოვთ, გამოიყენოთ მენიუს ღილაკი '📜 რუკის შედგენა'.", reply_markup=get_main_menu_keyboard(lang_code))


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
    feature_name_en = button_text
    for lc_key, trans_dict_val in translations.items():
        for key, value in trans_dict_val.items():
            if value == button_text:
                 feature_name_en = translations["en"].get(key, button_text)
                 break
        if feature_name_en != button_text: break
            
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

    # Conversation Handler
    chart_creation_conv = ConversationHandler(
        entry_points=[
            CommandHandler('createchart', create_chart_start_conv),
            # CallbackQueryHandler "initiate_chart_creation" უკვეLANG_CHOICE-შია,
            # ReplyKeyboard-დან მოსული ტექსტი "📜 რუკის შედგენა"
            MessageHandler(filters.Regex(f'^({re.escape(get_text("create_chart_button_text", "ka"))}|{re.escape(get_text("create_chart_button_text", "en"))}|{re.escape(get_text("create_chart_button_text", "ru"))})$'), create_chart_start_conv)
        ],
        states={
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
        allow_reentry=True
    )

    # Language selection conversation
    lang_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            LANG_CHOICE: [
                CallbackQueryHandler(handle_language_choice, pattern='^lang_(ka|en|ru)$'),
                CallbackQueryHandler(initiate_chart_creation_callback, pattern='^initiate_chart_creation$')
            ],
            # აქედან შეიძლება გადავიდეს chart_creation_conv-ის NAME_CONV მდგომარეობაში
            # ამისთვის initiate_chart_creation_callback აბრუნებს NAME_CONV-ს
            # და chart_creation_conv-ის entry_points-ს უნდა დავამატოთ შესაბამისი CallbackQueryHandler
        },
        fallbacks=[CommandHandler('cancel', cancel_conv)], # ან ცალკე cancel ფუნქცია ენის არჩევისთვის
        map_to_parent={ # ეს საშუალებას გვაძლევს, ენის არჩევის შემდეგ გადავიდეთ რუკის შედგენის კონვერსაციაში
            NAME_CONV: NAME_CONV, # თუ initiate_chart_creation_callback აბრუნებს NAME_CONV
            # ConversationHandler.END: ConversationHandler.END # თუ გაუქმდა
        }
    )
    # დავამატოთ chart_creation_conv, როგორც მშობელი კონვერსაცია
    # ან, უფრო მარტივად, დავიწყოთ ერთი კონვერსაციით /start-ზე, რომელიც პირველ რიგში ენას ითხოვს.

    # ---- გავამარტივოთ კონვერსაციის ლოგიკა: ერთი მთავარი კონვერსაცია ----
    # /start იწყებს ენის არჩევას (LANG_CHOICE)
    # ენის არჩევის შემდეგ, თუ რუკა არ აქვს, გადადის NAME_CONV-ში
    # /createchart პირდაპირ გადადის NAME_CONV-ში (თუ ენა არჩეულია) ან LANG_CHOICE-ში (თუ ენა არაა)
    
    # მდგომარეობები განახლებულია ზემოთ
    # ConversationHandler-ის მდგომარეობები
    (LANG_CHOICE, SAVED_DATA_OR_NAME, NAME, BIRTH_DATE, BIRTH_TIME, COUNTRY, CITY) = range(7)


    main_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            LANG_CHOICE: [
                CallbackQueryHandler(handle_language_choice_and_proceed, pattern='^lang_(ka|en|ru)$')
            ],
            SAVED_DATA_OR_NAME: [ # ეს მდგომარეობა გამოიძახება ენის არჩევის შემდეგ
                # თუ მომხმარებელს აქვს მონაცემები, აქ შევთავაზებთ არჩევანს, თუ არადა პირდაპირ სახელს ვკითხავთ
                CallbackQueryHandler(handle_saved_data_choice_conv_entry, pattern='^(use_saved_chart_conv|enter_new_data_conv|cancel_creation_conv)$'),
                # თუ პირდაპირ ღილაკს "რუკის შედგენა" დააჭირა LANG_CHOICE-დან
                CallbackQueryHandler(ask_for_name_direct, pattern='^initiate_chart_creation$'),
                # თუ რამე ტექსტი მოვიდა (არ უნდა ხდებოდეს, მაგრამ ყოველი შემთხვევისთვის)
                MessageHandler(filters.TEXT & ~filters.COMMAND, prompt_for_name_after_lang)
            ],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name_conv)],
            BIRTH_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_birth_date_conv)],
            BIRTH_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_birth_time_conv)],
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_country_conv)],
            CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_city_conv)],
        },
        fallbacks=[CommandHandler('cancel', cancel_conv)],
        allow_reentry=True
    )


    # Handler-ების რეგისტრაცია
    application.add_handler(main_conv_handler) # الرئيسي ConversationHandler
    application.add_handler(CommandHandler("createchart", create_chart_command_entry)) # /createchart ცალკე
    application.add_handler(CommandHandler("mydata", my_data_command))
    application.add_handler(CommandHandler("deletedata", delete_data_command))

    # ReplyKeyboard-ის ღილაკები
    application.add_handler(MessageHandler(filters.Regex(f'^({re.escape(get_text("main_menu_button_view_chart", "ka"))}|{re.escape(get_text("main_menu_button_view_chart", "en"))}|{re.escape(get_text("main_menu_button_view_chart", "ru"))})$'), view_my_chart_command))
    application.add_handler(MessageHandler(filters.Regex(f'^({re.escape(get_text("main_menu_button_delete_data", "ka"))}|{re.escape(get_text("main_menu_button_delete_data", "en"))}|{re.escape(get_text("main_menu_button_delete_data", "ru"))})$'), delete_data_command))

    other_buttons_texts_re = [
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
    other_buttons_regex_re = '^(' + '|'.join(re.escape(text) for text in set(other_buttons_texts_re)) + ')$'
    application.add_handler(MessageHandler(filters.Regex(other_buttons_regex_re), handle_other_menu_buttons))


    logger.info("Handlers registered.")
    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    load_dotenv()
    main()