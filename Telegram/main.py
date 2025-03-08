import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path
from threading import Lock
from datetime import datetime
import time
import uuid
import requests
from PIL import Image
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING,
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler('telegram_bot.log', maxBytes=5 * 1024 * 1024, backupCount=3)
    ]
)
logger = logging.getLogger(__name__)

# Check if debug mode is enabled
if os.getenv('BOT_DEBUG', 'false').lower() == 'true':
    logger.setLevel(logging.DEBUG)
    logger.debug("Debug mode enabled")

# Global state
BOT_RUNNING = True
BOT_LOCK = Lock()

# Define the commands and their descriptions
COMMANDS = [
    BotCommand("start", "Start or restart the bot"),
    BotCommand("help", "Show help message"),
    BotCommand("restart", "Restart the bot"),
    BotCommand("generate", "Generate an image from a text prompt"),
]

# PID file to prevent multiple instances
PID_FILE = "bot.pid"


def check_pid_file():
    """Check if a PID file exists and if the process is still running."""
    if Path(PID_FILE).exists():
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, 0)  # Check if the process is running
            return True
        except ProcessLookupError:
            # Process is not running, so remove the PID file
            os.remove(PID_FILE)
            return False
    return False


def create_pid_file():
    """Create a PID file with the current process ID."""
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    """Remove the PID file."""
    if Path(PID_FILE).exists():
        os.remove(PID_FILE)


async def set_commands(application):
    """Register the commands with Telegram."""
    await application.bot.set_my_commands(COMMANDS)


# Function to create the keyboard
def get_keyboard():
    """Generate the inline keyboard."""
    keyboard = [
        [InlineKeyboardButton("Say Hello", callback_data='hello')],
        [InlineKeyboardButton("Get Info", callback_data='info')],
        [InlineKeyboardButton("Stop Bot", callback_data='stop')],
    ]
    return InlineKeyboardMarkup(keyboard)


# Start command handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message with inline buttons when /start is issued."""
    global BOT_RUNNING
    with BOT_LOCK:
        BOT_RUNNING = True
    await send_message_with_keyboard(update, "Welcome to the bot! Choose an option:")
    logger.debug(f"User {update.effective_user.id} started the bot")


# Help command handler
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a help message when /help is issued."""
    if not BOT_RUNNING:
        await update.message.reply_text("The bot is currently stopped. Use /start to begin.")
        return

    help_text = (
        "/start - Start or restart the bot\n"
        "/help - Show this help message\n"
        "/restart - Restart the bot (alternative to /start)\n"
        "/generate <prompt> - Generate an image from a text prompt\n"
        "Use the buttons to interact with me!"
    )
    await send_message_with_keyboard(update, help_text)
    logger.debug(f"User {update.effective_user.id} requested help")


# Restart command handler
async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart the bot functionality."""
    global BOT_RUNNING
    with BOT_LOCK:
        BOT_RUNNING = True
    await send_message_with_keyboard(update, "Bot restarted! Choose an option:")
    logger.debug(f"User {update.effective_user.id} restarted the bot with /restart")


