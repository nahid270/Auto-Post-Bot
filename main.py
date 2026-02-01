# -*- coding: utf-8 -*-

# ==============================================================================
# 🎬 ULTIMATE MOVIE BOT - FINAL UPDATED VERSION (MANUAL FIX + CLEAN CAPTION)
# ==============================================================================
# Features Included:
# 1. TMDB & IMDb Link Search Logic
# 2. DEDICATED MANUAL POST COMMAND (/manual) - Clean & Accurate
# 3. Face Detection & Watermarking
# 4. Log Channel Backup System
# 5. URL Shortener Integration
# 6. Auto Delete Timer
# 7. Custom Button / Episode Support
# 8. Tutorial Link Support
# 9. Manual Channel Selection for Posting
# ==============================================================================

# ---- Core Python Imports ----
import os
import io
import re
import asyncio
import logging
import secrets
import string
import time
from threading import Thread
from datetime import datetime

# --- Third-party Library Imports ---
import requests
from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from pyrogram.errors import UserNotParticipant, FloodWait
from flask import Flask
from dotenv import load_dotenv
import motor.motor_asyncio
import numpy as np
import cv2  # OpenCV for Face Detection

# ==============================================================================
# 1. CONFIGURATION AND SETUP
# ==============================================================================
load_dotenv()

# Telegram Config
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

# External APIs
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

# Channels & Admin
FORCE_SUB_CHANNEL = os.getenv("FORCE_SUB_CHANNEL")
INVITE_LINK = os.getenv("INVITE_LINK")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID")) 

# Database Configuration
DB_URI = os.getenv("DATABASE_URI")
DB_NAME = os.getenv("DATABASE_NAME", "MovieBotDB")

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Check Database Connection
if not DB_URI:
    logger.critical("CRITICAL: DATABASE_URI is not set. Bot cannot start.")
    exit()

# Initialize MongoDB Client
db_client = motor.motor_asyncio.AsyncIOMotorClient(DB_URI)
db = db_client[DB_NAME]
users_collection = db.users
files_collection = db.files 

# Global Variables for Session Management
user_conversations = {}
BOT_USERNAME = ""

# Initialize Pyrogram Client
bot = Client(
    "UltimateMovieBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ==============================================================================
# FLASK KEEP-ALIVE SERVER (For 24/7 Hosting)
# ==============================================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Bot is Running Successfully!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# Start Flask in a separate thread
Thread(target=run_flask, daemon=True).start()


# ==============================================================================
# 2. HELPER FUNCTIONS & UTILITIES
# ==============================================================================

async def get_bot_username():
    """Fetch and cache the bot username to avoid repeated API calls."""
    global BOT_USERNAME
    if not BOT_USERNAME:
        me = await bot.get_me()
        BOT_USERNAME = me.username
    return BOT_USERNAME

def generate_random_code(length=8):
    """Generate a random alphanumeric code for deep links."""
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))

async def auto_delete_message(client, chat_id, message_id, delay_seconds):
    """Background task to delete a message after a specific delay."""
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
        try:
            await client.delete_messages(chat_id, message_id)
        except Exception as e:
            logger.warning(f"Failed to auto-delete message: {e}")

# --- Resource Downloaders (Fonts & Face Cascades) ---

def download_cascade():
    """Download OpenCV Face Cascade file if not exists."""
    cascade_file = "haarcascade_frontalface_default.xml"
    if not os.path.exists(cascade_file):
        logger.info("Downloading Face Cascade XML...")
        url = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            with open(cascade_file, 'wb') as f:
                f.write(r.content)
        except Exception as e:
            logger.error(f"Failed to download cascade: {e}")
            return None
    return cascade_file

def download_font():
    """Download Custom Font if not exists."""
    font_file = "HindSiliguri-Bold.ttf"
    if not os.path.exists(font_file):
        logger.info("Downloading Font...")
        url = "https://github.com/google/fonts/raw/main/ofl/hindsiliguri/HindSiliguri-Bold.ttf"
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            with open(font_file, 'wb') as f:
                f.write(r.content)
        except Exception as e:
            logger.error(f"Failed to download font: {e}")
            return None
    return font_file

# --- Database Helpers ---

async def add_user_to_db(user):
    """Add or Update user in MongoDB."""
    await users_collection.update_one(
        {'_id': user.id},
        {
            '$set': {'first_name': user.first_name},
            '$setOnInsert': {'is_premium': False, 'delete_timer': 0}
        },
        upsert=True
    )

async def is_user_premium(user_id: int) -> bool:
    """Check if a user is premium."""
    if user_id == OWNER_ID:
        return True
    user_data = await users_collection.find_one({'_id': user_id})
    if user_data:
        return user_data.get('is_premium', False)
    return False

async def shorten_link(user_id: int, long_url: str):
    """Shorten a URL using the user's API settings."""
    user_data = await users_collection.find_one({'_id': user_id})
    
    # Check if user has setup shortener
    if not user_data or 'shortener_api' not in user_data or 'shortener_url' not in user_data:
        return long_url # Return original if API not set

    api_key = user_data['shortener_api']
    base_url = user_data['shortener_url']
    api_url = f"https://{base_url}/api?api={api_key}&url={long_url}"
    
    try:
        response = requests.get(api_url, timeout=10)
        data = response.json()
        if data.get("status") == "success" and data.get("shortenedUrl"):
            return data["shortenedUrl"]
        else:
            return long_url
    except Exception as e:
        logger.error(f"Shortener Error: {e}")
        return long_url

# ==============================================================================
# 3. DECORATORS (Access Control)
# ==============================================================================

