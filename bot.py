import os
import json
import logging
import sqlite3 # <-- მონაცემთა ბაზისთვის
from datetime import datetime
from pathlib import Path # <-- ფაილის გზისთვის

from dotenv import load_dotenv
# განახლებული იმპორტები telegram.ext-დან
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup # Update აქედანაც არის იმპორტირებული
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackQueryHandler # <-- დაემატა იმპორტი
)

# შესწორებული Kerykeion იმპორტი (try...except-ის გარეშე)
from kerykeion import AstrologicalSubject

# .env ფაილიდან გარემოს ცვლადების ჩატვირთვა
load_dotenv()

# --- კონფიგურაცია ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEONAMES_USERNAME = os.getenv("GEONAMES_USERNAME")
DB_FILE = "user_data.db"

# ლოგირების ჩართვა
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("kerykeion").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- მონაცემთა ბაზის ფუნქციები ---
# (init_db, save_user_data, get_user_data, delete_user_data ფუნქციები უცვლელია)
def init_db():
    """ქმნის მონაცემთა ბაზის ფაილს და ცხრილს, თუ არ არსებობს."""
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
    """ინახავს ან ანახლებს მომხმარებლის მონაცემებს ბაზაში."""
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
    """აბრუნებს მომხმარებლის შენახულ მონაცემებს ბაზიდან, თუ არსებობს."""
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row # შედეგი ლექსიკონის სახით რომ დააბრუნოს
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
    """შლის მომხმარებლის მონაცემებს ბაზიდან."""
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

