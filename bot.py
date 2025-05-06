# -*- coding: utf-8 -*-
import os
import json
import logging
import sqlite3 # <-- მონაცემთა ბაზისთვის
from datetime import datetime
from pathlib import Path # <-- ფაილის გზისთვის
import asyncio # დაგვჭირდება Gemini-ს ასინქრონული გამოძახებისთვის

# --- Gemini AI Setup ---
import google.generativeai as genai
# -------------------------

from dotenv import load_dotenv
# განახლებული იმპორტები telegram.ext-დან
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackQueryHandler # <-- დაემატა იმპორტი
)

# შესწორებული Kerykeion იმპორტი
from kerykeion import AstrologicalSubject

# .env ფაილიდან გარემოს ცვლადების ჩატვირთვა
load_dotenv()

# --- კონფიგურაცია ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEONAMES_USERNAME = os.getenv("GEONAMES_USERNAME")
DB_FILE = "user_data.db"

# --- Gemini კონფიგურაცია ---
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    logging.warning("GEMINI_API_KEY not found in environment variables. AI features will be disabled.")

# ლოგირების ჩართვა
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("kerykeion").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- მონაცემთა ბაზის ფუნქციები ---
# (init_db, save_user_data, get_user_data, delete_user_data - უცვლელია)
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
                nation TEXT
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"Database {DB_FILE} initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")