def force_subscribe(func):
    """Decorator to enforce channel subscription."""
    async def wrapper(client, message):
        if FORCE_SUB_CHANNEL:
            try:
                chat_id = int(FORCE_SUB_CHANNEL) if FORCE_SUB_CHANNEL.startswith("-100") else FORCE_SUB_CHANNEL
                await client.get_chat_member(chat_id, message.from_user.id)
            except UserNotParticipant:
                join_link = INVITE_LINK or f"https://t.me/{FORCE_SUB_CHANNEL.replace('@', '')}"
                
                if len(message.command) > 1:
                    start_arg = message.command[1]
                    bot_uname = await get_bot_username()
                    return await message.reply_text(
                        "🔒 **Protected Content!**\n\nYou must join our channel to access this file.", 
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("👉 Join Channel", url=join_link)],
                            [InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{bot_uname}?start={start_arg}")]
                        ])
                    )
                else:
                    return await message.reply_text(
                        "❗ **You must join our channel to use this bot.**", 
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👉 Join Channel", url=join_link)]])
                    )
            except Exception as e:
                logger.error(f"Force Sub Error: {e}")
                pass 
        await func(client, message)
    return wrapper

def check_premium(func):
    """Decorator to restrict features to Premium users."""
    async def wrapper(client, message):
        if await is_user_premium(message.from_user.id):
            await func(client, message)
        else:
            await message.reply_text(
                "⛔ **Access Denied!**\n\nThis is a **Premium Feature**.\nPlease contact Admin to purchase.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👑 Contact Admin", user_id=OWNER_ID)]
                ])
            )
    return wrapper

# ==============================================================================
# 4. IMAGE PROCESSING & CAPTION GENERATION
# ==============================================================================

