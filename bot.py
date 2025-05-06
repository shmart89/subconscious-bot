# -*- coding: utf-8 -*-
import os
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
import asyncio
import re

# --- Gemini AI Setup ---
import google.generativeai as genai
from google.generativeai.types import generation_types

# -------------------------

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# Kerykeion იმპორტი და ასპექტების კლასი
from kerykeion import AstrologicalSubject, NatalAspects
from kerykeion.kr_types import KerykeionException # შეცდომის დასაჭერად

# .env ფაილიდან გარემოს ცვლადების ჩატვირთვა
load_dotenv()

# --- კონფიგურაცია ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEONAMES_USERNAME = os.getenv("GEONAMES_USERNAME") # !!! მნიშვნელოვანია ამის დაყენება .env-ში !!!
DB_FILE = "user_data.db"
TELEGRAM_MESSAGE_LIMIT = 4096

# ასპექტების კონფიგურაცია
ASPECT_PLANETS = ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'Pluto', 'Ascendant', 'Midheaven']
MAJOR_ASPECTS = ['conjunction', 'opposition', 'square', 'trine', 'sextile']
ASPECT_ORBS = {'Sun': 8, 'Moon': 8, 'Ascendant': 5, 'Midheaven': 5, 'default': 6}

# --- Gemini კონფიგურაცია ---
gemini_model = None
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
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
logging.getLogger("kerykeion").setLevel(logging.WARNING)
logging.getLogger("google.generativeai").setLevel(logging.WARNING)
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

# --- პლანეტების და ასპექტების ემოჯები/თარგმანები ---
planet_emojis = {
    "Sun": "☀️", "Moon": "🌙", "Mercury": "☿️", "Venus": "♀️", "Mars": "♂️",
    "Jupiter": "♃", "Saturn": "♄", "Uranus": "♅", "Neptune": "♆", "Pluto": "♇",
    "Ascendant": "⬆️", "Midheaven": " Mᶜ", # MC - Midheaven
}
aspect_translations = {
    "conjunction": "შეერთება", "opposition": "ოპოზიცია", "square": "კვადრატი",
    "trine": "ტრიგონი", "sextile": "სექსტილი"
}
aspect_symbols = {
    "conjunction": "☌", "opposition": "☍", "square": "□",
    "trine": "△", "sextile": "∗"
}