def save_user_data(user_id: int, data: dict):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO user_birth_data
            (user_id, name, year, month, day, hour, minute, city, nation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            data.get('name'), data.get('year'), data.get('month'), data.get('day'),
            data.get('hour'), data.get('minute'), data.get('city'), data.get('nation')
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
        if row:
            logger.info(f"Data retrieved for user {user_id}")
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

# --- პლანეტების ემოჯები ---
planet_emojis = {
    "Sun": "☀️", "Moon": "🌙", "Mercury": "☿️", "Venus": "♀️", "Mars": "♂️",
    "Jupiter": "♃", "Saturn": "♄", "Uranus": "♅", "Neptune": "♆", "Pluto": "♇",
}

# --- Gemini-სთან კომუნიკაციის ფუნქცია ---
async def get_gemini_interpretation(prompt: str) -> str:
    """Calls Gemini API asynchronously to get interpretation."""
    if not GEMINI_API_KEY:
        return "(Gemini API კლუჩი არ არის კონფიგურირებული)"
    try:
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = await model.generate_content_async(prompt)
        # დავამატოთ შემოწმება response-ის ტიპზე და შიგთავსზე
        if response and hasattr(response, 'text'):
            return response.text.strip()
        else:
             # დავლოგოთ სრული პასუხი, თუ ტექსტი არ აქვს
             logger.warning(f"Gemini response did not contain text. Response: {response}")
             return "(Gemini-მ პასუხი არ დააბრუნა ან დაბლოკა)"
    except Exception as e:
        logger.error(f"Gemini API error: {e}", exc_info=True)
        return "(ინტერპრეტაციის გენერირებისას მოხდა შეცდომა)"

# --- რუკის გენერირების და გაგზავნის ფუნქცია ---
async def generate_and_send_chart(user_data: dict, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Generates natal chart using Kerykeion and gets interpretations from Gemini."""
    name = user_data.get('name', 'User')
    year = user_data.get('year')
    month = user_data.get('month')
    day = user_data.get('day')
    hour = user_data.get('hour')
    minute = user_data.get('minute')
    city = user_data.get('city')
    nation = user_data.get('nation')

    if not all([name, year, month, day, hour, minute, city]):
         await context.bot.send_message(chat_id=chat_id, text="მონაცემები არასრულია რუკის შესადგენად.")
         return

    logger.info(f"Generating Kerykeion data for: {name}, {day}/{month}/{year} {hour}:{minute}, {city}, {nation}")
    processing_message = await context.bot.send_message(chat_id=chat_id, text="მონაცემები მიღებულია, ვიწყებ ასტროლოგიური მონაცემების გამოთვლას...")

    try:
        if not GEONAMES_USERNAME:
            logger.warning("GEONAMES_USERNAME not set. Kerykeion might have issues with city lookup.")

        # Kerykeion calculation (still potentially blocking)
        subject_instance = AstrologicalSubject(name, year, month, day, hour, minute, city, nation=nation) # Removed kerykeion_username arg
        logger.info(f"Kerykeion data generated successfully for {name}.")

        base_info_text = (
            f"✨ {name}-ს ნატალური რუკა ✨\n\n"
            f"<b>დაბადების მონაცემები:</b> {day}/{month}/{year}, {hour:02d}:{minute:02d}, {city}{f', {nation}' if nation else ''}\n\n"
        )
        try:
            sun_info = subject_instance.sun
            sun_sign = sun_info['sign']
            sun_position = f"{sun_info['position']:.2f}°"
            base_info_text += f"☀️ <b>მზე:</b> {sun_sign} (<code>{sun_position}</code>)\n"
        except Exception: base_info_text += "☀️ <b>მზე:</b> (შეცდომა)\n"

        try:
            ascendant_info = subject_instance.first_house
            ascendant_sign = ascendant_info['sign']
            ascendant_position = f"{ascendant_info['position']:.2f}°"
            base_info_text += f"⬆️ <b>ასცედენტი:</b> {ascendant_sign} (<code>{ascendant_position}</code>)\n"
        except Exception as asc_err:
             logger.warning(f"Could not calculate Ascendant for {name}: {asc_err}")
             base_info_text += "⬆️ <b>ასცედენტი:</b> (ვერ გამოითვალა - შეამოწმეთ ქალაქი/დრო)\n"

        time_note = ""
        if hour == 12 and minute == 0:
             time_note = "\n<i>(შენიშვნა: დრო მითითებულია 12:00. ასცედენტი და სახლები შეიძლება არ იყოს ზუსტი.)</i>"
        base_info_text += time_note + "\n"

        await processing_message.edit_text(text=base_info_text + "\n⏳ ვიწყებ ინტერპრეტაციების გენერირებას Gemini-სთან...", parse_mode='HTML')

        interpretations_text = "\n--- 🪐 <b>პლანეტები ნიშნებში</b> ---\n"
        main_planets_for_interpretation = ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn']
        interpretation_tasks = []
        planet_data_for_formatting = []

        for planet_name in main_planets_for_interpretation:
            try:
                planet_obj = getattr(subject_instance, planet_name.lower())
                sign = planet_obj['sign']
                pos = planet_obj['position']
                planet_data_for_formatting.append({
                    "name": planet_name, "sign": sign, "pos": pos
                })
                prompt = (f"შენ ხარ გამოცდილი ასტროლოგი. დაწერე ძალიან მოკლე (1-2 წინადადება) და ზოგადი ასტროლოგიური ინტერპრეტაცია "
                          f"ქართულად იმ ადამიანისთვის, ვისაც პლანეტა {planet_name} ყავს {sign} ნიშანში. "
                          f"აღწერე ამ მდებარეობის მთავარი არსი ან გავლენა პიროვნებაზე. მოერიდე დაზეპირებულ ფრაზებს, იყავი ლაკონური.")
                interpretation_tasks.append(get_gemini_interpretation(prompt))
                logger.info(f"Created Gemini task for {planet_name} in {sign}")

            except Exception as planet_err:
                logger.error(f"Error getting Kerykeion data for planet {planet_name}: {planet_err}")
                planet_data_for_formatting.append({
                     "name": planet_name, "sign": "???", "pos": 0.0, "error": True
                })
                interpretation_tasks.append(asyncio.sleep(0, result="(პლანეტის მონაცემები ვერ მოიძებნა)"))

        logger.info(f"Waiting for {len(interpretation_tasks)} Gemini interpretations...")
        all_interpretations = await asyncio.gather(*interpretation_tasks)
        logger.info("All Gemini interpretations received.")

        for i, data in enumerate(planet_data_for_formatting):
             planet_name = data["name"]
             sign = data["sign"]
             pos = data["pos"]
             interpretation = all_interpretations[i]
             emoji = planet_emojis.get(planet_name, "🪐")

             interpretations_text += f"\n{emoji} <b>{planet_name} {sign}-ში</b> (<code>{pos:.2f}°</code>)\n<i>{interpretation}</i>\n"

        final_response_text = base_info_text + interpretations_text
        # TODO: Add Planets in Houses and Aspects interpretations similarly

        if len(final_response_text) > 4090:
             final_response_text = final_response_text[:4090] + "..."
             logger.warning("Response text too long, truncated.")

        await processing_message.edit_text(text=final_response_text, parse_mode='HTML') # HTML ფორმატირება
        logger.info(f"Final chart with interpretations sent for {name}.")

    except ConnectionError as ce:
        logger.error(f"Kerykeion ConnectionError for {name}: {ce}")
        await processing_message.edit_text(text=f"Kerykeion კავშირის შეცდომა (სავარაუდოდ GeoNames): {ce}. შეამოწმეთ ინტერნეტ კავშირი ან სცადეთ მოგვიანებით.")
    except Exception as e:
        logger.error(f"An unexpected error occurred generating chart for {name}: {e}", exc_info=True)
        try:
            await processing_message.edit_text(text=f"მოულოდნელი შეცდომა მოხდა რუკის გენერაციისას.")
        except Exception:
             await context.bot.send_message(chat_id=chat_id, text="მოულოდნელი შეცდომა მოხდა რუკის გენერაციისას.")

# --- Handler ფუნქციები (განსაზღვრული main-ამდე) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    user = update.effective_user
    user_data = get_user_data(user.id)
    start_text = rf"გამარჯობა {user.mention_html()}! მე ვარ Subconscious ბოტი."
    if user_data:
         start_text += f"\n\nთქვენი შენახული მონაცემებია: {user_data.get('name')}, {user_data.get('day')}/{user_data.get('month')}/{user_data.get('year')}."
         start_text += "\nგამოიყენეთ /createchart ახალი რუკის შესადგენად (შეგიძლიათ აირჩიოთ შენახული მონაცემების გამოყენება)."
         start_text += "\n/mydata - შენახული მონაცემების ჩვენება."
         start_text += "\n/deletedata - შენახული მონაცემების წაშლა."
    else:
        start_text += "\n\nნატალური რუკის შესაქმნელად გამოიყენეთ /createchart ბრძანება."

    await update.message.reply_html(start_text)

# Conversation states
(NAME, YEAR, MONTH, DAY, HOUR, MINUTE, CITY, NATION, SAVED_DATA_CHOICE) = range(9)

async def create_chart_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation or asks about using saved data."""
    user_id = update.effective_user.id
    logger.info(f"User {user_id} started chart creation process with /createchart.")
    context.user_data.clear()

    saved_data = get_user_data(user_id)
    if saved_data:
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("კი, გამოვიყენოთ შენახული", callback_data="use_saved_data")],
            [InlineKeyboardButton("არა, შევიყვანოთ ახალი", callback_data="enter_new_data")],
            [InlineKeyboardButton("გაუქმება", callback_data="cancel_creation")],
        ])
        await update.message.reply_text(
            f"თქვენ უკვე შენახული გაქვთ მონაცემები ({saved_data.get('name', '?')}, {saved_data.get('day','?')}/{saved_data.get('month','?')}/{saved_data.get('year', '?')}...). "
            "გსურთ ამ მონაცემებით რუკის შედგენა?",
            reply_markup=reply_markup
        )
        return SAVED_DATA_CHOICE
    else:
        await update.message.reply_text(
            "ნატალური რუკის შესაქმნელად, მჭირდება თქვენი მონაცემები.\n"
            "შეგიძლიათ ნებისმიერ დროს შეწყვიტოთ პროცესი /cancel ბრძანებით.\n\n"
            "გთხოვთ, შეიყვანოთ სახელი, ვისთვისაც ვადგენთ რუკას:"
        )
        return NAME