def watermark_poster(poster_input, watermark_text: str, badge_text: str = None):
    """
    Advanced poster editor:
    - Adds text watermark (bottom).
    - Adds custom badge (top) with face detection.
    - UPDATED: Handles URL, Local File Path, or BytesIO.
    """
    if not poster_input:
        return None, "Poster not found."
    
    try:
        original_img = None
        # Load Image from URL, File Path, or Bytes
        if isinstance(poster_input, str):
            if poster_input.startswith("http"): # It's a URL
                img_data = requests.get(poster_input, timeout=15).content
                original_img = Image.open(io.BytesIO(img_data)).convert("RGBA")
            else: # It's a Local File Path
                if os.path.exists(poster_input):
                    original_img = Image.open(poster_input).convert("RGBA")
                else:
                    return None, f"Local file not found: {poster_input}"
        else: # It's BytesIO or File Object
            original_img = Image.open(poster_input).convert("RGBA")
            
        if not original_img:
            return None, "Failed to load image."
        
        img = Image.new("RGBA", original_img.size)
        img.paste(original_img)
        draw = ImageDraw.Draw(img)

        # ---- Badge Logic (with Gradient & Face Detect) ----
        if badge_text:
            badge_font_size = int(img.width / 9)
            font_path = download_font()
            try:
                badge_font = ImageFont.truetype(font_path, badge_font_size) if font_path else ImageFont.load_default()
            except:
                badge_font = ImageFont.load_default()

            # Calculate Text Size
            bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            x = (img.width - text_width) / 2
            
            # Smart Positioning (Face Detect)
            y_pos = img.height * 0.03
            cascade_path = download_cascade()
            
            if cascade_path:
                try:
                    cv_image = np.array(original_img.convert('RGB'))
                    gray = cv2.cvtColor(cv_image, cv2.COLOR_RGB2GRAY)
                    face_cascade = cv2.CascadeClassifier(cascade_path)
                    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
                    
                    # Check collision
                    padding = int(badge_font_size * 0.2)
                    text_box_y1 = y_pos + text_height + padding
                    
                    is_collision = False
                    for (fx, fy, fw, fh) in faces:
                        if y_pos < (fy + fh) and text_box_y1 > fy:
                            is_collision = True
                            break
                    
                    if is_collision:
                        y_pos = img.height * 0.25  # Move down if face detected at top
                except Exception as e:
                    logger.error(f"Face detection failed: {e}")

            y = y_pos
            padding = int(badge_font_size * 0.15)
            
            # Draw Semi-transparent Background for Badge
            rect_layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
            rect_draw = ImageDraw.Draw(rect_layer)
            rect_draw.rectangle(
                (x - padding, y - padding, x + text_width + padding, y + text_height + padding),
                fill=(0, 0, 0, 160)
            )
            img = Image.alpha_composite(img, rect_layer)
            draw = ImageDraw.Draw(img)

            # Draw Gradient Text
            gradient = Image.new('RGBA', (text_width, text_height + int(padding)), (0, 0, 0, 0))
            gradient_draw = ImageDraw.Draw(gradient)
            
            start_color = (255, 255, 0) # Yellow
            end_color = (255, 69, 0)   # OrangeRed
            
            for i in range(text_width):
                ratio = i / text_width
                r = int(start_color[0] * (1 - ratio) + end_color[0] * ratio)
                g = int(start_color[1] * (1 - ratio) + end_color[1] * ratio)
                b = int(start_color[2] * (1 - ratio) + end_color[2] * ratio)
                gradient_draw.line([(i, 0), (i, text_height + padding)], fill=(r, g, b, 255))
            
            mask = Image.new('L', (text_width, text_height + int(padding)), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.text((0, 0), badge_text, font=badge_font, fill=255)
            
            try:
                img.paste(gradient, (int(x), int(y)), mask)
            except:
                draw.text((x, y), badge_text, font=badge_font, fill="white")

        # ---- Watermark Logic ----
        if watermark_text:
            font_size = int(img.width / 12)
            try:
                font = ImageFont.truetype(download_font(), font_size)
            except:
                font = ImageFont.load_default()
            
            bbox = draw.textbbox((0, 0), watermark_text, font=font)
            text_width = bbox[2] - bbox[0]
            wx = (img.width - text_width) / 2
            wy = img.height - bbox[3] - (img.height * 0.05)
            
            # Shadow
            draw.text((wx + 2, wy + 2), watermark_text, font=font, fill=(0, 0, 0, 128))
            # Text
            draw.text((wx, wy), watermark_text, font=font, fill=(255, 255, 255, 200))
            
        buffer = io.BytesIO()
        buffer.name = "poster.png"
        img.convert("RGB").save(buffer, "PNG")
        buffer.seek(0)
        return buffer, None

    except Exception as e:
        logger.error(f"Watermark Error: {e}")
        return None, str(e)

# --- TMDB & IMDb Functions ---

def search_tmdb(query: str):
    """Search TMDB for movies/tv shows by text."""
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={query}&include_adult=true"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        return [res for res in results if res.get("media_type") in ["movie", "tv"]][:5]
    except Exception as e:
        logger.error(f"TMDB Search Error: {e}")
        return []

def search_by_imdb(imdb_id: str):
    """Find media on TMDB using IMDb ID (ttxxxxxxx)."""
    url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={TMDB_API_KEY}&external_source=imdb_id"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        results = []
        # Combine results from movie and tv
        for item in data.get("movie_results", []):
            item['media_type'] = 'movie'
            results.append(item)
        for item in data.get("tv_results", []):
            item['media_type'] = 'tv'
            results.append(item)
        return results
    except Exception as e:
        logger.error(f"IMDb Find Error: {e}")
        return []

def get_tmdb_details(media_type, media_id):
    """Get full details for a specific media."""
    url = f"https://api.themoviedb.org/3/{media_type}/{media_id}?api_key={TMDB_API_KEY}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"TMDB Details Error: {e}")
        return None

# ==============================================================================
# 🆕 UPDATED CAPTION GENERATOR (MANUAL MODE FIX)
# ==============================================================================
async def generate_channel_caption(data: dict, language: str, short_links: dict, is_manual: bool = False):
    """
    Generates the text caption.
    If is_manual is True, it DOES NOT add the promotional footer.
    """
    title = data.get("title") or data.get("name") or "Movie"
    date = data.get("release_date") or data.get("first_air_date") or "----"
    year = date[:4]
    rating_val = data.get('vote_average', 0)
    if rating_val:
        rating = f"{rating_val:.1f}"
    else:
        rating = "N/A"
    
    if isinstance(data.get("genres"), list) and len(data["genres"]) > 0:
        genre_str = ", ".join([g["name"] for g in data.get("genres", [])[:3]])
    else:
        genre_str = "N/A"

    # Base Info (Clean Caption)
    caption = f"""🎬 **{title} ({year})**
━━━━━━━━━━━━━━━━━━━━━━━
⭐ **Rating:** {rating}/10
🎭 **Genre:** {genre_str}
🔊 **Language:** {language}
━━━━━━━━━━━━━━━━━━━━━━━"""

    # 🛑 CHECK: If Manual Post, Return ONLY this info. No footer.
    if is_manual:
        return caption

    # If Automatic (TMDB) Post, Add Promotional Text
    caption += """
👀 𝗪𝗔𝗧𝗖𝗛 𝗢𝗡𝗟𝗜𝗡𝗘/📤𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗
👇  ℍ𝕚𝕘𝕙 𝕊𝕡𝕖𝕖𝕕 | ℕ𝕠 𝔹𝕦𝕗𝕗𝕖𝕣𝕚𝕟𝕘  👇"""

    footer = """\n\nMovie ReQuest Group 
👇👇👇
https://t.me/Terabox_search_group

Premium Backup Group link 👇👇👇
https://t.me/+GL_XAS4MsJg4ODM1"""
    
    return caption + footer

# ==============================================================================
# 5. BOT COMMAND HANDLERS
# ==============================================================================

@bot.on_message(filters.command("start") & filters.private)
@force_subscribe
async def start_cmd(client, message: Message):
    user = message.from_user
    uid = user.id
    await add_user_to_db(user)
    
    # ------------------------------------------------------------------
    # 🚀 FILE RETRIEVAL SYSTEM
    # ------------------------------------------------------------------
    if len(message.command) > 1:
        code = message.command[1]
        
        # 1. Check Database for File Info
        file_data = await files_collection.find_one({"code": code})
        
        if file_data:
            msg = await message.reply_text("📂 **Fetching your file...**\nPlease wait...")
            
            # 2. Get Info
            log_msg_id = file_data.get("log_msg_id")
            caption = file_data.get("caption", "🎬 **Movie File**")
            timer = file_data.get("delete_timer", 0)

            try:
                sent_msg = None
                
                # METHOD A: Send by File ID
                try:
                    sent_msg = await client.send_cached_media(
                        chat_id=uid,
                        file_id=file_data["file_id"],
                        caption=caption
                    )
                except Exception as e:
                    logger.warning(f"Failed to send cached media: {e}. Trying fallback.")
                    sent_msg = None

                # METHOD B: Copy from Log Channel
                if not sent_msg and LOG_CHANNEL_ID and log_msg_id:
                    sent_msg = await client.copy_message(
                        chat_id=uid,
                        from_chat_id=LOG_CHANNEL_ID,
                        message_id=log_msg_id,
                        caption=caption
                    )
                
                if sent_msg:
                    await msg.delete()
                    # 3. Handle Auto Delete
                    if timer > 0:
                        asyncio.create_task(auto_delete_message(client, uid, sent_msg.id, timer))
                        await client.send_message(uid, f"⚠️ **Auto-Delete Enabled!**\n\nThis file will be deleted in **{int(timer/60)} minutes**.\nForward it to Saved Messages now!")
                else:
                    await msg.edit_text("❌ **Error:** Could not retrieve file. It might have been deleted from the Log Channel.")

            except Exception as e:
                logger.error(f"File Send Error: {e}")
                await msg.edit_text(f"❌ **Error:** {e}")
        else:
            await message.reply_text("❌ **Link Expired or Invalid.**")
        return

    # ------------------------------------------------------------------
    # NORMAL START MENU
    # ------------------------------------------------------------------
    if uid in user_conversations:
        del user_conversations[uid]
        
    is_premium = await is_user_premium(uid)
    
    if uid == OWNER_ID:
        # Admin Menu
        welcome_text = f"👑 **Welcome Boss!**\n\n**Admin Control Panel:**"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
             InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
            [InlineKeyboardButton("➕ Add Premium", callback_data="admin_add_premium"),
             InlineKeyboardButton("➖ Remove Premium", callback_data="admin_rem_premium")],
             [InlineKeyboardButton("⚙️ Setup Instructions", callback_data="api_help")]
        ])
    else:
        # User Menu
        status_text = "💎 **Premium User**" if is_premium else "👤 **Free User**"
        welcome_text = f"👋 **Hello {user.first_name}!**\n\nYour Status: {status_text}\n\n👇 **Available Commands:**\n`/post` - Auto TMDB Post (Premium)\n`/manual` - Manual Post (Free for All)"
        
        user_buttons = [[InlineKeyboardButton("👤 My Account", callback_data="my_account")]]
        if not is_premium:
            user_buttons.insert(0, [InlineKeyboardButton("💎 Buy Premium Access", user_id=OWNER_ID)])
            
        buttons = InlineKeyboardMarkup(user_buttons)

    await message.reply_text(welcome_text, reply_markup=buttons)