# --- დამხმარე ფუნქცია რუკის გენერირებისთვის და გაგზავნისთვის ---
# (generate_and_send_chart ფუნქცია უცვლელია)
async def generate_and_send_chart(user_data: dict, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Generates natal chart using Kerykeion and sends the result."""
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

    logger.info(f"Generating chart for: {name}, {day}/{month}/{year} {hour}:{minute}, {city}, {nation}")
    processing_message = await context.bot.send_message(chat_id=chat_id, text="მონაცემები მიღებულია, ვიწყებ რუკის გამოთვლას...")

    try:
        if not GEONAMES_USERNAME:
            logger.warning("GEONAMES_USERNAME not set. Kerykeion might have issues with city lookup.")

        subject_instance = AstrologicalSubject(name, year, month, day, hour, minute, city, nation=nation, kerykeion_username=GEONAMES_USERNAME)

        sun_info = subject_instance.sun
        sun_sign = sun_info['sign']
        sun_position = f"{sun_info['position']:.2f}°"

        try:
            ascendant_info = subject_instance.first_house
            ascendant_sign = ascendant_info['sign']
            ascendant_position = f"{ascendant_info['position']:.2f}°"
            asc_text = f"ასცედენტი: {ascendant_sign} {ascendant_position}\n\n"
        except Exception as asc_err:
             logger.warning(f"Could not calculate Ascendant for {name}: {asc_err}")
             asc_text = "ასცედენტი: (ვერ გამოითვალა - შეამოწმეთ ქალაქი/დრო)\n\n"

        planets_text = "მთავარი პლანეტები:\n"
        main_planets = ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn']
        for planet_name in main_planets:
            try:
                planet_obj = getattr(subject_instance, planet_name.lower())
                planets_text += f"- {planet_name}: {planet_obj['sign']} ({planet_obj['position']:.2f}°)\n"
            except Exception as planet_err:
                logger.error(f"Error getting info for planet {planet_name}: {planet_err}")
                planets_text += f"- {planet_name}: (შეცდომა)\n"

        time_note = ""
        if hour == 12 and minute == 0:
             time_note = "\n(შენიშვნა: დრო მითითებულია 12:00, რადგან ზუსტი დრო უცნობი იყო. ასცედენტი და სახლები შეიძლება არ იყოს ზუსტი.)"

        response_text = (
            f"✨ {name}-ს ნატალური რუკა ✨\n\n"
            f"დაბადების მონაცემები: {day}/{month}/{year}, {hour:02d}:{minute:02d}, {city}{f', {nation}' if nation else ''}\n\n"
            f"მზე: {sun_sign} {sun_position}\n"
            f"{asc_text}"
            f"{planets_text}"
            f"{time_note}"
        )
        logger.info(f"Chart generated successfully for {name}.")
        await processing_message.edit_text(text=response_text)

    except ConnectionError as ce:
        logger.error(f"Kerykeion ConnectionError for {name}: {ce}")
        await processing_message.edit_text(text=f"Kerykeion კავშირის შეცდომა (სავარაუდოდ GeoNames): {ce}. შეამოწმეთ ინტერნეტ კავშირი ან სცადეთ მოგვიანებით.")
    except Exception as e:
        logger.error(f"An unexpected error occurred generating chart for {name}: {e}", exc_info=True)
        await processing_message.edit_text(text=f"მოულოდნელი შეცდომა მოხდა რუკის გენერაციისას.")


# --- Handler ფუნქციები (განსაზღვრული main-ამდე) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    user = update.effective_user
    user_data = get_user_data(user.id) # შევამოწმოთ, აქვს თუ არა მონაცემები
    start_text = rf"გამარჯობა {user.mention_html()}! მე ვარ Subconscious ბოტი."
    if user_data:
         start_text += f"\n\nთქვენი შენახული მონაცემებია: {user_data.get('name')}, {user_data.get('day')}/{user_data.get('month')}/{user_data.get('year')}."
         start_text += "\nგამოიყენეთ /createchart ახალი რუკის შესადგენად (შეგიძლიათ აირჩიოთ შენახული მონაცემების გამოყენება)."
         start_text += "\n/mydata - შენახული მონაცემების ჩვენება."
         start_text += "\n/deletedata - შენახული მონაცემების წაშლა."
    else:
        start_text += "\n\nნატალური რუკის შესაქმნელად გამოიყენეთ /createchart ბრძანება."

    await update.message.reply_html(start_text)


# --- ConversationHandler-ის მდგომარეობები ---
(NAME, YEAR, MONTH, DAY, HOUR, MINUTE, CITY, NATION, SAVED_DATA_CHOICE) = range(9) # დაემატა SAVED_DATA_CHOICE

# --- ConversationHandler-ის ფუნქციები ---

async def create_chart_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation or asks about using saved data."""
    user_id = update.effective_user.id
    logger.info(f"User {user_id} started chart creation process with /createchart.")
    context.user_data.clear() # ვასუფთავებთ წინა დროებით მონაცემებს

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
        return SAVED_DATA_CHOICE # გადავდივართ ახალ მდგომარეობაში, ველოდებით ღილაკის პასუხს
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
    await query.answer() # აუცილებელია callback query-ზე პასუხის გასაცემად
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
            await query.message.reply_text("გთხოვთ, შეიყვანოთ სახელი, ვისთვისაც ვადგენთ რუკას:") # პირდაპირ ვესაუბრებით ჩატს
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
    """Handles the name input and asks for the year."""
    user_name_input = update.message.text
    if not user_name_input or len(user_name_input) < 2:
         await update.message.reply_text("გთხოვთ, შეიყვანოთ კორექტული სახელი (მინ. 2 სიმბოლო).")
         return NAME

    context.user_data['name'] = user_name_input
    logger.info(f"User {update.effective_user.id} entered name: {user_name_input}")
    await update.message.reply_text(f"გმადლობთ, {user_name_input}. ახლა გთხოვთ, შეიყვანოთ დაბადების წელი (მაგ., 1990):")
    return YEAR

async def handle_year(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the year input and asks for the month."""
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
    """Handles the month input and asks for the day."""
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
    """Handles the day input and asks for the hour."""
    try:
        day = int(update.message.text)
        # TODO: Add validation based on month and leap year later if needed
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
    """Handles the hour input and asks for the minute."""
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
    """Handles the minute input and asks for the city."""
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
    """Handles the city input and asks for the nation (optional)."""
    city = update.message.text
    if not city or len(city) < 2:
         await update.message.reply_text("გთხოვთ, შეიყვანოთ კორექტული ქალაქის სახელი.")
         return CITY

    context.user_data['city'] = city.strip()
    logger.info(f"User {update.effective_user.id} entered city: {city.strip()}")
    await update.message.reply_text("შეიყვანეთ ქვეყნის კოდი (სურვილისამებრ, მაგ., GE, US, GB), ან გამოტოვეთ /skip ბრძანებით:")
    return NATION

async def handle_nation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the nation input, saves data, generates chart, and ends conversation."""
    nation_input = update.message.text.strip().upper()
    user_id = update.effective_user.id
    chat_id = update.message.chat_id

    # ქვეყნის კოდის ვალიდაცია (2 ან 3 ასო)
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
    """Handles skipping the nation input."""
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
    """Cancels and ends the conversation."""
    user = update.effective_user
    logger.info(f"User {user.id} canceled the conversation.")
    context.user_data.clear()
    await update.message.reply_text('მონაცემების შეყვანის პროცესი გაუქმებულია.')
    return ConversationHandler.END

# --- სხვა ბრძანებები ---

async def show_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
     """Shows the user their saved birth data."""
     user_id = update.effective_user.id
     user_data = get_user_data(user_id)
     if user_data:
         text = "თქვენი შენახული მონაცემებია:\n"
         text += f"  სახელი: {user_data.get('name', '-')}\n"
         text += f"  თარიღი: {user_data.get('day', '-')}/{user_data.get('month', '-')}/{user_data.get('year', '-')}\n"
         text += f"  დრო: {user_data.get('hour', '-')}:{user_data.get('minute', '-')}\n"
         text += f"  ქალაქი: {user_data.get('city', '-')}\n"
         text += f"  ქვეყანა: {user_data.get('nation') or 'არ არის მითითებული'}" # None-ის შემთხვევაში გამოიტანს ტექსტს
         await update.message.reply_text(text)
     else:
         await update.message.reply_text("თქვენ არ გაქვთ შენახული მონაცემები. გამოიყენეთ /createchart დასამატებლად.")

async def delete_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deletes the user's saved birth data."""
    user_id = update.effective_user.id
    if delete_user_data(user_id):
        await update.message.reply_text("თქვენი შენახული მონაცემები წარმატებით წაიშალა.")
    else:
        await update.message.reply_text("მონაცემების წაშლისას მოხდა შეცდომა ან მონაცემები არ არსებობდა.")


# --- მთავარი ფუნქცია ---

def main() -> None:
    """Start the bot in polling mode."""
    # შევქმნათ/შევამოწმოთ მონაცემთა ბაზა გაშვებისას
    init_db()

    if not TELEGRAM_BOT_TOKEN:
        logger.critical("Error: TELEGRAM_BOT_TOKEN environment variable not set. Bot cannot start.")
        return

    logger.info("Creating application...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- Conversation Handler-ის შექმნა ---
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('createchart', create_chart_start)],
        states={
            SAVED_DATA_CHOICE: [ # ახალი მდგომარეობა ღილაკზე პასუხისთვის
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
                CommandHandler('skip', skip_nation) # /skip ბრძანება ქვეყნის გამოსატოვებლად
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    # Handler-ების რეგისტრაცია
    application.add_handler(conv_handler) # <-- ვამატებთ ConversationHandler-ს
    application.add_handler(CommandHandler("start", start)) # /start ისევ გვჭირდება მისასალმებლად
    application.add_handler(CommandHandler("mydata", show_my_data)) # ახალი ბრძანება მონაცემების სანახავად
    application.add_handler(CommandHandler("deletedata", delete_data)) # ახალი ბრძანება მონაცემების წასაშლელად

    logger.info("Handlers registered (Conversation, start, mydata, deletedata).")

    # ბოტის გაშვება POLLING რეჟიმში
    logger.info("Starting bot polling...")
    # ვიყენებთ Update.ALL_TYPES, რათა CallbackQueryHandler-მა იმუშაოს
    application.run_polling(allowed_updates=Update.ALL_TYPES) # <-- შესწორებულია TelegramUpdate -> Update

# --- სკრიპტის გაშვების წერტილი ---
if __name__ == "__main__":
    load_dotenv()
    main()