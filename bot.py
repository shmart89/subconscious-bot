import asyncio
import os
import json
import logging
from datetime import datetime
from urllib.parse import urljoin

from aiohttp import web # ვებ სერვერისთვის
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Update as TelegramUpdate
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# ვცადოთ Kerykeion-ის იმპორტი, როგორც დოკუმენტაციაშია
try:
    from kerykeion import AstrologicalSubject
except ImportError:
    # ალტერნატიული იმპორტი, თუ წინა ვერ ხერხდება (იშვიათი შემთხვევა)
    from kerykeion.kerykeion import AstrologicalSubject

# .env ფაილიდან გარემოს ცვლადების ჩატვირთვა
load_dotenv()

# --- კონფიგურაცია ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # Gemini ჯერ არ გამოიყენება, მაგრამ დავტოვოთ
GEONAMES_USERNAME = os.getenv("GEONAMES_USERNAME") # Kerykeion-ს შეიძლება დასჭირდეს, თუ ჩართულია

# Web App URL
WEBAPP_URL = "https://shmart89.github.io/subconscious-bot/natal_form.html"

# ლოგირების ჩართვა
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("kerykeion").setLevel(logging.WARNING) # Kerykeion-ის ლოგირების დონის დაწევა
logger = logging.getLogger(__name__)