# --- Callback Query Handler (Admin & Menu) ---

@bot.on_callback_query(filters.regex(r"^(admin_|my_account|api_help)"))
async def callback_handler(client, cb: CallbackQuery):
    data = cb.data
    uid = cb.from_user.id
    
    if data == "my_account":
        status = "Premium 💎" if await is_user_premium(uid) else "Free 👤"
        await cb.answer(f"User: {cb.from_user.first_name}\nStatus: {status}", show_alert=True)
        
    elif data == "api_help":
        help_text = (
            "**⚙️ Bot Setup Commands:**\n\n"
            "1. `/setapi <your_api_key>` - Set Shortener.\n"
            "2. `/setwatermark <text>` - Set watermark.\n"
            "3. `/settutorial <link>` - Set Tutorial Link (YouTube/Telegram).\n"
            "4. `/addchannel <id>` - Add channel."
        )
        await cb.answer(help_text, show_alert=True)
        
    elif data.startswith("admin_") and uid == OWNER_ID:
        if data == "admin_stats":
            total = await users_collection.count_documents({})
            prem = await users_collection.count_documents({'is_premium': True})
            await cb.answer(f"📊 Total Users: {total}\n💎 Premium: {prem}", show_alert=True)
            
        elif data == "admin_broadcast":
            await cb.message.edit_text("📢 **Broadcast Mode**\n\nSend the message you want to broadcast to all users.")
            user_conversations[uid] = {"state": "admin_broadcast_wait"}
            
        elif "add_premium" in data:
            await cb.message.edit_text("➕ **Add Premium**\n\nSend the User ID.")
            user_conversations[uid] = {"state": "admin_add_prem_wait"}
            
        elif "rem_premium" in data:
            await cb.message.edit_text("➖ **Remove Premium**\n\nSend the User ID.")
            user_conversations[uid] = {"state": "admin_rem_prem_wait"}

# --- Settings Commands ---

