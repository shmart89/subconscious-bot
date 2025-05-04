import logging
import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# .env ფაილიდან გარემოს ცვლადების ჩატვირთვა
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # ჯერ არ ვიყენებთ, მაგრამ ჩავტვირთოთ
ASTROLOGY_API_USER_ID = os.getenv("ASTROLOGY_API_USER_ID") # ჯერ არ ვიყენებთ
ASTROLOGY_API_KEY = os.getenv("ASTROLOGY_API_KEY") # ჯერ არ ვიყენებთ

# ლოგირების ჩართვა (დაგვეხმარება პრობლემების პოვნაში თუ რამე მოხდა)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING) # არასასურველი ლოგების გათიშვა
logger = logging.getLogger(__name__)

# /start ბრძანების ფუნქცია
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /start is issued."""
    user = update.effective_user
    # ვუგზავნით პასუხს მომხმარებელს მისი სახელის გამოყენებით
    await update.message.reply_html(
        rf"გამარჯობა {user.mention_html()}! მე ვარ Subconscious ბოტი.",
    )
    # მოგვიანებით აქ დავამატებთ ღილაკს Web App-ისთვის
    # keyboard = [[KeyboardButton("ასტროლოგიური რუკა", web_app=WebAppInfo(url="YOUR_WEB_APP_URL"))]]
    # reply_markup = ReplyKeyboardMarkup(keyboard)
    # await update.message.reply_text('დააჭირე ღილაკს:', reply_markup=reply_markup)


# მთავარი ფუნქცია, რომელიც ბოტს უშვებს
def main() -> None:
    """Start the bot."""
    # ვქმნით აპლიკაციას და გადავცემთ ჩვენს ტელეგრამ ბოტის ტოკენს .env ფაილიდან
    logger.info("Creating application...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # ვარეგისტრირებთ /start ბრძანების დამმუშავებელს (handler)
    application.add_handler(CommandHandler("start", start))
    logger.info("Start handler registered.")

    # ვუშვებთ ბოტს Polling რეჟიმში (მუდმივად ამოწმებს ახალ შეტყობინებებს)
    logger.info("Starting bot polling...")
    application.run_polling()

# ეს სტანდარტული Python კონსტრუქციაა, რომელიც main ფუნქციას უშვებს სკრიპტის გაშვებისას
if __name__ == "__main__":
    main()