# --- ბრძანებების და WebApp-ის დამმუშავებელი ფუნქციები ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    user = update.effective_user
    keyboard = [
        [InlineKeyboardButton("✨ ნატალური რუკის შექმნა ✨", web_app=WebAppInfo(url=WEBAPP_URL))]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_html(
        rf"გამარჯობა {user.mention_html()}! მე ვარ Subconscious ბოტი."
        "\n\nდააჭირე ღილაკს ნატალური რუკის შესაქმნელად:",
        reply_markup=reply_markup
    )

async def natal_chart_webapp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message with a button to open the Natal Chart Web App (alternative command)."""
    keyboard = [
        [InlineKeyboardButton("✍️ მონაცემების შეყვანა ✍️", web_app=WebAppInfo(url=WEBAPP_URL))]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "გთხოვთ, დააჭიროთ ღილაკს დაბადების მონაცემების შესაყვანად:",
        reply_markup=reply_markup
    )

async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles data received from the Web App."""
    if not update.effective_message or not update.effective_message.web_app_data:
        logger.warning("Received update without message or web_app_data, ignoring.")
        return

    data_str = update.effective_message.web_app_data.data
    chat_id = update.effective_chat.id
    logger.info(f"Received web app data for chat_id {chat_id}: {data_str}")
    # ვაგზავნით "processing" შეტყობინებას დაუყოვნებლივ
    processing_message = await context.bot.send_message(chat_id=chat_id, text="მონაცემები მიღებულია, ვიწყებ დამუშავებას...")

    try:
        data = json.loads(data_str)

        name = data.get("name")
        birthdate_str = data.get("birthdate") # "YYYY-MM-DD"
        birthtime_str = data.get("birthtime") # "HH:MM"
        city = data.get("city")
        nation = data.get("nation", None) or None # უზრუნველვყოთ None, თუ ცარიელი სტრიქონია

        if not all([name, birthdate_str, birthtime_str, city]):
            missing_fields = [f for f, v in {'name': name, 'birthdate': birthdate_str, 'birthtime': birthtime_str, 'city': city}.items() if not v]
            raise ValueError(f"არასრული მონაცემები ფორმიდან. აკლია: {', '.join(missing_fields)}")

        # თარიღის და დროის გარდაქმნა
        try:
            birth_dt = datetime.strptime(f"{birthdate_str} {birthtime_str}", "%Y-%m-%d %H:%M")
            year = birth_dt.year
            month = birth_dt.month
            day = birth_dt.day
            hour = birth_dt.hour
            minute = birth_dt.minute
        except ValueError:
             raise ValueError("თარიღის ან დროის ფორმატი არასწორია.")

        logger.info(f"Processing AstrologicalSubject for: {name}, {day}/{month}/{year} {hour}:{minute}, {city}, {nation}")

        if not GEONAMES_USERNAME:
            logger.warning("GEONAMES_USERNAME not set. Kerykeion might have issues with city lookup.")

        # --- Kerykeion-ის გამოძახება ---
        # შენიშვნა: Kerykeion-ის გამოძახებამ (განსაკუთრებით ქალაქის ძებნამ GeoNames-ით)
        # შეიძლება გამოიწვიოს blocking I/O. ასინქრონულ გარემოში, როგორიც aiohttp-ია,
        # იდეალურია ასეთი ოპერაციების გაშვება ცალკე thread-ში (მაგ. asyncio.to_thread).
        # ამ ეტაპზე, სიმარტივისთვის, დავტოვოთ პირდაპირ გამოძახება.
        try:
            subject_instance = AstrologicalSubject(name, year, month, day, hour, minute, city, nation=nation, kerykeion_username=GEONAMES_USERNAME)
        except Exception as kerykeion_err:
             logger.error(f"Kerykeion calculation failed for {name}: {kerykeion_err}", exc_info=True)
             # შეგვიძლია გავაგზავნოთ უფრო კონკრეტული შეცდომა მომხმარებელთან
             await processing_message.edit_text(f"ასტროლოგიური მონაცემების გამოთვლისას მოხდა შეცდომა: {kerykeion_err}. შეამოწმეთ ქალაქის სახელი.")
             return # ვწყვეტთ ფუნქციის მუშაობას

        # --- შედეგების ფორმირება ---
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

        logger.info(f"AstrologicalSubject generated successfully for {name}.")
        # ვცვლით "processing" შეტყობინებას საბოლოო პასუხით
        await processing_message.edit_text(text=response_text)

    # --- შეცდომების დაჭერა ---
    except json.JSONDecodeError:
        logger.error(f"Failed to decode JSON from Web App: {data_str}")
        await processing_message.edit_text(text="ვებ აპლიკაციიდან მიღებული მონაცემების დამუშავება ვერ მოხერხდა (JSON შეცდომა).")
    except ValueError as ve:
        logger.error(f"Data processing error: {ve}")
        await processing_message.edit_text(text=f"მონაცემების დამუშავების შეცდომა: {ve}. გთხოვთ, შეამოწმოთ შეყვანილი მონაცემები ფორმაში.")
    except ConnectionError as ce: # ეს შეიძლება მოხდეს Kerykeion-ის GeoNames-თან დაკავშირებისას
        logger.error(f"Kerykeion ConnectionError: {ce}")
        await processing_message.edit_text(text=f"Kerykeion კავშირის შეცდომა (სავარაუდოდ GeoNames): {ce}. შეამოწმეთ ინტერნეტ კავშირი ან სცადეთ მოგვიანებით.")
    except Exception as e:
        logger.error(f"An unexpected error occurred processing web app data for chat_id {chat_id}: {e}", exc_info=True)
        # ვცდილობთ შეცდომის შეტყობინების რედაქტირებას, თუ შესაძლებელია
        try:
            await processing_message.edit_text(text=f"მოულოდნელი შეცდომა მოხდა მონაცემების დამუშავებისას.")
        except Exception: # თუ საწყისი შეტყობინების რედაქტირებაც ვერ ხერხდება
             await context.bot.send_message(chat_id=chat_id, text="მოულოდნელი შეცდომა მოხდა მონაცემების დამუშავებისას.")


# --- Webhook და Web Server-ის ლოგიკა ---

async def telegram_webhook_handler(request: web.Request):
    """Telegram-ისგან მიღებული განახლებების დამმუშავებელი"""
    application = request.app['bot_app']
    try:
        update_data = await request.json()
        update = TelegramUpdate.de_json(update_data, application.bot)
        logger.info(f"Received update via webhook: {update.update_id}")
        # განახლების ასინქრონულად დამუშავება, რათა Telegram-ს სწრაფად ვუპასუხოთ 200 OK
        asyncio.create_task(application.process_update(update))
        return web.Response(status=200) # დაუყოვნებლივ ვაბრუნებთ 200 OK-ს
    except json.JSONDecodeError:
         logger.error("Webhook received non-JSON data.")
         return web.Response(status=400, text="Invalid JSON")
    except Exception as e:
        logger.error(f"Error processing update from webhook: {e}", exc_info=True)
        return web.Response(status=500)

async def health_check_handler(request: web.Request):
    """Render/Railway-სთვის health check წერტილი"""
    return web.Response(text="OK", status=200)

async def setup_bot_and_webhook(application: Application):
    """Sets up the bot webhook (listening on root path /) based on environment variables."""
    # ვეძებთ BOT_PUBLIC_URL ცვლადს, რომელსაც ხელით ვამატებთ ჰოსტინგზე
    base_url = os.environ.get('BOT_PUBLIC_URL')
    webhook_path = "/" # ვუსმენთ ძირითად მისამართზე

    if not base_url:
        logger.warning("Cannot determine base URL (BOT_PUBLIC_URL environment variable not set). Webhook will NOT be set.")
        return None # ვერ ვაყენებთ webhook-ს

    # დავრწმუნდეთ, რომ base_url-ს ბოლოში / არ აქვს, რადგან Telegram-ს პირდაპირ აქ ვუთითებთ
    webhook_target_url = base_url.rstrip('/')

    logger.info(f"Attempting to set webhook to target URL: {webhook_target_url}")
    try:
        await application.bot.set_webhook(
            url=webhook_target_url, # Telegram-ი გამოაგზავნის POST-ს პირდაპირ აქ
            allowed_updates=["message"], # ჩვენ გვჭირდება მხოლოდ message განახლებები (მათ შორის web_app_data)
            drop_pending_updates=True # ვუთხრათ Telegram-ს, რომ წაშალოს დაგროვილი განახლებები (თუ ბოტი გათიშული იყო)
            # secret_token="YOUR_SECRET_TOKEN" # დამატებითი უსაფრთხოებისთვის
        )
        logger.info(f"Webhook successfully set to {webhook_target_url}")
        return webhook_path # ვაბრუნებთ "/" გზას aiohttp როუტერისთვის
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}", exc_info=True)
        return None