# Handle button presses
async def handle_button_press(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button presses."""
    global BOT_RUNNING
    query = update.callback_query
    await query.answer()

    if not BOT_RUNNING:
        return

    if query.data == 'hello':
        await send_message_with_keyboard(update, "Hello there!")
        logger.debug(f"User {update.effective_user.id} pressed 'Say Hello'")
    elif query.data == 'info':
        await send_message_with_keyboard(update, f"Your user ID is: {query.from_user.id}")
        logger.debug(f"User {update.effective_user.id} pressed 'Get Info'")
    elif query.data == 'stop':
        with BOT_LOCK:
            BOT_RUNNING = False
        await send_message_with_keyboard(update, "Bot stopped. Use /start to begin again.")
        logger.debug(f"User {update.effective_user.id} stopped the bot")


# Echo handler for text messages
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user's message if the bot is running."""
    if not BOT_RUNNING:
        return
    await send_message_with_keyboard(update, update.message.text)
    logger.debug(f"User {update.effective_user.id} sent message: {update.message.text}")


# Helper function to send messages with keyboard
async def send_message_with_keyboard(update: Update, text: str) -> None:
    """Send a message with the default inline keyboard."""
    reply_markup = get_keyboard()

    if update.message:
        # Handle Message updates
        await update.message.reply_text(text, reply_markup=reply_markup)
    elif update.callback_query and update.callback_query.message:
        # Handle CallbackQuery updates
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        logger.error("Unable to send message: No message or accessible callback_query found in update.")


# Error handler
async def error(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and notify the user."""
    logger.error(f"Update {update} caused error {context.error}")

    if update is None:
        logger.error("Error occurred outside of an update context.")
        return

    if update.message:
        # Handle Message updates
        await update.message.reply_text("An error occurred. Please try again later.")
    elif update.callback_query and update.callback_query.message:
        # Handle CallbackQuery updates
        await update.callback_query.message.reply_text("An error occurred. Please try again later.")
    else:
        logger.error("Unable to send error message: No message or accessible callback_query found in update.")


# Function to generate an image using Pollinations AI
def generate_images_pollynation_ai(prompt):
    """Generate an image using Pollinations AI and return the image content."""
    prompt += "full-body-view,cowboy-shot,realistic"
    prompt += str(uuid.uuid4())  # Ensure unique prompts to bypass caching
    formatted_prompt = prompt.replace(" ", "-")
    url = f"https://image.pollinations.ai/prompt/{formatted_prompt}"

    while True:
        try:
            response = requests.get(url)
            if response.status_code == 200:
                # Open the image and crop it
                image = Image.open(BytesIO(response.content))
                image = image.crop((0, 0, image.width, image.height - 48))  # Crop watermark
                cropped_image = image.crop((0, 0, image.width, image.height - 100))  # Crop artifacts

                # Save the cropped image to a BytesIO object
                image_bytes = BytesIO()
                cropped_image.save(image_bytes, format="PNG")
                image_bytes.seek(0)  # Reset the stream position

                return image_bytes
            else:
                print(f"Retrying... Status code: {response.status_code}")
        except requests.RequestException as e:
            print(f"Request failed: {e}. Retrying...")
        time.sleep(5)  # Avoid rate-limiting


# Command handler for generating images
async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate an image from a text prompt and send it to the user."""
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("Please provide a text prompt. Usage: /generate <prompt>")
        return

    await update.message.reply_text("Generating your image... Please wait.")

    try:
        # Generate the image
        image_bytes = generate_images_pollynation_ai(prompt)

        # Send the image as a reply
        await update.message.reply_photo(photo=image_bytes)
    except Exception as e:
        logger.error(f"Error generating image: {e}")
        await update.message.reply_text("An error occurred while generating the image. Please try again later.")


# Main function to run the bot
def main() -> None:
    """Start the bot."""
    if check_pid_file():
        logger.error("Another instance of the bot is already running. Exiting.")
        sys.exit(1)

    create_pid_file()

    try:
        BOT_TOKEN = os.getenv('BOT_TOKEN')
        if not BOT_TOKEN:
            raise ValueError("No BOT_TOKEN environment variable set")

        # Create the Application with post_init
        application = (
            Application.builder()
            .token(BOT_TOKEN)
            .post_init(set_commands)  # Register commands after initialization
            .build()
        )

        # Register command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("restart", restart))
        application.add_handler(CommandHandler("generate", generate_image))

        # Add button handler
        application.add_handler(CallbackQueryHandler(handle_button_press))

        # Add message handler
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

        # Add error handler
        application.add_error_handler(error)

        # Start the bot
        logger.debug("Bot is starting...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        remove_pid_file()


if __name__ == '__main__':
    main()