async def handle_saved_data_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's choice regarding saved data via callback query."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    choice = query.data

    if choice == "use_saved_data":
        saved_data = get_user_data(user_id)
        if saved_data:
            await query.edit_message_text("გამოვიყენებ შენახულ მონაცემებს რუკის შესადგენად.")
            await generate_and_send_chart(saved_data, query.message.chat_id, context)
            context.user_data.clear()
            return ConversationHandler.END
        else:
            await query.edit_message_text("შენახული მონაცემები ვერ მოიძებნა. ვიწყებ ახლის შეგროვებას.")
            await query.message.reply_text("გთხოვთ, შეიყვანოთ სახელი, ვისთვისაც ვადგენთ რუკას:")
            return NAME
    elif choice == "enter_new_data":
        await query.edit_message_text("კარგი, შევიყვანოთ ახალი მონაცემები.")
        await query.message.reply_text("გთხოვთ, შეიყვანოთ სახელი, ვისთვისაც ვადგენთ რუკას:")
        return NAME
    elif choice == "cancel_creation":
        await query.edit_message_text("რუკის შექმნა გაუქმებულია.")
        context.user_data.clear()
        return ConversationHandler.END
    else:
        await query.edit_message_text("არასწორი არჩევანი. გთხოვთ, სცადოთ თავიდან /createchart.")
        context.user_data.clear()
        return ConversationHandler.END