async def main_async() -> None:
    """Sets up the bot, webhook, and starts the web server."""
    if not TELEGRAM_BOT_TOKEN:
        logger.critical("Error: TELEGRAM_BOT_TOKEN environment variable not set. Exiting.")
        return

    logger.info("Creating application...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # ბრძანებების და WebApp handler-ის რეგისტრაცია
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("natalchart", natal_chart_webapp))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))
    logger.info("Command handlers registered (start, natalchart) and WebApp handler.")

    # აპლიკაციის ინიციალიზაცია
    logger.info("Initializing application...")
    await application.initialize()
    logger.info("Application initialized.")

    # Webhook-ის დაყენების მცდელობა
    webhook_path = await setup_bot_and_webhook(application)

    # aiohttp ვებ სერვერის შექმნა
    aiohttp_app = web.Application()
    aiohttp_app['bot_app'] = application # ვინახავთ application ობიექტს, რომ handler-მა გამოიყენოს

    # Handler-ების დამატება კონკრეტულ მისამართებზე
    if webhook_path: # Webhook handler-ი ემატება მხოლოდ თუ webhook წარმატებით დაყენდა
         aiohttp_app.router.add_post(webhook_path, telegram_webhook_handler) # დაემატება "/" მისამართზე
         logger.info(f"Webhook handler registered at path: {webhook_path}")
    else:
         logger.warning("Webhook path could not be determined or set. Telegram webhook handler NOT registered.")
    aiohttp_app.router.add_get('/health', health_check_handler) # Health check
    logger.info("Health check handler registered at path: /health")

    # ვებ სერვერის გაშვება
    port = int(os.environ.get('PORT', 8080)) # Railway/Render იყენებს PORT ცვლადს, ლოკალურად 8080 იყოს default
    runner = web.AppRunner(aiohttp_app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=port)

    logger.info(f"Starting web server on host 0.0.0.0 port {port}...")
    await site.start()

    # ბოტის შიდა პროცესების გაშვება (პოლინგის გარეშე)
    await application.start()
    logger.info("Bot application started in webhook mode.")

    # სერვერის მუშაობის ლოდინი
    await asyncio.Event().wait()

    # Shutdown ლოგიკა
    logger.info("Shutting down...")
    await application.stop()
    await runner.cleanup()
    logger.info("Application stopped.")

# --- სკრიპტის გაშვების წერტილი ---
if __name__ == "__main__":
    load_dotenv()
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        try:
            asyncio.run(main_async())
        except KeyboardInterrupt:
             logger.info("Process interrupted by user (KeyboardInterrupt)")
        except Exception as e:
             logger.critical(f"Application failed to run: {e}", exc_info=True)
    else:
        logger.critical("TELEGRAM_BOT_TOKEN environment variable not found. Bot cannot start.")