@bot.on_message(filters.command(["setwatermark", "setapi", "setdomain", "settimer", "addchannel", "delchannel", "mychannels", "settutorial"]) & filters.private)
@force_subscribe
async def settings_commands(client, message: Message):
    cmd = message.command[0].lower()
    uid = message.from_user.id
    
    if cmd == "setwatermark":
        text = " ".join(message.command[1:])
        if text:
            await users_collection.update_one({'_id': uid}, {'$set': {'watermark_text': text}}, upsert=True)
            await message.reply_text(f"✅ Watermark set to: `{text}`")
        else:
            await message.reply_text("❌ Usage: `/setwatermark Your Text`")

    elif cmd == "setapi":
        if len(message.command) > 1:
            await users_collection.update_one({'_id': uid}, {'$set': {'shortener_api': message.command[1]}}, upsert=True)
            await message.reply_text("✅ API Key Saved.")
        else:
            await message.reply_text("❌ Usage: `/setapi YOUR_KEY`")

    elif cmd == "setdomain":
        if len(message.command) > 1:
            await users_collection.update_one({'_id': uid}, {'$set': {'shortener_url': message.command[1]}}, upsert=True)
            await message.reply_text("✅ Domain Saved.")
        else:
            await message.reply_text("❌ Usage: `/setdomain shrinkme.io`")
            
    # 🆕 UPDATED TUTORIAL SETTING COMMAND (LINK BASED)
    elif cmd == "settutorial":
        if len(message.command) > 1:
            link = message.command[1]
            if link.startswith("http"):
                await users_collection.update_one({'_id': uid}, {'$set': {'tutorial_url': link}}, upsert=True)
                await message.reply_text(f"✅ **Tutorial Link Saved!**\nLink: {link}")
            else:
                await message.reply_text("❌ Invalid Link! Must start with `http` or `https`.")
        else:
            await message.reply_text("❌ **Usage:** `/settutorial https://t.me/your_post_link`\nor `/settutorial https://youtube.com/watch?v=...`")

    elif cmd == "settimer":
        if len(message.command) > 1:
            try:
                mins = int(message.command[1])
                secs = mins * 60
                await users_collection.update_one({'_id': uid}, {'$set': {'delete_timer': secs}}, upsert=True)
                await message.reply_text(f"✅ Auto-Delete Timer set to: **{mins} Minutes**")
            except:
                await message.reply_text("❌ Usage: `/settimer 10` (for 10 minutes)")
        else:
            await users_collection.update_one({'_id': uid}, {'$set': {'delete_timer': 0}})
            await message.reply_text("✅ Auto-Delete DISABLED.")

    elif cmd == "addchannel":
        if len(message.command) > 1:
            cid = message.command[1]
            await users_collection.update_one({'_id': uid}, {'$addToSet': {'channel_ids': cid}}, upsert=True)
            await message.reply_text(f"✅ Channel `{cid}` added.")
        else:
            await message.reply_text("❌ Usage: `/addchannel -100xxxxx`")

    elif cmd == "delchannel":
        if len(message.command) > 1:
            cid = message.command[1]
            await users_collection.update_one({'_id': uid}, {'$pull': {'channel_ids': cid}})
            await message.reply_text(f"✅ Channel `{cid}` removed.")

    elif cmd == "mychannels":
        data = await users_collection.find_one({'_id': uid})
        channels = data.get('channel_ids', [])
        if channels:
            await message.reply_text(f"📋 **Saved Channels:**\n" + "\n".join([f"`{c}`" for c in channels]))
        else:
            await message.reply_text("❌ No channels saved.")

# ==============================================================================
# 6. POST CREATION FLOW (TMDB + IMDb)
# ==============================================================================

@bot.on_message(filters.command("post") & filters.private)
@force_subscribe
@check_premium
async def post_search_cmd(client, message: Message):
    if len(message.command) == 1:
        return await message.reply_text("**Usage:**\n1. `/post Movie Name`\n2. `/post IMDb-Link/ID` (e.g. tt1234567)")
    
    query = " ".join(message.command[1:]).strip()
    msg = await message.reply_text(f"🔍 **Searching...**\n`{query}`")
    
    results = []
    
    # Check: Is it an IMDb ID/Link?
    imdb_match = re.search(r'tt\d+', query)
    
    if imdb_match:
        # Search via IMDb ID
        imdb_id = imdb_match.group(0)
        results = search_by_imdb(imdb_id)
    else:
        # Search via Name
        results = search_tmdb(query)
    
    buttons = []
    for r in results:
        m_type = r.get('media_type', 'movie')
        title = r.get('title') or r.get('name')
        year = (r.get('release_date') or r.get('first_air_date') or '----')[:4]
        buttons.append([InlineKeyboardButton(f"🎬 {title} ({year})", callback_data=f"sel_{m_type}_{r['id']}")])
    
    if not results:
        await msg.edit_text("❌ **No TMDB results found!**\nTry searching manually or check spelling.")
    else:
        await msg.edit_text(f"👇 **Found {len(results)} Result(s):**", reply_markup=InlineKeyboardMarkup(buttons))

# ==============================================================================
# 🆕 6.1 DEDICATED MANUAL POST COMMAND (OPEN FOR ALL)
# ==============================================================================

@bot.on_message(filters.command("manual") & filters.private)
@force_subscribe
async def manual_cmd_start(client, message: Message):
    """
    Dedicated Manual Post Entry Point.
    No premium check required here.
    """
    await message.reply_text(
        "📝 **Manual Post Creation**\n\nWhat are you uploading?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 Movie", callback_data="manual_type_movie"),
             InlineKeyboardButton("📺 Web Series", callback_data="manual_type_tv")]
        ])
    )

@bot.on_callback_query(filters.regex("^manual_type_"))
async def manual_type_handler(client, cb: CallbackQuery):
    m_type = cb.data.split("_")[2]
    uid = cb.from_user.id
    
    # Initialize session for Manual Post
    user_conversations[uid] = {
        "details": {"media_type": m_type},
        "links": {},
        "state": "wait_manual_title",
        "is_manual": True # Flag set for Clean Caption
    }
    await cb.message.edit_text(f"📝 **Step 1:** Send the **Title** of the {m_type}.")

# ==============================================================================
# 7. CALLBACK HANDLERS & UPLOAD PANEL
# ==============================================================================