async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_name_input = update.message.text
    if not user_name_input or len(user_name_input) < 2:
         await update.message.reply_text("გთხოვთ, შეიყვანოთ კორექტული სახელი (მინ. 2 სიმბოლო).")
         return NAME
    context.user_data['name'] = user_name_input
    logger.info(f"User {update.effective_user.id} entered name: {user_name_input}")
    await update.message.reply_text(f"გმადლობთ, {user_name_input}. ახლა გთხოვთ, შეიყვანოთ დაბადების წელი (მაგ., 1990):")
    return YEAR

async def handle_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        year = int(update.message.text)
        current_year = datetime.now().year
        if 1900 <= year <= current_year:
            context.user_data['year'] = year
            logger.info(f"User {update.effective_user.id} entered year: {year}")
            await update.message.reply_text("შეიყვანეთ დაბადების თვე (რიცხვი 1-დან 12-მდე):")
            return MONTH
        else:
            await update.message.reply_text(f"გთხოვთ, შეიყვანოთ კორექტული წელი ({1900}-{current_year}).")
            return YEAR
    except ValueError:
        await update.message.reply_text("გთხოვთ, შეიყვანოთ წელი რიცხვებით (მაგ., 1990).")
        return YEAR

async def handle_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        month = int(update.message.text)
        if 1 <= month <= 12:
            context.user_data['month'] = month
            logger.info(f"User {update.effective_user.id} entered month: {month}")
            await update.message.reply_text("შეიყვანეთ დაბადების რიცხვი:")
            return DAY
        else:
            await update.message.reply_text("გთხოვთ, შეიყვანოთ თვე რიცხვით 1-დან 12-მდე.")
            return MONTH
    except ValueError:
        await update.message.reply_text("გთხოვთ, შეიყვანოთ თვე რიცხვით (1-12).")
        return MONTH

async def handle_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        day = int(update.message.text)
        if 1 <= day <= 31:
            context.user_data['day'] = day
            logger.info(f"User {update.effective_user.id} entered day: {day}")
            await update.message.reply_text("შეიყვანეთ დაბადების საათი (0-დან 23-მდე, თუ არ იცით, შეიყვანეთ 12):")
            return HOUR
        else:
            await update.message.reply_text("გთხოვთ, შეიყვანოთ რიცხვი 1-დან 31-მდე.")
            return DAY
    except ValueError:
        await update.message.reply_text("გთხოვთ, შეიყვანოთ რიცხვი.")
        return DAY

