import os
import logging
import psycopg2
import threading
from datetime import datetime, timedelta
from dotenv import load_dotenv
from web3 import Web3
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler

# Load environment variables from .env file
load_dotenv()

# Sensitive information loaded from environment variables
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DATABASE_URL = os.getenv("DATABASE_URL")
SHIBARIUM_NODE_URL = os.getenv("SHIBARIUM_NODE_URL")
APP_NAME = os.getenv("APP_NAME")
YOUR_ADMIN_USER_ID = os.getenv("YOUR_ADMIN_USER_ID")

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database setup
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Web3 setup for Shibarium
w3 = Web3(Web3.HTTPProvider(SHIBARIUM_NODE_URL))

# Global thread lock
db_lock = threading.Lock()

# Token distribution constants
MAX_TAPS_PER_DAY = 10
TAP_REWARD = 1000
INITIAL_MINING_POWER = 100
TOTAL_MINING_TOKENS = 5000000000000
BONUS_TOKENS = 500000
TELEGRAM_TWITTER_BONUS = 200000
MAX_REFERRAL_USES = 9
REFERRAL_BONUS_PERCENTAGE = 0.10

# Ensure necessary tables exist
def ensure_tables_exist():
    with db_lock:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id SERIAL PRIMARY KEY,
                wallet_address TEXT UNIQUE,
                taps INTEGER DEFAULT 0,
                mining_power INTEGER DEFAULT 100,
                referral_code TEXT UNIQUE,
                referred_by TEXT,
                referral_count INTEGER DEFAULT 0,
                tap_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                joined_telegram BOOLEAN DEFAULT FALSE,
                followed_twitter BOOLEAN DEFAULT FALSE,
                token_balance BIGINT DEFAULT 0  -- Changed to BIGINT for larger values
            )
        ''')
        conn.commit()

# Command handlers
def start(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("Tap", callback_data='tap')],
        [InlineKeyboardButton("Skip Wallet Connection", callback_data='skip')],
        [InlineKeyboardButton("Connect Wallet", callback_data='connect')],
        [InlineKeyboardButton("Check Balance", callback_data='balance')],
        [InlineKeyboardButton("Invite Friends", callback_data='invite')],
        [InlineKeyboardButton("Join Telegram Group", url='https://www.t.me/shibariumpartnershib')],
        [InlineKeyboardButton("Follow on Twitter", url='https://x.com/partnershib24')],
        [InlineKeyboardButton("View Dashboard", callback_data='dashboard')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_message = "Welcome to PartnerShib Bot! Please choose an option:"
    update.message.reply_text(welcome_message, reply_markup=reply_markup)

def connect(update: Update, context: CallbackContext):
    query = update.callback_query
    query.message.reply_text("Please enter your Shibarium wallet address:")

def skip(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id

    with db_lock:
        cursor.execute(
            "INSERT INTO users (user_id, token_balance) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
            (user_id, BONUS_TOKENS)
        )
        conn.commit()
    
    query.message.reply_text("You have skipped wallet connection. You can still participate in other activities.")

def handle_wallet_address(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    wallet_address = update.message.text

    if not Web3.isAddress(wallet_address):
        update.message.reply_text("Invalid wallet address. Please try again.")
        return

    with db_lock:
        try:
            cursor.execute(
                "UPDATE users SET wallet_address = %s WHERE user_id = %s",
                (wallet_address, user_id)
            )
            conn.commit()
            update.message.reply_text("Wallet connected successfully!")
        except psycopg2.IntegrityError as e:
            conn.rollback()
            logger.error(f"Integrity error: {e}")
            update.message.reply_text("There was an error with connecting your wallet. Please try again.")

def view_dashboard(update: Update, context: CallbackContext):
    user_id = update.callback_query.from_user.id

    with db_lock:
        cursor.execute("SELECT wallet_address, mining_power, token_balance, referral_count, joined_telegram, followed_twitter FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()

    if user:
        wallet_address, mining_power, token_balance, referral_count, joined_telegram, followed_twitter = user
        dashboard_message = (f"Dashboard:\n"
                             f"Wallet Address: {wallet_address if wallet_address else 'Not connected'}\n"
                             f"Mining Power: {mining_power}\n"
                             f"Token Balance: {token_balance}\n"
                             f"Referral Count: {referral_count}\n"
                             f"Joined Telegram: {joined_telegram}\n"
                             f"Followed Twitter: {followed_twitter}")
    else:
        dashboard_message = "You are not registered yet. Please use the /start command to register."

    update.callback_query.message.reply_text(dashboard_message)

def handle_tap(update: Update, context: CallbackContext):
    user_id = update.callback_query.from_user.id

    with db_lock:
        cursor.execute("SELECT taps, tap_timestamp, mining_power FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()

    if user:
        taps, tap_timestamp, mining_power = user
        current_time = datetime.utcnow()

        if taps < MAX_TAPS_PER_DAY or current_time - tap_timestamp > timedelta(days=1):
            new_taps = taps + 1 if taps < MAX_TAPS_PER_DAY else 1
            new_tokens = TAP_REWARD * mining_power

            cursor.execute("UPDATE users SET taps = %s, tap_timestamp = %s, token_balance = token_balance + %s WHERE user_id = %s",
                           (new_taps, current_time, new_tokens, user_id))
            conn.commit()
            update.callback_query.message.reply_text(f"Tapped! You received {new_tokens} tokens.")

            # Update referrer's token balance
            cursor.execute("SELECT referred_by FROM users WHERE user_id = %s", (user_id,))
            referrer = cursor.fetchone()
            if referrer and referrer[0]:
                referrer_code = referrer[0]
                referrer_bonus = new_tokens * REFERRAL_BONUS_PERCENTAGE

                cursor.execute("UPDATE users SET token_balance = token_balance + %s WHERE referral_code = %s",
                               (referrer_bonus, referrer_code))
                conn.commit()
                logger.debug(f"User {user_id}'s referrer {referrer_code} received {referrer_bonus} tokens")

        else:
            update.callback_query.message.reply_text("You have reached the maximum taps for today. Please try again tomorrow.")
            logger.debug(f"User {user_id} reached maximum taps for today")

    else:
        update.callback_query.message.reply_text("You are not registered yet. Please use the /start command to register.")
        logger.debug(f"User {user_id} is not registered")

# Main function to start the bot
def main():
    updater = Updater(token=TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    # Command handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_wallet_address))
    dispatcher.add_handler(CallbackQueryHandler(connect, pattern='connect'))
    dispatcher.add_handler(CallbackQueryHandler(skip, pattern='skip'))
    dispatcher.add_handler(CallbackQueryHandler(handle_tap, pattern='tap'))
    dispatcher.add_handler(CallbackQueryHandler(view_dashboard, pattern='dashboard'))

    # Start the bot
    updater.start_polling()
    logger.debug("Bot started")
    updater.idle()

if __name__ == '__main__':
    ensure_tables_exist()
    main()