@bot.on_callback_query(filters.regex("^sel_"))
async def media_selected(client, cb: CallbackQuery):
    _, m_type, mid = cb.data.split("_")
    details = get_tmdb_details(m_type, mid)
    
    if not details:
        return await cb.answer("Error fetching details!", show_alert=True)
    
    uid = cb.from_user.id
    user_conversations[uid] = {
        "details": details,
        "links": {},
        "state": "wait_lang",
        "is_manual": False
    }
    
    langs = [["English", "Hindi"], ["Bengali", "Dual Audio"], ["Tamil", "Telugu"]]
    buttons = [[InlineKeyboardButton(l, callback_data=f"lang_{l}") for l in row] for row in langs]
    
    await cb.message.edit_text(
        f"✅ Selected: **{details.get('title') or details.get('name')}**\n\n🌐 **Select Language:**",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

@bot.on_callback_query(filters.regex("^lang_"))
async def language_selected(client, cb: CallbackQuery):
    lang = cb.data.split("_")[1]
    uid = cb.from_user.id
    user_conversations[uid]["language"] = lang
    await show_upload_panel(cb.message, uid)

async def show_upload_panel(message, uid):
    """Shows the panel to upload files for different qualities."""
    buttons = [
        [InlineKeyboardButton("📤 Upload 480p", callback_data="up_480p")],
        [InlineKeyboardButton("📤 Upload 720p", callback_data="up_720p")],
        [InlineKeyboardButton("📤 Upload 1080p", callback_data="up_1080p")],
        # Custom Episode Button
        [InlineKeyboardButton("➕ Add Episode / Custom Button", callback_data="add_custom_btn")],
        [InlineKeyboardButton("🇧🇩 Badge: Bangla", callback_data="bdg_bangla"),
         InlineKeyboardButton("⏭ Skip Badge", callback_data="bdg_skip")],
        [InlineKeyboardButton("✅ FINISH & POST", callback_data="proc_final")]
    ]
    
    links = user_conversations[uid].get('links', {})
    badge = user_conversations[uid].get('temp_badge_text', 'Not Set')
    
    status_text = "\n".join([f"✅ **{k}** Added" for k in links.keys()])
    if not status_text: status_text = "No files added yet."
    
    text = (f"📂 **File Manager**\n\n{status_text}\n\n"
            f"🏷 **Badge:** {badge}\n\n"
            f"👇 **Tap a button to upload a file for that quality:**")
    
    if isinstance(message, Message):
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

# Custom Button Name Handler
@bot.on_callback_query(filters.regex("^add_custom_btn"))
async def add_custom_btn_handler(client, cb: CallbackQuery):
    uid = cb.from_user.id
    user_conversations[uid]["state"] = "wait_custom_btn_name"
    await cb.message.edit_text(
        "📝 **Enter Custom Button Name:**\n\n"
        "Examples:\n"
        "- `Episode 1`\n"
        "- `Season 1 Zip`\n"
        "- `480p [Drive]`"
    )

@bot.on_callback_query(filters.regex("^up_"))
async def upload_request(client, cb: CallbackQuery):
    qual = cb.data.split("_")[1]
    uid = cb.from_user.id
    
    user_conversations[uid]["current_quality"] = qual
    user_conversations[uid]["state"] = "wait_file_upload"
    
    await cb.message.edit_text(
        f"📤 **Upload Mode: {qual}**\n\n"
        "👉 **Forward** or **Send** the video file here.\n"
        "🤖 Bot will backup to Log Channel & create a Short Link.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_panel")]])
    )

@bot.on_callback_query(filters.regex("^bdg_"))
async def badge_handler(client, cb: CallbackQuery):
    action = cb.data.split("_")[1]
    uid = cb.from_user.id
    
    if action == "bangla":
        user_conversations[uid]['temp_badge_text'] = "বাংলা ডাবিং"
    elif action == "skip":
        user_conversations[uid]['temp_badge_text'] = None
        
    await show_upload_panel(cb, uid)

@bot.on_callback_query(filters.regex("^back_panel"))
async def back_button(client, cb: CallbackQuery):
    await show_upload_panel(cb, cb.from_user.id)

# ==============================================================================
# 8. MAIN MESSAGE HANDLER (TEXT & FILES)
# ==============================================================================