async def handle_hour(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        hour = int(update.message.text)
        if 0 <= hour <= 23:
            context.user_data['hour'] = hour
            logger.info(f"User {update.effective_user.id} entered hour: {hour}")
            await update.message.reply_text("შეიყვანეთ დაბადების წუთი (0-დან 59-მდე):")
            return MINUTE
        else:
            await update.message.reply_text("გთხოვთ, შეიყვანოთ საათი 0-დან 23-მდე.")
            return HOUR
    except ValueError:
        await update.message.reply_text("გთხოვთ, შეიყვანოთ საათი რიცხვით (0-23).")
        return HOUR

async def handle_minute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        minute = int(update.message.text)
        if 0 <= minute <= 59:
            context.user_data['minute'] = minute
            logger.info(f"User {update.effective_user.id} entered minute: {minute}")
            await update.message.reply_text("შეიყვანეთ დაბადების ქალაქი (მაგ., Tbilisi, Kutaisi):")
            return CITY
        else:
            await update.message.reply_text("გთხოვთ, შეიყვანოთ წუთი 0-დან 59-მდე.")
            return MINUTE
    except ValueError:
        await update.message.reply_text("გთხოვთ, შეიყვანოთ წუთი რიცხვით (0-59).")
        return MINUTE

async def handle_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    city = update.message.text
    if not city or len(city) < 2:
         await update.message.reply_text("გთხოვთ, შეიყვანოთ კორექტული ქალაქის სახელი.")
         return CITY
    context.user_data['city'] = city.strip()
    logger.info(f"User {update.effective_user.id} entered city: {city.strip()}")
    await update.message.reply_text("შეიყვანეთ ქვეყნის კოდი (სურვილისამებრ, მაგ., GE, US, GB), ან გამოტოვეთ /skip ბრძანებით:")
    return NATION

async def handle_nation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nation_input = update.message.text.strip().upper()
    user_id = update.effective_user.id
    chat_id = update.message.chat_id
    if len(nation_input) < 2 or len(nation_input) > 3 or not nation_input.isalpha():
         await update.message.reply_text("არასწორი ქვეყნის კოდის ფორმატი. გთხოვთ, შეიყვანოთ 2 ან 3 ასო (მაგ., GE) ან /skip.")
         return NATION
    context.user_data['nation'] = nation_input
    logger.info(f"User {update.effective_user.id} entered nation: {nation_input}")
    if save_user_data(user_id, context.user_data):
        await update.message.reply_text("მონაცემები შენახულია.")
    else:
        await update.message.reply_text("მონაცემების შენახვისას მოხდა შეცდომა.")
    await generate_and_send_chart(context.user_data, chat_id, context)
    context.user_data.clear()
    logger.info(f"Conversation ended for user {user_id}.")
    return ConversationHandler.END

async def skip_nation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    chat_id = update.message.chat_id
    logger.info(f"User {user_id} skipped nation input.")
    context.user_data['nation'] = None
    if save_user_data(user_id, context.user_data):
        await update.message.reply_text("მონაცემები შენახულია (ქვეყნის გარეშე).")
    else:
        await update.message.reply_text("მონაცემების შენახვისას მოხდა შეცდომა.")
    await generate_and_send_chart(context.user_data, chat_id, context)
    context.user_data.clear()
    logger.info(f"Conversation ended for user {user_id}.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    logger.info(f"User {user.id} canceled the conversation.")
    context.user_data.clear()
    await update.message.reply_text('მონაცემების შეყვანის პროცესი გაუქმებულია.')
    return ConversationHandler.END

# --- სხვა ბრძანებები ---
async def show_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
     user_id = update.effective_user.id
     user_data = get_user_data(user_id)
     if user_data:
         text = "თქვენი შენახული მონაცემებია:\n"
         text += f"  სახელი: {user_data.get('name', '-')}\n"
         text += f"  თარიღი: {user_data.get('day', '-')}/{user_data.get('month', '-')}/{user_data.get('year', '-')}\n"
         text += f"  დრო: {user_data.get('hour', '-')}:{user_data.get('minute', '-')}\n"
         text += f"  ქალაქი: {user_data.get('city', '-')}\n"
         text += f"  ქვეყანა: {user_data.get('nation') or 'არ არის მითითებული'}"
         await update.message.reply_text(text)
     else:
         await update.message.reply_text("თქვენ არ გაქვთ შენახული მონაცემები. გამოიყენეთ /createchart დასამატებლად.")

async def delete_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if delete_user_data(user_id):
        await update.message.reply_text("თქვენი შენახული მონაცემები წარმატებით წაიშალა.")
    else:
        await update.message.reply_text("მონაცემების წაშლისას მოხდა შეცდომა ან მონაცემები არ არსებობდა.")

# --- მთავარი ფუნქცია ---
def main() -> None:
    """Start the bot in polling mode."""
    init_db()

    if not TELEGRAM_BOT_TOKEN:
        logger.critical("Error: TELEGRAM_BOT_TOKEN environment variable not set. Bot cannot start.")
        return
    if not GEMINI_API_KEY:
         logger.warning("Warning: GEMINI_API_KEY environment variable not set. AI features will be disabled.")

    logger.info("Creating application...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Conversation Handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('createchart', create_chart_start)],
        states={
            SAVED_DATA_CHOICE: [
                 CallbackQueryHandler(handle_saved_data_choice, pattern='^(use_saved_data|enter_new_data|cancel_creation)$')
            ],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
            YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_year)],
            MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_month)],
            DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_day)],
            HOUR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_hour)],
            MINUTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_minute)],
            CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_city)],
            NATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_nation),
                CommandHandler('skip', skip_nation)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    # Handler-ების რეგისტრაცია
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("mydata", show_my_data))
    application.add_handler(CommandHandler("deletedata", delete_data))

    logger.info("Handlers registered (Conversation, start, mydata, deletedata).")

    # ბოტის გაშვება POLLING რეჟიმში
    logger.info("Starting bot polling...")
    # შესწორებულია: ვიყენებთ Update კლასს პირდაპირ telegram მოდულიდან
    application.run_polling(allowed_updates=Update.ALL_TYPES)

# --- სკრიპტის გაშვების წერტილი ---
if __name__ == "__main__":
    load_dotenv()
    main()