# --- Gemini-სთან კომუნიკაციის ფუნქცია ---
# (get_gemini_interpretation - უცვლელია)
async def get_gemini_interpretation(prompt: str) -> str:
    if not gemini_model:
        return "(Gemini API მიუწვდომელია)"
    try:
        request_options = {"timeout": 120}
        response = await gemini_model.generate_content_async(
            prompt,
            generation_config={"response_mime_type": "text/plain"},
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


# --- დამხმარე ფუნქცია ტექსტის ნაწილებად დასაყოფად ---
# (split_text - უცვლელია)
def split_text(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    parts = []
    while len(text.encode('utf-8')) > limit:
        limit_bytes = limit - 10
        search_text_bytes = text.encode('utf-8')[:limit_bytes]
        split_pos_byte = search_text_bytes.rfind(b'\n\n')
        if split_pos_byte == -1: split_pos_byte = search_text_bytes.rfind(b'\n')
        if split_pos_byte == -1: split_pos_byte = search_text_bytes.rfind(b'. ')
        if split_pos_byte == -1 or split_pos_byte < limit_bytes // 2 :
             split_pos_byte = search_text_bytes.rfind(b' ')
             if split_pos_byte == -1: split_pos_byte = limit_bytes
        split_pos_char = len(search_text_bytes[:split_pos_byte].decode('utf-8', errors='ignore'))
        parts.append(text[:split_pos_char])
        text = text[split_pos_char:].lstrip()
    parts.append(text)
    if parts and not parts[0]: return parts[1:] if len(parts) > 1 else []
    return parts


# --- რუკის გენერირების და გაგზავნის ფუნქცია (შესწორებული ასპექტებით) ---
async def generate_and_send_chart(user_data: dict, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Generates natal chart using Kerykeion and gets interpretations from Gemini via a single prompt."""
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
        # --- Kerykeion-ის გამოთვლა ---
        if not GEONAMES_USERNAME:
            logger.warning("GEONAMES_USERNAME not set. Kerykeion might have issues with city lookup.")
            await context.bot.send_message(chat_id=chat_id, text="⚠️ გაფრთხილება: GeoNames მომხმარებლის სახელი არ არის დაყენებული. ქალაქის ძებნა შეიძლება ვერ მოხერხდეს.")
            # აქ შეგვიძლია გავაგრძელოთ ან გავაჩეროთ, მომხმარებლის ინფორმირება ჯობს. გავაგრძელოთ ამ ეტაპზე.

        try:
            logger.info("Calculating AstrologicalSubject...")
            # ვცადოთ asyncio.to_thread
            subject_instance = await asyncio.to_thread(
                AstrologicalSubject, name, year, month, day, hour, minute, city, nation=nation # აქ აღარ გადავცემთ kerykeion_username-ს
            )
            logger.info("AstrologicalSubject calculated.")
        except RuntimeError as e:
             logger.warning(f"asyncio.to_thread failed ({e}), calling Kerykeion directly.")
             subject_instance = AstrologicalSubject(name, year, month, day, hour, minute, city, nation=nation) # აქაც აღარ გადავცემთ
             logger.info("AstrologicalSubject calculated directly.")
        except KerykeionException as ke: # დავიჭიროთ ქალაქის ვერ პოვნის შეცდომა
             logger.error(f"KerykeionException for {name}: {ke}")
             await processing_message.edit_text(text=f"შეცდომა: Kerykeion-მა ვერ იპოვა მონაცემები ქალაქისთვის '{city}'. გთხოვთ, შეამოწმოთ ქალაქის სახელი და სცადოთ თავიდან /createchart.")
             return # ვწყვეტთ მუშაობას
        except Exception as keryk_err: # დავიჭიროთ სხვა Kerykeion შეცდომები
             logger.error(f"Error creating AstrologicalSubject for {name}: {keryk_err}", exc_info=True)
             await processing_message.edit_text(text="შეცდომა მოხდა ასტროლოგიური მონაცემების გამოთვლისას.")
             return

        # --- ასპექტების გამოთვლა (შესწორებული) ---
        logger.info("Calculating aspects...")
        try:
             # !!! შესწორება: წაიშალა aspects_list პარამეტრი !!!
             aspect_calculator = NatalAspects(subject_instance, planets=ASPECT_PLANETS, orb_dictionary=ASPECT_ORBS)
             all_relevant_aspects = aspect_calculator.get_relevant_aspects()
             logger.info(f"Found {len(all_relevant_aspects)} relevant aspects based on orbs.")

             # ვფილტრავთ მხოლოდ მაჟორულ ასპექტებს ჩვენი სიიდან
             filtered_aspects = [
                 asp for asp in all_relevant_aspects
                 if asp.get('aspect') in MAJOR_ASPECTS
             ]
             logger.info(f"Filtered down to {len(filtered_aspects)} major aspects.")
        except Exception as aspect_err:
             logger.error(f"Error calculating aspects for {name}: {aspect_err}", exc_info=True)
             filtered_aspects = [] # თუ ასპექტების გამოთვლა ვერ მოხერხდა, სია ცარიელი იქნება
             await context.bot.send_message(chat_id=chat_id, text="⚠️ გაფრთხილება: ასპექტების გამოთვლისას მოხდა შეცდომა.")


        # --- მონაცემების მომზადება Prompt-ისთვის ---
        planets_data_str = ""
        planet_list_for_prompt = ['Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune', 'Pluto', 'Ascendant', 'Midheaven']
        for planet_name in planet_list_for_prompt:
            try:
                 obj_name_in_kerykeion = planet_name.lower().replace('midheaven', 'mc')
                 planet_obj = getattr(subject_instance, obj_name_in_kerykeion)
                 sign = planet_obj.get('sign', '?')
                 pos = planet_obj.get('position', 0.0)
                 house = planet_obj.get('house', '?') # სახლი შეიძლება "?" იყოს, თუ ვერ გამოითვალა
                 retro = " (R)" if planet_obj.get('isRetro') == 'true' else ""
                 # სახლის დამატება ტექსტში
                 house_str = f", {house}-ე სახლი" if isinstance(house, int) else ""
                 planets_data_str += f"- {planet_name}: {sign} {pos:.2f}°{house_str}{retro}\n"
            except Exception as e:
                 logger.error(f"Error getting data for {planet_name}: {e}")
                 planets_data_str += f"- {planet_name}: მონაცემების წაკითხვის შეცდომა\n"

        aspects_data_str = ""
        if filtered_aspects:
            for aspect in filtered_aspects:
                p1 = aspect.get('p1_name')
                p2 = aspect.get('p2_name')
                aspect_type = aspect.get('aspect')
                orb = aspect.get('orbit', 0.0)
                if p1 and p2 and aspect_type:
                     aspect_name_ge = aspect_translations.get(aspect_type, aspect_type)
                     aspects_data_str += f"- {p1} {aspect_name_ge} {p2} ({orb:.1f}°)\n"
        if not aspects_data_str:
             aspects_data_str = "- მნიშვნელოვანი მაჟორული ასპექტები ვერ მოიძებნა მითითებული ორბისებით.\n"

        # --- დიდი Prompt-ის შექმნა ---
        large_prompt = f"""შენ ხარ გამოცდილი, პროფესიონალი ასტროლოგი, რომელიც წერს სიღრმისეულ და დეტალურ ნატალური რუკის ანალიზს ქართულ ენაზე.
მიჰყევი მოთხოვნილ სტრუქტურას და თითოეულ პუნქტზე დაწერე მინიმუმ 3-4 წინადადება, რომელიც ხსნის მის მნიშვნელობას მოცემული ადამიანისთვის ({name}).
გამოიყენე პროფესიონალური, მაგრამ გასაგები ენა. მოერიდე დაზეპირებულ ფრაზებს. იყავი მაქსიმალურად ზუსტი და დეტალური, PDF ნიმუშის მსგავსად.

**მონაცემები:**
სახელი: {name}
თარიღი: {day}/{month}/{year} {hour:02d}:{minute:02d}
ადგილმდებარეობა: {city}{f', {nation}' if nation else ''}
ზოდიაქო: ტროპიკული
სახლების სისტემა: პლაციდუსი

**პლანეტების მდებარეობა:**
{planets_data_str}
**მნიშვნელოვანი ასპექტები (პლანეტა1, ასპექტი, პლანეტა2, ორბისი):**
{aspects_data_str}
**დავალება:**
დაწერე სრული ანალიზი, დაყოფილი შემდეგ სექციებად. გამოიყენე ზუსტად ეს სექციების სახელები და ფორმატირება (მაგ., `[SECTION: PlanetsInSigns]`):

[SECTION: PlanetsInSigns]
(აქ დაწერე დეტალური ანალიზი თითოეული პლანეტისთვის (Sun-Pluto) მის ნიშანში. თითოეულზე 3-4 წინადადება მინიმუმ.)

[SECTION: PlanetsInHouses]
(აქ დაწერე დეტალური ანალიზი თითოეული პლანეტისთვის (Sun-Pluto) მის სახლში, თუ სახლის ნომერი ცნობილია. თითოეულზე 3-4 წინადადება მინიმუმ.)

[SECTION: Aspects]
(აქ დაწერე დეტალური ანალიზი თითოეული ჩამოთვლილი ასპექტისთვის. თითოეულზე 3-4 წინადადება მინიმუმ.)

გთხოვ, პასუხი დააბრუნო მხოლოდ ამ სამი სექციის ტექსტით, დაწყებული `[SECTION: PlanetsInSigns]`-ით. არ დაამატო შესავალი ან დასკვნითი სიტყვები.
"""

        # --- Gemini-ს გამოძახება ---
        await processing_message.edit_text(text="""ასტროლოგიური მონაცემები გამოთვლილია. ვიწყებ დეტალური ინტერპრეტაციების გენერირებას Gemini-სთან...
⏳ ამას შეიძლება 1-2 წუთი დასჭირდეს.""", parse_mode=ParseMode.HTML)

        logger.info(f"Sending large prompt to Gemini for user {chat_id}")
        full_interpretation_text = await get_gemini_interpretation(large_prompt)
        logger.info(f"Received full interpretation from Gemini for user {chat_id}. Length: {len(full_interpretation_text)}")

        # --- Gemini-ს პასუხის დამუშავება და ფორმატირება ---
        final_report_parts = []

        # 1. ძირითადი ინფორმაცია
        base_info_text = (
            f"✨ {name}-ს ნატალური რუკა ✨\n\n"
            f"<b>დაბადების მონაცემები:</b> {day}/{month}/{year}, {hour:02d}:{minute:02d}, {city}{f', {nation}' if nation else ''}\n"
            f"<b>ზოდიაქო:</b> ტროპიკული, <b>სახლები:</b> პლაციდუსი\n\n"
        )
        try: sun_info = subject_instance.sun; base_info_text += f"☀️ <b>მზე:</b> {sun_info['sign']} (<code>{sun_info['position']:.2f}°</code>)\n"
        except: pass
        try: asc_info = subject_instance.ascendant; base_info_text += f"⬆️ <b>ასცედენტი:</b> {asc_info['sign']} (<code>{asc_info['position']:.2f}°</code>)\n"
        except: pass
        time_note = "\n<i>(შენიშვნა: დრო მითითებულია 12:00. ასცედენტი და სახლები შეიძლება არ იყოს ზუსტი.)</i>" if hour == 12 and minute == 0 else ""
        base_info_text += time_note + "\n"
        final_report_parts.append(base_info_text)

        # 2. ვპოულობთ სექციებს Gemini-ს ტექსტში
        # გამოვიყენოთ re.IGNORECASE და გავაუმჯობესოთ პატერნი
        planets_in_signs_match = re.search(r"\[SECTION:\s*PlanetsInSigns\s*\](.*?)(?:\[SECTION:|\Z)", full_interpretation_text, re.DOTALL | re.IGNORECASE)
        planets_in_houses_match = re.search(r"\[SECTION:\s*PlanetsInHouses\s*\](.*?)(?:\[SECTION:|\Z)", full_interpretation_text, re.DOTALL | re.IGNORECASE)
        aspects_match = re.search(r"\[SECTION:\s*Aspects\s*\](.*?)(?:\[SECTION:|\Z)", full_interpretation_text, re.DOTALL | re.IGNORECASE)

        # 3. ვამატებთ სექციებს ფორმატირებით
        if planets_in_signs_match:
            text = planets_in_signs_match.group(1).strip()
            if text: final_report_parts.append(f"\n--- 🪐 <b>პლანეტები ნიშნებში</b> ---\n\n{text}")
        else: logger.warning("Could not parse [SECTION: PlanetsInSigns] from Gemini response.")

        if planets_in_houses_match:
            text = planets_in_houses_match.group(1).strip()
            if text: final_report_parts.append(f"\n--- 🏠 <b>პლანეტები სახლებში</b> ---\n\n{text}")
        else: logger.warning("Could not parse [SECTION: PlanetsInHouses] from Gemini response.")

        if aspects_match:
            text = aspects_match.group(1).strip()
            if text: final_report_parts.append(f"\n--- ✨ <b>ასპექტები</b> ---\n\n{text}")
        else: logger.warning("Could not parse [SECTION: Aspects] from Gemini response.")

        # თუ სექციები ვერ ვიპოვეთ, ან თუ Gemini-მ შეცდომა დააბრუნა
        if len(final_report_parts) == 1:
            if full_interpretation_text.startswith("("): # Gemini-ს შეცდომის ტექსტი
                 final_report_parts.append(f"\n<b>ინტერპრეტაცია ვერ მოხერხდა:</b>\n{full_interpretation_text}")
            elif len(full_interpretation_text) > 10: # თუ ტექსტი არის, მაგრამ ტეგები არ/ვერ დაიცვა
                 logger.warning("Could not parse sections, showing raw Gemini text.")
                 final_report_parts.append(f"\n<b>ინტერპრეტაცია (დაუმუშავებელი):</b>\n{full_interpretation_text}")
            # თუ ცარიელი პასუხია, არაფერს ვამატებთ

        # --- საბოლოო პასუხის გაგზავნა (ნაწილებად) ---
        full_response_text = "".join(final_report_parts).strip()

        if not full_response_text or full_response_text == base_info_text.strip(): # თუ მხოლოდ ბაზის ინფო დარჩა
            await processing_message.edit_text(text="ინტერპრეტაციების გენერირება ვერ მოხერხდა.")
            return

        parts = split_text(full_response_text)
        logger.info(f"Sending response in {len(parts)} parts.")

        # პირველ ნაწილს ვარედაქტირებთ
        await processing_message.edit_text(text=parts[0], parse_mode=ParseMode.HTML)
        # დანარჩენს ვგზავნით ახალ შეტყობინებებად
        for part in parts[1:]:
            await context.bot.send_message(chat_id=chat_id, text=part, parse_mode=ParseMode.HTML)

        logger.info(f"Full detailed chart sent for {name}.")

    # --- შეცდომების დაჭერა ---
    except KerykeionException as ke: # დავიჭიროთ Kerykeion შეცდომები ცალკე
        logger.error(f"KerykeionException for {name}: {ke}", exc_info=True)
        await processing_message.edit_text(text=f"შეცდომა ასტროლოგიური მონაცემების გამოთვლისას: {ke}. გთხოვთ, შეამოწმოთ შეყვანილი მონაცემები, განსაკუთრებით ქალაქი.")
    except ConnectionError as ce:
        logger.error(f"ConnectionError during chart generation for {name}: {ce}")
        await processing_message.edit_text(text=f"კავშირის შეცდომა მოხდა (სავარაუდოდ GeoNames ან Gemini). სცადეთ მოგვიანებით.")
    except Exception as e:
        logger.error(f"An unexpected error occurred generating chart for {name}: {e}", exc_info=True)
        try:
            await processing_message.edit_text(text=f"მოულოდნელი შეცდომა მოხდა რუკის გენერაციისას: {type(e).__name__}")
        except Exception:
             await context.bot.send_message(chat_id=chat_id, text="მოულოდნელი შეცდომა მოხდა რუკის გენერაციისას.")


# --- Handler ფუნქციები ---
# (start, create_chart_start, handle_saved_data_choice, handle_name, ..., cancel, show_my_data, delete_data - უცვლელია)
# (აქ აღარ ჩავსვი ადგილის დასაზოგად, გამოიყენეთ წინა ვერსიიდან)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

(NAME, YEAR, MONTH, DAY, HOUR, MINUTE, CITY, NATION, SAVED_DATA_CHOICE) = range(9)

async def create_chart_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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

async def show_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
     user_id = update.effective_user.id
     user_data = get_user_data(user_id)
     if user_data:
         text = "თქვენი შენახული მონაცემებია:\n"
         text += f"  <b>სახელი:</b> {user_data.get('name', '-')}\n"
         text += f"  <b>თარიღი:</b> {user_data.get('day', '-')}/{user_data.get('month', '-')}/{user_data.get('year', '-')}\n"
         text += f"  <b>დრო:</b> {user_data.get('hour', '-')}:{user_data.get('minute', '-')}\n"
         text += f"  <b>ქალაქი:</b> {user_data.get('city', '-')}\n"
         text += f"  <b>ქვეყანა:</b> {user_data.get('nation') or 'არ არის მითითებული'}"
         await update.message.reply_text(text, parse_mode=ParseMode.HTML)
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
    if not gemini_model: # შევამოწმოთ მოდელი ჩაიტვირთა თუ არა
         logger.warning("Gemini model not loaded (check API key?). AI features will be disabled in responses.")

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
    application.run_polling(allowed_updates=Update.ALL_TYPES)

# --- სკრიპტის გაშვების წერტილი ---
if __name__ == "__main__":
    load_dotenv()
    main()