@bot.on_message(filters.private & (filters.text | filters.video | filters.document | filters.photo))
async def main_conversation_handler(client, message: Message):
    uid = message.from_user.id
    convo = user_conversations.get(uid)
    
    if not convo or "state" not in convo:
        return
    
    state = convo["state"]
    text = message.text
    
    # -----------------------------------------------------------
    # ADMIN BROADCAST & PREMIUM
    # -----------------------------------------------------------
    if state == "admin_broadcast_wait":
        if uid != OWNER_ID: return
        msg = await message.reply_text("📣 **Broadcasting...**")
        count = 0
        async for u in users_collection.find({}):
            try:
                await message.copy(chat_id=u['_id'])
                count += 1
                await asyncio.sleep(0.05)
            except:
                pass
        await msg.edit_text(f"✅ Broadcast complete.\nSent to: {count} users.")
        del user_conversations[uid]
        return
        
    elif state == "admin_add_prem_wait":
        if uid != OWNER_ID: return
        try:
            target_id = int(text)
            await users_collection.update_one({'_id': target_id}, {'$set': {'is_premium': True}}, upsert=True)
            await message.reply_text(f"✅ Premium Added to ID: `{target_id}`")
        except:
            await message.reply_text("❌ Invalid ID.")
        del user_conversations[uid]
        return
        
    elif state == "admin_rem_prem_wait":
        if uid != OWNER_ID: return
        try:
            target_id = int(text)
            await users_collection.update_one({'_id': target_id}, {'$set': {'is_premium': False}})
            await message.reply_text(f"✅ Premium Removed from ID: `{target_id}`")
        except:
            await message.reply_text("❌ Invalid ID.")
        del user_conversations[uid]
        return

    # -----------------------------------------------------------
    # MANUAL MODE INPUTS (UPDATED - SAFE FILE HANDLING)
    # -----------------------------------------------------------
    if state == "wait_manual_title":
        convo["details"]["title"] = text
        convo["details"]["name"] = text
        convo["state"] = "wait_manual_year"
        await message.reply_text("✅ Title Saved.\n\n📅 **Send Year:** (e.g. 2024)\n_Send 'skip' to leave empty._")
        
    elif state == "wait_manual_year":
        if text.lower() == "skip":
            convo["details"]["release_date"] = "----"
            convo["details"]["first_air_date"] = "----"
        else:
            convo["details"]["release_date"] = f"{text}-01-01"
            convo["details"]["first_air_date"] = f"{text}-01-01"
        convo["state"] = "wait_manual_rating"
        await message.reply_text("✅ Year Saved.\n\n⭐ **Send Rating:** (e.g. 7.5)\n_Send 'skip' to leave empty._")
        
    elif state == "wait_manual_rating":
        if text.lower() == "skip":
             convo["details"]["vote_average"] = 0.0
        else:
            try:
                convo["details"]["vote_average"] = float(text)
            except:
                convo["details"]["vote_average"] = 0.0
        convo["state"] = "wait_manual_genres"
        await message.reply_text("✅ Rating Saved.\n\n🎭 **Send Genres:** (e.g. Action, Drama)\n_Send 'skip' to leave empty._")
        
    elif state == "wait_manual_genres":
        if text.lower() == "skip":
            convo["details"]["genres"] = []
        else:
            genres = [{"name": g.strip()} for g in text.split(",")]
            convo["details"]["genres"] = genres
        convo["state"] = "wait_manual_poster"
        await message.reply_text("✅ Genres Saved.\n\n🖼 **Send Poster Photo:**")
        
    elif state == "wait_manual_poster":
        if not message.photo:
            return await message.reply_text("❌ Please send a Photo.")
        
        # নিরাপদ ছবি ডাউনলোডিং (Safe Local Download)
        msg = await message.reply_text("⬇️ Downloading poster...")
        photo_name = f"poster_{uid}_{int(time.time())}.jpg"
        try:
            # Download file
            photo_path = await client.download_media(message, file_name=photo_name)
            # Store ABSOLUTE path
            convo["details"]["poster_local_path"] = os.path.abspath(photo_path) 
            await msg.delete()
            
            convo["state"] = "wait_lang"
            await message.reply_text("✅ Poster Saved.\n\n🌐 **Send Language:** (e.g. Hindi, English)")
        except Exception as e:
            logger.error(f"Poster Download Error: {e}")
            await msg.edit_text(f"❌ Error downloading poster: {e}")

    elif state == "wait_lang" and convo.get("is_manual"):
        convo["language"] = text
        await show_upload_panel(message, uid)

    # CUSTOM BUTTON NAME HANDLER
    elif state == "wait_custom_btn_name":
        convo["temp_btn_name"] = text
        convo["current_quality"] = "custom"
        convo["state"] = "wait_file_upload"
        await message.reply_text(
            f"📤 **Upload File for: '{text}'**\n"
            "👉 Send Video/File now."
        )

    # -----------------------------------------------------------
    # FILE UPLOAD & LOG CHANNEL BACKUP LOGIC
    # -----------------------------------------------------------
    elif state == "wait_file_upload":
        if not (message.video or message.document):
            return await message.reply_text("❌ Please send a **Video** or **Document** file.")
        
        if not LOG_CHANNEL_ID:
            return await message.reply_text("❌ **System Error:** Log Channel ID is not set in `.env`.")

        # Determine Button Name
        if convo["current_quality"] == "custom":
            btn_name = convo["temp_btn_name"]
        else:
            btn_name = convo["current_quality"]
        
        # A. Notification
        status_msg = await message.reply_text("🔄 **Processing File...**\n1. Forwarding to Log Channel...\n2. Saving to Database...\n3. Shortening Link...")
        
        try:
            # B. Forward to Log Channel
            log_msg = await message.copy(chat_id=LOG_CHANNEL_ID, caption=f"#BACKUP\nUser: {uid}\nItem: {btn_name}")
            
            # C. Extract IDs
            backup_file_id = log_msg.video.file_id if log_msg.video else log_msg.document.file_id
            backup_msg_id = log_msg.id
            
            # D. Generate Data
            code = generate_random_code()
            bot_username = await get_bot_username()
            
            details = convo['details']
            title = details.get('title') or details.get('name') or "Unknown"
            
            # -- Clean Caption Construction (Name, Year, Language, Quality) --
            date = details.get("release_date") or details.get("first_air_date") or "----"
            year = date[:4]
            language = convo.get("language", "Unknown")

            tmdb_caption = (
                f"🎬 **Movie:** {title}\n"
                f"📅 **Year:** {year}\n"
                f"🔊 **Language:** {language}\n"
                f"🔰 **Quality:** {btn_name}"
            )
            
            # E. Get User Timer Settings
            user_data = await users_collection.find_one({'_id': uid})
            timer = user_data.get('delete_timer', 0)
            
            # F. Save to Database
            await files_collection.insert_one({
                "code": code,
                "file_id": backup_file_id,  # File ID from Log Channel
                "log_msg_id": backup_msg_id, # Msg ID from Log Channel
                "caption": tmdb_caption, # Only clean caption stored
                "delete_timer": timer,
                "uploader_id": uid,
                "created_at": datetime.now()
            })
            
            # G. Shorten Link
            deep_link = f"https://t.me/{bot_username}?start={code}"
            short_link = await shorten_link(uid, deep_link)
            
            # H. Update Conversation (Use specific button name)
            convo['links'][btn_name] = short_link
            
            # DONE
            await show_upload_panel(status_msg, uid)
            
        except Exception as e:
            logger.error(f"Upload Error: {e}")
            try:
                await status_msg.edit_text(f"❌ **Error:** {str(e)}")
            except:
                await message.reply_text(f"❌ **Error:** {str(e)}")

# ==============================================================================
# 9. FINAL POST PROCESSING & CHANNEL SELECTION
# ==============================================================================

@bot.on_callback_query(filters.regex("^proc_final"))
async def process_final_post(client, cb: CallbackQuery):
    uid = cb.from_user.id
    convo = user_conversations.get(uid)
    
    if not convo:
        return await cb.answer("Session expired.", show_alert=True)
    
    if not convo['links']:
        return await cb.answer("❌ No files uploaded!", show_alert=True)
        
    await cb.message.edit_text("🖼️ **Generating Poster & Preview...**\nPlease wait a moment.")
    
    # Check if this is a manual post
    is_manual = convo.get("is_manual", False)

    # 1. Generate Caption (Pass is_manual flag)
    caption = await generate_channel_caption(
        convo['details'],
        convo.get('language', 'Unknown'),
        convo['links'],
        is_manual=is_manual
    )
    
    # 2. Create Buttons (Handle Custom Names properly)
    buttons = []
    for qual, link in convo['links'].items():
        if qual in ["480p", "720p", "1080p"]:
            btn_text = f"📥 Download {qual}"
        else:
            btn_text = f"📥 {qual}" 
        buttons.append([InlineKeyboardButton(btn_text, url=link)])
        
    # 🆕 NEW: Add "How to Download" Button from User Settings
    user_data = await users_collection.find_one({'_id': uid})
    tutorial_link = user_data.get('tutorial_url')
    
    if tutorial_link:
        buttons.append([InlineKeyboardButton("ℹ️ How to Download / কিভাবে ডাউনলোড করবেন", url=tutorial_link)])
    
    # 3. Process Image (Watermark + Badge) [UPDATED MANUAL LOGIC]
    details = convo['details']
    
    poster_input = None
    
    # Priority 1: Manual Local File Check (Prioritize Manual Upload)
    if details.get('poster_local_path') and os.path.exists(details['poster_local_path']):
        poster_input = details['poster_local_path']
    # Priority 2: TMDB Poster Path (Only if not manual/local not found)
    elif details.get('poster_path'):
        poster_input = f"https://image.tmdb.org/t/p/w500{details['poster_path']}"
        
    poster_buffer, error = watermark_poster(
        poster_input,
        user_data.get('watermark_text'),
        convo.get('temp_badge_text')
    )
    
    if not poster_buffer:
        # Fallback if no poster found/error
        if error:
            logger.error(f"Poster Error: {error}")
            return await cb.message.edit_text(f"❌ **Image Processing Error:** {error}\n(Did you upload a poster in manual mode?)")
        else:
             return await cb.message.edit_text("❌ Poster not found.")
    
    # 4. Send Preview to User FIRST
    poster_buffer.seek(0)
    try:
        preview_msg = await client.send_photo(
            chat_id=uid,
            photo=poster_buffer,
            caption=caption,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        return await cb.message.edit_text(f"❌ Failed to send preview: {e}")

    await cb.message.delete()
    
    # 5. Store Data for Posting
    convo['final_post_data'] = {
        'file_id': preview_msg.photo.file_id, # Use cached file_id from preview
        'caption': caption,
        'buttons': buttons
    }
    
    # 6. Generate Channel Selection Buttons
    channels = user_data.get('channel_ids', [])
    channel_btns = []
    
    if channels:
        for cid in channels:
            channel_btns.append([InlineKeyboardButton(f"📢 Post to: {cid}", callback_data=f"sndch_{cid}")])
    else:
        await client.send_message(uid, "⚠️ **No Channels Saved!** Add using `/addchannel <id>`.")
    
    channel_btns.append([InlineKeyboardButton("✅ DONE / CLOSE", callback_data="close_post")])
    
    await client.send_message(
        uid, 
        "👇 **Select Channel to Publish:**\n(Clicking a button will instantly post to that channel)",
        reply_markup=InlineKeyboardMarkup(channel_btns)
    )

# Post to Channel Handler
@bot.on_callback_query(filters.regex("^sndch_"))
async def send_to_channel_handler(client, cb: CallbackQuery):
    uid = cb.from_user.id
    target_cid = cb.data.split("_")[1]
    convo = user_conversations.get(uid)
    
    if not convo or 'final_post_data' not in convo:
        return await cb.answer("❌ Session Expired.", show_alert=True)
    
    data = convo['final_post_data']
    try:
        await client.send_photo(
            chat_id=int(target_cid),
            photo=data['file_id'],
            caption=data['caption'],
            reply_markup=InlineKeyboardMarkup(data['buttons'])
        )
        await cb.answer(f"✅ Posted to {target_cid}", show_alert=True)
    except Exception as e:
        await cb.answer(f"❌ Failed: {e}", show_alert=True)

@bot.on_callback_query(filters.regex("^close_post"))
async def close_post_handler(client, cb: CallbackQuery):
    uid = cb.from_user.id
    if uid in user_conversations:
        # Cleanup: Delete the local poster file if it exists
        local_path = user_conversations[uid].get('details', {}).get('poster_local_path')
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp file: {e}")
                
        del user_conversations[uid]
        
    await cb.message.delete()
    await cb.answer("✅ Session Closed.", show_alert=True)

if __name__ == "__main__":
    logger.info("🚀 Bot is starting...")
    bot.run()
