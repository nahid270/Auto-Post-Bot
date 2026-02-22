# -*- coding: utf-8 -*-

# ==============================================================================
# 🎬 ULTIMATE MOVIE BOT - PREMIUM EDITION (WITH BATCH FOLDER SYSTEM)
# ==============================================================================
# Update Log:
# 1. Added Rich Caption Support for Files.
# 2. BATCH UPLOAD WITH QUALITY FOLDER SYSTEM (Deep Linking).
# 3. BLOGGER/WEBSITE REDIRECT SUPPORT (Anti-Ban Link System).
# 4. ADD EPISODE TO OLD POST & REPOST SYSTEM.
# 5. GLOBAL CANCEL COMMAND (/cancel).
# 6. AUTO YOUTUBE TRAILER FETCH & TRENDING MOVIES.
# 7. SETTINGS DASHBOARD (/settings) & DATABASE BACKUP (/backup).
# 8. ASYNC ANTI-LAG IMAGE PROCESSING.
# 9. SMART AUTO-REPLY REQUEST SYSTEM (With Auto-Spell Checker & Genre/Lang display)
# 10. DIRECT TEXT SEARCH (No need to click "Request Movie" button!)
# 11. SET DOMAIN COMMAND (/setdomain) FOR URL SHORTENERS.
# 12. [NEW] QUALITY BASED WEB SERIES FOLDER SYSTEM.
# ==============================================================================

import os
import io
import re
import asyncio
import logging
import secrets
import string
import time
import json
from threading import Thread
from datetime import datetime

# --- Third-party Library Imports ---
import requests
from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from pyrogram.errors import UserNotParticipant, FloodWait, MessageNotModified
from flask import Flask
from dotenv import load_dotenv
import motor.motor_asyncio
import numpy as np
import cv2 

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
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0")) 

# ------------------------------------------------------------------------------
# 🌐 BLOGGER / WEBSITE REDIRECT CONFIGURATION
# ------------------------------------------------------------------------------
BLOG_URL = os.getenv("BLOG_URL", "") 

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
requests_collection = db.requests 
batches_collection = db.batches # NEW: For Web Series Folders

# Global Variables
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
# FLASK KEEP-ALIVE SERVER
# ==============================================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Bot is Running Successfully!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

Thread(target=run_flask, daemon=True).start()


# ==============================================================================
# 2. HELPER FUNCTIONS & UTILITIES
# ==============================================================================

async def get_bot_username():
    global BOT_USERNAME
    if not BOT_USERNAME:
        me = await bot.get_me()
        BOT_USERNAME = me.username
    return BOT_USERNAME

def generate_random_code(length=8):
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))

async def auto_delete_message(client, chat_id, message_id, delay_seconds):
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
        try:
            await client.delete_messages(chat_id, message_id)
        except Exception:
            pass

# --- Resource Downloaders ---

def download_cascade():
    cascade_file = "haarcascade_frontalface_default.xml"
    if not os.path.exists(cascade_file):
        url = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"
        try:
            r = requests.get(url, timeout=20)
            with open(cascade_file, 'wb') as f:
                f.write(r.content)
        except Exception:
            return None
    return cascade_file

def download_font():
    font_file = "HindSiliguri-Bold.ttf"
    if not os.path.exists(font_file):
        url = "https://github.com/google/fonts/raw/main/ofl/hindsiliguri/HindSiliguri-Bold.ttf"
        try:
            r = requests.get(url, timeout=20)
            with open(font_file, 'wb') as f:
                f.write(r.content)
        except Exception:
            return None
    return font_file

# --- Database Helpers ---

async def add_user_to_db(user):
    await users_collection.update_one(
        {'_id': user.id},
        {
            '$set': {'first_name': user.first_name},
            '$setOnInsert': {'is_premium': False, 'delete_timer': 0}
        },
        upsert=True
    )

async def is_user_premium(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    user_data = await users_collection.find_one({'_id': user_id})
    if user_data:
        return user_data.get('is_premium', False)
    return False

async def shorten_link(user_id: int, long_url: str):
    user_data = await users_collection.find_one({'_id': user_id})
    
    if not user_data or 'shortener_api' not in user_data or 'shortener_url' not in user_data:
        return long_url

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
    except Exception:
        return long_url

# ==============================================================================
# 3. DECORATORS
# ==============================================================================

def force_subscribe(func):
    async def wrapper(client, message):
        if FORCE_SUB_CHANNEL:
            try:
                chat_id = int(FORCE_SUB_CHANNEL) if FORCE_SUB_CHANNEL.startswith("-100") else FORCE_SUB_CHANNEL
                await client.get_chat_member(chat_id, message.from_user.id)
            except UserNotParticipant:
                join_link = INVITE_LINK or f"https://t.me/{FORCE_SUB_CHANNEL.replace('@', '')}"
                return await message.reply_text(
                    "❗ **You must join our channel to use this bot.**", 
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👉 Join Channel", url=join_link)]])
                )
            except Exception:
                pass 
        await func(client, message)
    return wrapper

def check_premium(func):
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
    if not poster_input:
        return None, "Poster not found."
    
    try:
        original_img = None
        if isinstance(poster_input, str):
            if poster_input.startswith("http"): 
                img_data = requests.get(poster_input, timeout=15).content
                original_img = Image.open(io.BytesIO(img_data)).convert("RGBA")
            else: 
                if os.path.exists(poster_input):
                    original_img = Image.open(poster_input).convert("RGBA")
                else:
                    return None, f"Local file not found: {poster_input}"
        else: 
            original_img = Image.open(poster_input).convert("RGBA")
            
        if not original_img:
            return None, "Failed to load image."
        
        img = Image.new("RGBA", original_img.size)
        img.paste(original_img)
        draw = ImageDraw.Draw(img)

        if badge_text and badge_text.strip().lower() != "none":
            badge_font_size = int(img.width / 9)
            font_path = download_font()
            try:
                badge_font = ImageFont.truetype(font_path, badge_font_size) if font_path else ImageFont.load_default()
            except:
                badge_font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            x = (img.width - text_width) / 2
            
            y_pos = img.height * 0.03
            cascade_path = download_cascade()
            
            if cascade_path:
                try:
                    cv_image = np.array(original_img.convert('RGB'))
                    gray = cv2.cvtColor(cv_image, cv2.COLOR_RGB2GRAY)
                    face_cascade = cv2.CascadeClassifier(cascade_path)
                    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
                    
                    is_collision = False
                    padding = int(badge_font_size * 0.2)
                    text_box_y1 = y_pos + text_height + padding
                    
                    for (fx, fy, fw, fh) in faces:
                        if y_pos < (fy + fh) and text_box_y1 > fy:
                            is_collision = True
                            break
                    
                    if is_collision:
                        y_pos = img.height * 0.25
                except Exception:
                    pass

            y = y_pos
            padding = int(badge_font_size * 0.15)
            
            rect_layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
            rect_draw = ImageDraw.Draw(rect_layer)
            rect_draw.rectangle(
                (x - padding, y - padding, x + text_width + padding, y + text_height + padding),
                fill=(0, 0, 0, 160)
            )
            img = Image.alpha_composite(img, rect_layer)
            draw = ImageDraw.Draw(img)

            gradient = Image.new('RGBA', (text_width, text_height + int(padding)), (0, 0, 0, 0))
            gradient_draw = ImageDraw.Draw(gradient)
            
            start_color = (255, 255, 0)
            end_color = (255, 69, 0)
            
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
            
            draw.text((wx + 2, wy + 2), watermark_text, font=font, fill=(0, 0, 0, 128))
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

def get_tmdb_trailer(media_type, media_id):
    url = f"https://api.themoviedb.org/3/{media_type}/{media_id}/videos?api_key={TMDB_API_KEY}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        for vid in data.get("results", []):
            if vid.get("site") == "YouTube" and vid.get("type") == "Trailer":
                return f"https://www.youtube.com/watch?v={vid.get('key')}"
    except Exception:
        pass
    return None

def get_trending_today():
    url = f"https://api.themoviedb.org/3/trending/all/day?api_key={TMDB_API_KEY}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json().get("results", [])[:10]
    except Exception:
        return []

def search_tmdb(query: str):
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={query}&include_adult=true&page=1"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        return [res for res in results if res.get("media_type") in ["movie", "tv"]][:8] 
    except Exception:
        return []

def search_by_imdb(imdb_id: str):
    url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={TMDB_API_KEY}&external_source=imdb_id"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data.get("movie_results", []):
            item['media_type'] = 'movie'
            results.append(item)
        for item in data.get("tv_results", []):
            item['media_type'] = 'tv'
            results.append(item)
        return results
    except Exception:
        return []

def get_tmdb_details(media_type, media_id):
    url = f"https://api.themoviedb.org/3/{media_type}/{media_id}?api_key={TMDB_API_KEY}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        data['media_type'] = media_type 
        return data
    except Exception:
        return None

def extract_id_from_url(url: str):
    tmdb_pattern = r"themoviedb\.org/(movie|tv)/(\d+)"
    tmdb_match = re.search(tmdb_pattern, url)
    if tmdb_match:
        return "tmdb", tmdb_match.group(1), tmdb_match.group(2)

    imdb_pattern = r"tt\d{5,}"
    imdb_match = re.search(imdb_pattern, url)
    if imdb_match:
        return "imdb", None, imdb_match.group(0)
        
    return "text", None, url

# ==============================================================================
# CAPTION GENERATOR (POST)
# ==============================================================================
async def generate_channel_caption(data: dict, language: str, short_links: dict, is_manual: bool = False, trailer_url: str = None):
    title = data.get("title") or data.get("name") or "Movie"
    date = data.get("release_date") or data.get("first_air_date") or "----"
    year = date[:4]
    rating_val = data.get('vote_average', 0)
    rating = f"{rating_val:.1f}"
    
    if isinstance(data.get("genres"), list) and len(data["genres"]) > 0:
        if isinstance(data["genres"][0], dict):
            genre_str = ", ".join([g["name"] for g in data.get("genres", [])[:3]])
        else:
            genre_str = str(data.get("genres"))
    else:
        genre_str = "N/A"

    caption = f"""🎬 **{title} ({year})**
━━━━━━━━━━━━━━━━━━━━━━━
⭐ **Rating:** {rating}/10
🎭 **Genre:** {genre_str}
🔊 **Language:** {language}"""

    if trailer_url:
        caption += f"\n🎞 **Trailer:** [Watch Here]({trailer_url})"

    caption += """\n━━━━━━━━━━━━━━━━━━━━━━━
👀 𝗪𝗔𝗧𝗖𝗛 𝗢𝗡𝗟𝗜𝗡𝗘/📤𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗
👇  ℍ𝕚𝕘𝕙 𝕊𝕡𝕖𝕖𝕕 | ℕ𝕠 𝔹𝕦𝕗𝕗𝕖𝕣𝕚𝕟𝕘  👇"""

    footer = """\n\nMovie ReQuest Group 
👇👇👇
https://t.me/Terabox_search_group

Premium Backup Group link 👇👇👇
https://t.me/+GL_XAS4MsJg4ODM1"""
    
    return caption + footer

# ==============================================================================
# 5. GENERAL & ADMIN DIRECT COMMANDS
# ==============================================================================

@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_process_cmd(client, message: Message):
    uid = message.from_user.id
    if uid in user_conversations:
        del user_conversations[uid]
        await message.reply_text("✅ **All processes have been cancelled successfully.**")
    else:
        await message.reply_text("ℹ️ **No active process found to cancel.**")

@bot.on_message(filters.command("settings") & filters.private)
@force_subscribe
async def settings_dashboard(client, message: Message):
    uid = message.from_user.id
    user_data = await users_collection.find_one({'_id': uid})
    if not user_data:
        return await message.reply_text("❌ User data not found. Type /start first.")

    watermark = user_data.get('watermark_text', 'Not Set')
    api_url = user_data.get('shortener_url', 'Not Set')
    timer = user_data.get('delete_timer', 0)
    channels = user_data.get('channel_ids', [])
    channel_list = ", ".join(channels) if channels else "None"

    text = (f"⚙️ **Your Settings Dashboard** ⚙️\n\n"
            f"💧 **Watermark:** `{watermark}`\n"
            f"🔗 **Shortener Base:** `{api_url}`\n"
            f"⏳ **Auto-Delete Timer:** `{int(timer/60)} Mins`\n"
            f"📢 **Saved Channels:** `{channel_list}`\n\n"
            f"💡 **How to change:**\n"
            f"• `/setwatermark Text`\n"
            f"• `/setdomain url.com`\n"
            f"• `/setapi Key`\n"
            f"• `/settimer 10` (in mins)\n"
            f"• `/addchannel -100xxx`")
    
    await message.reply_text(text)

@bot.on_message(filters.command("backup") & filters.private)
async def backup_db_cmd(client, message: Message):
    if message.from_user.id != OWNER_ID:
        return
    msg = await message.reply_text("🔄 **Generating Database Backup...**")
    try:
        users = await users_collection.find({}).to_list(None)
        files = await files_collection.find({}).to_list(None)
        
        backup_data = {"users": users, "files": files}
        
        with open("db_backup.json", "w", encoding="utf-8") as f:
            json.dump(backup_data, f, default=str, indent=4)
            
        await message.reply_document("db_backup.json", caption="📦 **Database Backup Successfully Generated.**")
        os.remove("db_backup.json")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ **Backup Failed:** {str(e)}")

# --- ADMIN DIRECT COMMANDS ---
@bot.on_message(filters.command("stats") & filters.private)
async def stats_command(client, message: Message):
    if message.from_user.id != OWNER_ID: return
    total = await users_collection.count_documents({})
    prem = await users_collection.count_documents({'is_premium': True})
    files = await files_collection.count_documents({})
    reqs = await requests_collection.count_documents({})
    await message.reply_text(f"📊 **Bot Statistics:**\n\n👥 Total Users: {total}\n💎 Premium Users: {prem}\n📂 Total Files: {files}\n📨 Pending Requests: {reqs}")

@bot.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client, message: Message):
    if message.from_user.id != OWNER_ID: return
    user_conversations[message.from_user.id] = {"state": "admin_broadcast_wait"}
    await message.reply_text("📢 **Broadcast Mode**\n\nSend the message (Text/Photo/Video) you want to broadcast.\n(Type /cancel to stop)")

@bot.on_message(filters.command("addpremium") & filters.private)
async def add_premium_cmd(client, message: Message):
    if message.from_user.id != OWNER_ID: return
    if len(message.command) > 1:
        try:
            user_id = int(message.command[1])
            await users_collection.update_one({'_id': user_id}, {'$set': {'is_premium': True}}, upsert=True)
            await message.reply_text(f"✅ Premium Added to ID: `{user_id}`")
        except:
            await message.reply_text("❌ Invalid ID format.")
    else:
        user_conversations[message.from_user.id] = {"state": "admin_add_prem_wait"}
        await message.reply_text("➕ **Add Premium**\n\nSend User ID.\n(Type /cancel to stop)")

@bot.on_message(filters.command("rempremium") & filters.private)
async def rem_premium_cmd(client, message: Message):
    if message.from_user.id != OWNER_ID: return
    if len(message.command) > 1:
        try:
            user_id = int(message.command[1])
            await users_collection.update_one({'_id': user_id}, {'$set': {'is_premium': False}})
            await message.reply_text(f"✅ Premium Removed from ID: `{user_id}`")
        except:
            await message.reply_text("❌ Invalid ID format.")
    else:
        user_conversations[message.from_user.id] = {"state": "admin_rem_prem_wait"}
        await message.reply_text("➖ **Remove Premium**\n\nSend User ID.\n(Type /cancel to stop)")

# ==============================================================================
# 6. START COMMAND & BOT MENUS
# ==============================================================================

@bot.on_message(filters.command("start") & filters.private)
@force_subscribe
async def start_cmd(client, message: Message):
    user = message.from_user
    uid = user.id
    await add_user_to_db(user)
    
    # --- FILE & FOLDER RETRIEVAL SYSTEM ---
    if len(message.command) > 1:
        code = message.command[1]
        
        # 📁 FOLDER RETRIEVAL (Web Series Batches)
        if code.startswith("b_"):
            batch_data = await batches_collection.find_one({"batch_code": code})
            if batch_data:
                msg = await message.reply_text("📂 **আপনার ফোল্ডারটি ওপেন হচ্ছে...**")
                
                title = batch_data.get('title', 'Web Series')
                qual = batch_data.get('quality', 'Files')
                files = batch_data.get('files', [])
                
                text = f"📁 **{title}**\n🔰 **কোয়ালিটি:** {qual}\n\n👇 **নিচ থেকে আপনার এপিসোডটি সিলেক্ট করুন:**"
                
                ep_buttons = []
                row = []
                for f in files:
                    row.append(InlineKeyboardButton(f.get('name', 'Episode'), url=f.get('link')))
                    if len(row) == 2:
                        ep_buttons.append(row)
                        row = []
                if row: ep_buttons.append(row)
                    
                await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(ep_buttons))
            else:
                await message.reply_text("❌ **Folder Link Expired or Invalid.**")
            return

        # 📄 SINGLE FILE RETRIEVAL
        file_data = await files_collection.find_one({"code": code})
        if file_data:
            msg = await message.reply_text("📂 **Fetching your file...**")
            log_msg_id = file_data.get("log_msg_id")
            caption = file_data.get("caption", "🎬 **Movie File**")
            timer = file_data.get("delete_timer", 0)

            try:
                sent_msg = None
                try:
                    sent_msg = await client.send_cached_media(
                        chat_id=uid,
                        file_id=file_data["file_id"],
                        caption=caption
                    )
                except:
                    sent_msg = None

                if not sent_msg and LOG_CHANNEL_ID and log_msg_id:
                    sent_msg = await client.copy_message(
                        chat_id=uid,
                        from_chat_id=LOG_CHANNEL_ID,
                        message_id=log_msg_id,
                        caption=caption
                    )
                
                if sent_msg:
                    await msg.delete()
                    if timer > 0:
                        asyncio.create_task(auto_delete_message(client, uid, sent_msg.id, timer))
                        await client.send_message(uid, f"⚠️ **Auto-Delete Enabled!**\n\nThis file will be deleted in **{int(timer/60)} minutes**.")
                else:
                    await msg.edit_text("❌ **Error:** File not found.")

            except Exception as e:
                await msg.edit_text(f"❌ **Error:** {e}")
        else:
            await message.reply_text("❌ **Link Expired or Invalid.**")
        return

    # --- MAIN MENU ---
    if uid in user_conversations:
        del user_conversations[uid]
        
    is_premium = await is_user_premium(uid)
    
    if uid == OWNER_ID:
        welcome_text = f"👑 **Welcome Boss!**\n\n**Admin Control Panel:**"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
             InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
            [InlineKeyboardButton("➕ Add Premium", callback_data="admin_add_premium"),
             InlineKeyboardButton("➖ Remove Premium", callback_data="admin_rem_premium")],
             [InlineKeyboardButton("⚙️ Setup Instructions", callback_data="api_help")]
        ])
    else:
        status_text = "💎 **Premium User**" if is_premium else "👤 **Free User**"
        welcome_text = f"👋 **Hello {user.first_name}!**\n\nYour Status: {status_text}\n\n👇 **Available Commands:**\n`/post <Name/Link>` - Auto Post (Supports IMDb/TMDB)\n`/manual` - Manual Post (Free for All)\n`/addep <Link>` - Add Episode to Old Post\n`/trending` - Get Trending Movies/Series\n\n🔍 **মুভি খুঁজতে সরাসরি মুভির নাম লিখে সেন্ড করুন!**"
        
        user_buttons = [
            [InlineKeyboardButton("👤 My Account", callback_data="my_account")],
        ]
        if not is_premium:
            user_buttons.insert(0, [InlineKeyboardButton("💎 Buy Premium Access", user_id=OWNER_ID)])
            
        buttons = InlineKeyboardMarkup(user_buttons)

    await message.reply_text(welcome_text, reply_markup=buttons)

# --- Callback Handler ---

@bot.on_callback_query(filters.regex(r"^(admin_|my_account|api_help|request_movie)"))
async def callback_handler(client, cb: CallbackQuery):
    data = cb.data
    uid = cb.from_user.id 
    
    if data == "my_account":
        status = "Premium 💎" if await is_user_premium(uid) else "Free 👤"
        await cb.answer(f"User: {cb.from_user.first_name}\nStatus: {status}", show_alert=True)
        
    elif data == "api_help":
        help_text = "**⚙️ Commands:**\n`/setdomain <url>`\n`/setapi <key>`\n`/setwatermark <text>`\n`/settutorial <link>`\n`/addchannel <id>`"
        await cb.answer(help_text, show_alert=True)

    elif data == "request_movie":
        user_conversations[uid] = {"state": "waiting_for_request"}
        await cb.message.edit_text("📝 **Request System**\n\n✍️ Please type the Name of the Movie or Series you want:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_req")]]))
        
    elif data.startswith("admin_") and uid == OWNER_ID:
        if data == "admin_stats":
            total = await users_collection.count_documents({})
            prem = await users_collection.count_documents({'is_premium': True})
            files = await files_collection.count_documents({})
            reqs = await requests_collection.count_documents({})
            await cb.answer(f"📊 Total Users: {total}\n💎 Premium: {prem}\n📂 Files: {files}\n📨 Requests: {reqs}", show_alert=True)
            
        elif data == "admin_broadcast":
            await cb.message.edit_text("📢 **Broadcast Mode**\n\nSend message to broadcast.\n(Type /cancel to stop)")
            user_conversations[uid] = {"state": "admin_broadcast_wait"}
            
        elif "add_premium" in data:
            await cb.message.edit_text("➕ **Add Premium**\n\nSend User ID.\n(Type /cancel to stop)")
            user_conversations[uid] = {"state": "admin_add_prem_wait"}
            
        elif "rem_premium" in data:
            await cb.message.edit_text("➖ **Remove Premium**\n\nSend User ID.\n(Type /cancel to stop)")
            user_conversations[uid] = {"state": "admin_rem_prem_wait"}

@bot.on_callback_query(filters.regex("^cancel_req"))
async def cancel_request(client, cb: CallbackQuery):
    uid = cb.from_user.id
    if uid in user_conversations:
        del user_conversations[uid]
    await cb.message.edit_text("❌ **Request Cancelled.**")

# --- Settings Commands ---

@bot.on_message(filters.command(["setwatermark", "setapi", "setdomain", "settimer", "addchannel", "delchannel", "mychannels", "settutorial"]) & filters.private)
@force_subscribe
async def settings_commands(client, message: Message):
    cmd = message.command[0].lower()
    uid = message.from_user.id
    
    if cmd == "setwatermark":
        text = " ".join(message.command[1:])
        if text.lower() in ['none', 'off', 'clear']: text = ""
        await users_collection.update_one({'_id': uid}, {'$set': {'watermark_text': text}}, upsert=True)
        await message.reply_text(f"✅ Watermark set: `{text}`")

    elif cmd == "setdomain":
        if len(message.command) > 1:
            domain = message.command[1].replace("https://", "").replace("http://", "").strip("/")
            await users_collection.update_one({'_id': uid}, {'$set': {'shortener_url': domain}}, upsert=True)
            await message.reply_text(f"✅ Shortener Domain Saved: `{domain}`")
        else:
            await message.reply_text("❌ Usage: `/setdomain shareus.io`")

    elif cmd == "setapi":
        if len(message.command) > 1:
            await users_collection.update_one({'_id': uid}, {'$set': {'shortener_api': message.command[1]}}, upsert=True)
            await message.reply_text("✅ API Key Saved.")
        else: await message.reply_text("❌ Usage: `/setapi KEY`")

    elif cmd == "settutorial":
        if len(message.command) > 1:
            link = message.command[1]
            await users_collection.update_one({'_id': uid}, {'$set': {'tutorial_url': link}}, upsert=True)
            await message.reply_text(f"✅ Tutorial Link Saved.")
        else: await message.reply_text("❌ Usage: `/settutorial link`")

    elif cmd == "settimer":
        if len(message.command) > 1:
            try:
                mins = int(message.command[1])
                await users_collection.update_one({'_id': uid}, {'$set': {'delete_timer': mins*60}}, upsert=True)
                await message.reply_text(f"✅ Timer set: **{mins} Minutes**")
            except: await message.reply_text("❌ Usage: `/settimer 10`")
        else:
            await users_collection.update_one({'_id': uid}, {'$set': {'delete_timer': 0}})
            await message.reply_text("✅ Auto-Delete DISABLED.")

    elif cmd == "addchannel":
        if len(message.command) > 1:
            cid = message.command[1]
            await users_collection.update_one({'_id': uid}, {'$addToSet': {'channel_ids': cid}}, upsert=True)
            await message.reply_text(f"✅ Channel `{cid}` added.")

    elif cmd == "delchannel":
        if len(message.command) > 1:
            cid = message.command[1]
            await users_collection.update_one({'_id': uid}, {'$pull': {'channel_ids': cid}})
            await message.reply_text(f"✅ Channel `{cid}` removed.")

    elif cmd == "mychannels":
        data = await users_collection.find_one({'_id': uid})
        channels = data.get('channel_ids', [])
        if channels: await message.reply_text(f"📋 **Channels:**\n" + "\n".join([f"`{c}`" for c in channels]))
        else: await message.reply_text("❌ No channels saved.")

# ==============================================================================
# 7. AUTO POST (TMDB & IMDb SMART SEARCH) & TRENDING
# ==============================================================================

@bot.on_message(filters.command("trending") & filters.private)
@force_subscribe
@check_premium
async def trending_cmd(client, message: Message):
    msg = await message.reply_text("🔥 **Fetching Today's Trending Movies/Series...**")
    results = await asyncio.to_thread(get_trending_today)
    
    if not results:
        return await msg.edit_text("❌ **Could not fetch trending data right now.**")
        
    buttons = []
    for r in results:
        m_type = r.get('media_type', 'movie')
        title = r.get('title') or r.get('name')
        year = (r.get('release_date') or r.get('first_air_date') or '----')[:4]
        buttons.append([InlineKeyboardButton(f"🎬 {title} ({year})", callback_data=f"sel_{m_type}_{r['id']}")])
        
    await msg.edit_text(f"📈 **Top 10 Trending Today:**", reply_markup=InlineKeyboardMarkup(buttons))

@bot.on_message(filters.command("post") & filters.private)
@force_subscribe
@check_premium
async def post_search_cmd(client, message: Message):
    if len(message.command) == 1:
        return await message.reply_text("**Usage:**\n`/post Spiderman`\n`/post https://www.imdb.com/title/tt12345/`")
    
    raw_query = " ".join(message.command[1:]).strip()
    msg = await message.reply_text(f"🔍 **Searching...**")
    
    search_type, m_type, extracted_val = extract_id_from_url(raw_query)
    
    results = []
    
    if search_type == "tmdb":
        details = await asyncio.to_thread(get_tmdb_details, m_type, extracted_val)
        if details:
            uid = message.from_user.id
            user_conversations[uid] = {
                "details": details,
                "links": {},
                "state": "wait_lang",
                "is_manual": False
            }
            langs = [["English", "Hindi"], ["Bengali", "Dual Audio"]]
            buttons = [[InlineKeyboardButton(l, callback_data=f"lang_{l}") for l in row] for row in langs]
            buttons.append([InlineKeyboardButton("✍️ Custom Language", callback_data="lang_custom")])
            
            return await msg.edit_text(f"✅ Found: **{details.get('title') or details.get('name')}**\n\n🌐 **Select Language:**", reply_markup=InlineKeyboardMarkup(buttons))
        else:
            return await msg.edit_text("❌ Invalid TMDB Link.")

    elif search_type == "imdb":
        results = await asyncio.to_thread(search_by_imdb, extracted_val)
        if not results:
             return await msg.edit_text("❌ IMDb ID not found in TMDB database.")
    
    else:
        results = await asyncio.to_thread(search_tmdb, extracted_val)

    if not results:
        return await msg.edit_text("❌ **No results found!**\nTry checking the spelling or use an IMDb link.")
    
    buttons = []
    for r in results:
        m_type = r.get('media_type', 'movie')
        title = r.get('title') or r.get('name')
        year = (r.get('release_date') or r.get('first_air_date') or '----')[:4]
        buttons.append([InlineKeyboardButton(f"🎬 {title} ({year})", callback_data=f"sel_{m_type}_{r['id']}")])
    
    await msg.edit_text(f"👇 **Found {len(results)} Result(s):**", reply_markup=InlineKeyboardMarkup(buttons))

# ==============================================================================
# 8. MANUAL POST SYSTEM
# ==============================================================================

@bot.on_message(filters.command("manual") & filters.private)
@force_subscribe
async def manual_cmd_start(client, message: Message):
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
    
    user_conversations[uid] = {
        "details": {"media_type": m_type},
        "links": {},
        "state": "wait_manual_title",
        "is_manual": True 
    }
    await cb.message.edit_text(f"📝 **Step 1:** Send the **Title** of the {m_type}.")

# ==============================================================================
# 9. UPLOAD PANEL & HANDLERS (FOLDER SYSTEM ENABLED)
# ==============================================================================

@bot.on_callback_query(filters.regex("^sel_"))
async def media_selected(client, cb: CallbackQuery):
    _, m_type, mid = cb.data.split("_")
    details = await asyncio.to_thread(get_tmdb_details, m_type, mid)
    if not details: return await cb.answer("Error fetching details!", show_alert=True)
    
    uid = cb.from_user.id
    user_conversations[uid] = {
        "details": details,
        "links": {},
        "state": "wait_lang",
        "is_manual": False
    }
    
    langs = [["English", "Hindi"], ["Bengali", "Dual Audio"]]
    buttons = [[InlineKeyboardButton(l, callback_data=f"lang_{l}") for l in row] for row in langs]
    buttons.append([InlineKeyboardButton("✍️ Custom Language", callback_data="lang_custom")])

    await cb.message.edit_text(f"✅ Selected: **{details.get('title') or details.get('name')}**\n\n🌐 **Select Language:**", reply_markup=InlineKeyboardMarkup(buttons))

@bot.on_callback_query(filters.regex("^lang_"))
async def language_selected(client, cb: CallbackQuery):
    data = cb.data.split("_")[1]
    uid = cb.from_user.id
    
    if data == "custom":
        user_conversations[uid]["state"] = "wait_custom_lang"
        await cb.message.edit_text("✍️ **Type Your Custom Language:**\n(e.g. Tamil, French, Spanish Dubbed)")
        return

    user_conversations[uid]["language"] = data
    await show_upload_panel(cb.message, uid, is_edit=True)

async def show_upload_panel(message, uid, is_edit=False):
    convo = user_conversations.get(uid, {})
    is_batch = convo.get("is_batch_mode", False)
    season_tag = convo.get("batch_season_prefix", None)
    
    if is_batch:
        batch_text = f"🔴 Turn Batch OFF ({season_tag if season_tag else 'Active'})"
    else:
        batch_text = "📦 Start Batch/Season Upload"
    
    batch_callback = "toggle_batch"
    
    buttons = [
        [InlineKeyboardButton("📤 Upload 480p", callback_data="up_480p")],
        [InlineKeyboardButton("📤 Upload 720p", callback_data="up_720p")],
        [InlineKeyboardButton("📤 Upload 1080p", callback_data="up_1080p")],
        [InlineKeyboardButton(batch_text, callback_data=batch_callback)], 
        [InlineKeyboardButton("➕ Custom Button / Episode", callback_data="add_custom_btn")],
        [InlineKeyboardButton("🎨 Add Badge", callback_data="set_badge")],
        [InlineKeyboardButton("✅ FINISH & POST", callback_data="proc_final")]
    ]
    
    links = convo.get('links', {})
    badge = convo.get('temp_badge_text', 'None')
    
    status_lines = []
    for q, files in links.items():
        if isinstance(files, list) and len(files) > 0:
            if len(files) == 1:
                status_lines.append(f"✅ **{q}** Added (1 File)")
            else:
                status_lines.append(f"📁 **{q}** ➔ {len(files)} File(s) [Folder Mode]")
                
    status_text = "\n".join(status_lines) if status_lines else "No files added yet."
    
    mode_text = ""
    if is_batch:
        mode_text = f"🟢 **BATCH MODE ACTIVE**\nClick a Quality below to upload multiple episodes into its folder!"

    text = (f"📂 **File Manager**\n{mode_text}\n\n{status_text}\n\n"
            f"🏷 **Badge:** {badge}\n\n"
            f"👇 **Tap a button to upload a file for that quality:**")
    
    if is_edit:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

@bot.on_callback_query(filters.regex("^toggle_batch"))
async def toggle_batch_handler(client, cb: CallbackQuery):
    uid = cb.from_user.id
    convo = user_conversations.get(uid)
    if not convo: return await cb.answer("Session expired.", show_alert=True)
    
    if convo.get("is_batch_mode", False):
        convo["is_batch_mode"] = False
        convo["batch_season_prefix"] = None 
        convo["episode_counts"] = {}
        await cb.answer("🔴 Batch Mode Disabled.", show_alert=True)
        await show_upload_panel(cb.message, uid, is_edit=True)
    else:
        convo["state"] = "wait_batch_season_input"
        await cb.message.edit_text(
            "📝 **Enter Season Number (Optional)**\n\n"
            "👉 Type a prefix like `S1`, `S01` or `Season 1`.\n"
            "Episodes will look like: **S1 E1**, **S1 E2** etc.\n\n"
            "👇 **Click Skip** to use default (**Episode 1**).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭ SKIP (Default)", callback_data="batch_skip_season")],
                [InlineKeyboardButton("❌ Cancel", callback_data="back_panel")]
            ])
        )

@bot.on_callback_query(filters.regex("^batch_skip_season"))
async def batch_skip_season_handler(client, cb: CallbackQuery):
    uid = cb.from_user.id
    convo = user_conversations.get(uid)
    
    convo["batch_season_prefix"] = None 
    convo["is_batch_mode"] = True
    convo["episode_counts"] = {}
    
    await cb.message.edit_text(
        "🟢 **Batch Mode Active (Default)**\n\n"
        "👉 **Now click a Quality (e.g. 720p) from the panel to upload episodes!**\n"
        "Naming: **Episode 1, Episode 2...**",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Panel", callback_data="back_panel")]])
    )

@bot.on_callback_query(filters.regex("^add_custom_btn"))
async def add_custom_btn_handler(client, cb: CallbackQuery):
    uid = cb.from_user.id
    user_conversations[uid]["state"] = "wait_custom_btn_name"
    await cb.message.edit_text("📝 **Enter Custom Button Name:**\n(e.g. Episode 1, Zip File)")

@bot.on_callback_query(filters.regex("^up_"))
async def upload_request(client, cb: CallbackQuery):
    qual = cb.data.split("_")[1]
    uid = cb.from_user.id
    
    user_conversations[uid]["current_quality"] = qual
    user_conversations[uid]["state"] = "wait_file_upload"
    
    is_batch = user_conversations[uid].get("is_batch_mode", False)
    mode_text = "Batch/Folder Mode" if is_batch else "Single File Mode"
    
    await cb.message.edit_text(
        f"📤 **Upload Mode: {qual} ({mode_text})**\n\n"
        "👉 **Forward** or **Send** the video file(s) here.\n"
        "🤖 Bot will backup to Log Channel & create a Short Link.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back / Finish", callback_data="back_panel")]])
    )

@bot.on_callback_query(filters.regex("^set_badge"))
async def badge_menu_handler(client, cb: CallbackQuery):
    uid = cb.from_user.id
    user_conversations[uid]["state"] = "wait_badge_text"
    await cb.message.edit_text("✍️ **Enter the text for the Badge:**\n(e.g., 4K HDR, Dual Audio) or 'None'")

@bot.on_callback_query(filters.regex("^back_panel"))
async def back_button(client, cb: CallbackQuery):
    uid = cb.from_user.id
    await show_upload_panel(cb.message, uid, is_edit=True)

# ==============================================================================
# 10. ADD EPISODE (EDIT) & REPOST SYSTEM
# ==============================================================================

@bot.on_message(filters.command("addep") & filters.private)
@force_subscribe
@check_premium
async def add_episode_cmd(client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "⚠️ **Usage:**\n`/addep <Channel_Post_Link>`\n\n"
            "Example: `/addep https://t.me/MyMovieChannel/123`"
        )
    
    post_link = message.command[1]
    
    try:
        if "/c/" in post_link: 
            parts = post_link.split("/")
            chat_id = int("-100" + parts[-2])
            msg_id = int(parts[-1])
        else: 
            parts = post_link.split("/")
            chat_id = parts[-2] 
            msg_id = int(parts[-1])
    except:
        return await message.reply_text("❌ **Invalid Link Format!**")

    try:
        target_msg = await client.get_messages(chat_id, msg_id)
        if not target_msg or not target_msg.reply_markup:
            return await message.reply_text("❌ **Message not found or has no buttons!**\nMake sure Bot is Admin in that channel.")
    except Exception as e:
        return await message.reply_text(f"❌ **Error accessing post:** {e}\n(Make sure Bot is Admin)")

    uid = message.from_user.id
    user_conversations[uid] = {
        "state": "wait_file_for_edit",
        "edit_chat_id": chat_id,
        "edit_msg_id": msg_id,
        "old_markup": target_msg.reply_markup
    }
    
    await message.reply_text(
        f"✅ **Post Found!**\n🆔 Message ID: `{msg_id}`\n\n"
        "📂 **Now send the New File (Episode/Movie):**\n"
        "_(Bot will create a link and add it to the post)_"
    )

@bot.on_callback_query(filters.regex("^repost_"))
async def repost_handler(client, cb: CallbackQuery):
    uid = cb.from_user.id
    convo = user_conversations.get(uid)
    if not convo or "repost_data" not in convo:
        return await cb.answer("❌ Session Expired.", show_alert=True)
    
    data = convo["repost_data"]
    chat_id = data["chat_id"]
    msg_id = data["message_id"]
    update_text = data["update_text"]
    
    action = cb.data
    
    try:
        if action == "repost_full":
            updated_msg = await client.get_messages(chat_id, msg_id)
            await client.copy_message(
                chat_id=chat_id,
                from_chat_id=chat_id,
                message_id=msg_id,
                reply_markup=updated_msg.reply_markup 
            )
            await cb.message.edit_text(f"✅ **Fresh Post Sent!**\nUsers have been notified about **{update_text}**.")
            
        elif action == "repost_alert":
            if str(chat_id).startswith("-100"):
                clean_id = str(chat_id)[4:]
                post_link = f"https://t.me/c/{clean_id}/{msg_id}"
            else:
                chat_info = await client.get_chat(chat_id)
                post_link = f"https://t.me/{chat_info.username}/{msg_id}"

            alert_text = (
                f"🔔 **Update Alert!**\n\n"
                f"🆕 **{update_text}** has been added!\n"
                f"👇 Click below to watch."
            )
            
            await client.send_message(
                chat_id=chat_id,
                text=alert_text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎬 Watch Now", url=post_link)]
                ])
            )
            await cb.message.edit_text(f"✅ **Alert Sent!**\nNotification sent for **{update_text}**.")
            
    except Exception as e:
        logger.error(f"Repost Error: {e}")
        await cb.message.edit_text(f"❌ **Failed to Repost:** {e}")
    
    if uid in user_conversations:
        del user_conversations[uid]

# ==============================================================================
# 11. MAIN MESSAGE HANDLER (TEXT & FILES)
# ==============================================================================

@bot.on_message(filters.private & (filters.text | filters.video | filters.document | filters.photo) & ~filters.command(["start", "post", "manual", "addep", "cancel", "trending", "settings", "backup", "setwatermark", "setapi", "setdomain", "settimer", "addchannel", "delchannel", "mychannels", "settutorial", "stats", "broadcast", "addpremium", "rempremium"]))
async def main_conversation_handler(client, message: Message):
    uid = message.from_user.id
    convo = user_conversations.get(uid)
    
    if convo and "state" in convo:
        state = convo["state"]
        text = message.text
    else:
        if message.text:
            state = "waiting_for_request"
            text = message.text
        else:
            return 
    
    # ---------------------------------------------------------
    # 🎬 NEW SMART AUTO-REPLY SYSTEM (WITH INCOME/SHORTENER FIX)
    # ---------------------------------------------------------
    if state == "waiting_for_request":
        request_text = text
        if not request_text: return await message.reply_text("❌ Please send text only.")
        
        msg = await message.reply_text("🔍 **আপনার মুভিটি আমাদের ডাটাবেসে খোঁজা হচ্ছে...**\n(দয়া করে অপেক্ষা করুন)")
        
        try:
            tmdb_results = await asyncio.to_thread(search_tmdb, request_text)
            if tmdb_results:
                corrected_title = tmdb_results[0].get('title') or tmdb_results[0].get('name')
            else:
                corrected_title = request_text

            clean_name = re.sub(r'[^a-zA-Z0-9\s]', ' ', corrected_title)
            words = [w for w in clean_name.split() if len(w) > 1][:4] 
            if not words: words = request_text.split()[:4]
            
            regex_pattern = "".join([f"(?=.*{re.escape(w)})" for w in words])
            query = {"caption": {"$regex": regex_pattern, "$options": "i"}}
            
            found_files = await files_collection.find(query).to_list(length=10)
            
            if found_files:
                buttons = []
                languages = set()
                genres = set()
                
                for f in found_files:
                    caption_text = f.get('caption', '')
                    
                    qual_match = re.search(r"Quality:\*\*\s*(.*?)\n", caption_text)
                    qual = qual_match.group(1).strip() if qual_match else "Download"
                    
                    lang_match = re.search(r"Language:\*\*\s*(.*?)\n", caption_text)
                    if lang_match and lang_match.group(1).strip() not in ["Unknown", "N/A"]:
                        languages.add(lang_match.group(1).strip())
                        
                    genre_match = re.search(r"Genre:\*\*\s*(.*?)\n", caption_text)
                    if genre_match and genre_match.group(1).strip() not in ["Unknown", "N/A"]:
                        genres.add(genre_match.group(1).strip())
                    
                    bot_uname = await get_bot_username()
                    file_code = f['code']
                    
                    if BLOG_URL and "http" in BLOG_URL:
                        base_blog = BLOG_URL.rstrip("/")
                        final_long_url = f"{base_blog}/?code={file_code}"
                    else:
                        final_long_url = f"https://t.me/{bot_uname}?start={file_code}"
                    
                    uploader_id = f.get('uploader_id', uid) 
                    short_link = await shorten_link(uploader_id, final_long_url)
                    
                    buttons.append([InlineKeyboardButton(f"📥 {qual}", url=short_link)])
                    
                display_lang = ", ".join(languages) if languages else "Unknown"
                display_genre = list(genres)[0] if genres else "Unknown"
                    
                await msg.edit_text(
                    f"✅ **খুশির খবর!**\nআপনি যেই মুভিটি খুঁজছেন, তা আমাদের কাছে আগে থেকেই আপলোড করা আছে।\n\n"
                    f"🎬 **Name:** {corrected_title}\n"
                    f"🎭 **Genre:** {display_genre}\n"
                    f"🔊 **Language:** {display_lang}\n\n"
                    f"👇 নিচ থেকে সরাসরি ডাউনলোড করে নিন:",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                user_conversations.pop(uid, None)
                return
        except Exception as e:
            logger.error(f"Auto Reply Error: {e}")
            pass 
            
        req_entry = {
            "user_id": uid,
            "user_name": message.from_user.first_name,
            "request": request_text,
            "date": datetime.now()
        }
        await requests_collection.insert_one(req_entry)
        
        if LOG_CHANNEL_ID:
            await client.send_message(
                LOG_CHANNEL_ID, 
                f"📨 **New Request!**\n👤 User: {message.from_user.mention}\n📝 Request: `{request_text}`\n🤖 Auto-Search: `Not Found`"
            )
            
        await msg.edit_text("⏳ **মুভিটি আমাদের ডাটাবেসে পাওয়া যায়নি।**\n\nআপনার রিকোয়েস্টটি অ্যাডমিনদের কাছে পাঠানো হয়েছে। খুব দ্রুত এটি আপলোড করা হবে!")
        user_conversations.pop(uid, None)
        return

    # ---------------------------------------------------------
    # OTHER STATES
    # ---------------------------------------------------------
    if state == "admin_broadcast_wait":
        if uid != OWNER_ID: return
        msg = await message.reply_text("📣 **Broadcasting...**")
        async for u in users_collection.find({}):
            try: await message.copy(chat_id=u['_id']); await asyncio.sleep(0.05)
            except: pass
        await msg.edit_text("✅ Broadcast complete.")
        user_conversations.pop(uid, None)
        return
        
    elif state == "admin_add_prem_wait":
        if uid != OWNER_ID: return
        try:
            await users_collection.update_one({'_id': int(text)}, {'$set': {'is_premium': True}}, upsert=True)
            await message.reply_text(f"✅ Premium Added to ID: `{text}`")
        except: await message.reply_text("❌ Invalid ID.")
        user_conversations.pop(uid, None)
        return
        
    elif state == "admin_rem_prem_wait":
        if uid != OWNER_ID: return
        try:
            await users_collection.update_one({'_id': int(text)}, {'$set': {'is_premium': False}})
            await message.reply_text(f"✅ Premium Removed from ID: `{text}`")
        except: await message.reply_text("❌ Invalid ID.")
        user_conversations.pop(uid, None)
        return

    if state == "wait_batch_season_input":
        prefix = text.strip()
        convo["batch_season_prefix"] = prefix
        convo["is_batch_mode"] = True
        convo["episode_counts"] = {}
        convo["state"] = "wait_panel"
        
        await message.reply_text(
            f"🟢 **Batch Mode Active**\nPrefix: `{prefix}`\n\n"
            f"👉 **Now click a Quality (e.g. 720p) from the panel to upload episodes!**\n"
            f"Naming: **{prefix} E1, {prefix} E2...**",
        )
        await show_upload_panel(message, uid, is_edit=False)
        return

    if state == "wait_manual_title":
        convo["details"]["title"] = text
        convo["details"]["name"] = text
        convo["state"] = "wait_manual_year"
        await message.reply_text("✅ Title Saved.\n\n📅 **Send Year:** (e.g. 2024)\n_Send 'skip' to leave empty._")
        
    elif state == "wait_manual_year":
        if text.lower() == "skip":
            convo["details"]["release_date"] = "----"; convo["details"]["first_air_date"] = "----"
        else:
            convo["details"]["release_date"] = f"{text}-01-01"; convo["details"]["first_air_date"] = f"{text}-01-01"
        convo["state"] = "wait_manual_rating"
        await message.reply_text("✅ Year Saved.\n\n⭐ **Send Rating:** (e.g. 7.5)\n_Send 'skip' to leave empty._")
        
    elif state == "wait_manual_rating":
        try: convo["details"]["vote_average"] = float(text)
        except: convo["details"]["vote_average"] = 0.0
        convo["state"] = "wait_manual_genres"
        await message.reply_text("✅ Rating Saved.\n\n🎭 **Send Genres:** (e.g. Action, Drama)\n_Send 'skip' to leave empty._")
        
    elif state == "wait_manual_genres":
        if text.lower() == "skip": convo["details"]["genres"] = []
        else: convo["details"]["genres"] = [{"name": g.strip()} for g in text.split(",")]
        convo["state"] = "wait_manual_poster"
        await message.reply_text("✅ Genres Saved.\n\n🖼 **Send Poster Photo:**")
        
    elif state == "wait_manual_poster":
        if not message.photo: return await message.reply_text("❌ Please send a Photo.")
        
        msg = await message.reply_text("⬇️ Downloading poster...")
        try:
            photo_path = await client.download_media(message, file_name=f"poster_{uid}_{int(time.time())}.jpg")
            convo["details"]["poster_local_path"] = os.path.abspath(photo_path) 
            await msg.delete()
            convo["state"] = "wait_lang"
            await message.reply_text("✅ Poster Saved.\n\n🌐 **Enter Language:**")
        except Exception as e:
            await msg.edit_text(f"❌ Error: {e}")

    elif state == "wait_lang" and convo.get("is_manual"):
        convo["language"] = text
        await show_upload_panel(message, uid, is_edit=False)
        
    elif state == "wait_custom_lang":
        convo["language"] = text
        await message.reply_text(f"✅ Language Set: **{text}**")
        await show_upload_panel(message, uid, is_edit=False)

    elif state == "wait_badge_text":
        convo["temp_badge_text"] = text
        await show_upload_panel(message, uid, is_edit=False)

    elif state == "wait_custom_btn_name":
        convo["temp_btn_name"] = text
        convo["current_quality"] = "custom"
        convo["state"] = "wait_file_upload"
        await message.reply_text(f"📤 **Upload File for: '{text}'**\n👉 Send Video/File now.")

    elif state == "wait_file_for_edit":
        if not (message.video or message.document):
            return await message.reply_text("❌ Please send a **Video** or **Document** file.")
        
        btn_msg = await message.reply_text(
            "📝 **File Received!**\n\n👉 **Enter Button Name:**\n(e.g. `Episode 2`, `1080p Link`)"
        )
        convo["state"] = "wait_btn_name_for_edit"
        convo["pending_file_msg"] = message 
        return

    elif state == "wait_btn_name_for_edit":
        button_name = text
        chat_id = convo["edit_chat_id"]
        msg_id = convo["edit_msg_id"]
        old_markup = convo["old_markup"]
        file_msg = convo["pending_file_msg"]
        
        status_msg = await message.reply_text("🔄 **Processing & Updating Channel Post...**")
        
        try:
            log_msg = await file_msg.copy(chat_id=LOG_CHANNEL_ID, caption=f"#UPDATE_POST\nUser: {uid}\nItem: {button_name}")
            backup_file_id = log_msg.video.file_id if log_msg.video else log_msg.document.file_id
            
            code = generate_random_code()
            user_data = await users_collection.find_one({'_id': uid})
            file_caption = f"🎬 **{button_name}**\n━━━━━━━━━━━━━━\n🤖 @{await get_bot_username()}"
            
            await files_collection.insert_one({
                "code": code, "file_id": backup_file_id, "log_msg_id": log_msg.id,
                "caption": file_caption, "delete_timer": user_data.get('delete_timer', 0),
                "uploader_id": uid, "created_at": datetime.now()
            })
            
            bot_uname = await get_bot_username()
            if BLOG_URL and "http" in BLOG_URL:
                base_blog = BLOG_URL.rstrip("/")
                final_long_url = f"{base_blog}/?code={code}"
            else:
                final_long_url = f"https://t.me/{bot_uname}?start={code}"
            
            short_link = await shorten_link(uid, final_long_url)
            
            new_button = InlineKeyboardButton(button_name, url=short_link)
            current_keyboard = old_markup.inline_keyboard if old_markup else []
            
            if current_keyboard and len(current_keyboard[-1]) < 3 and "Episode" in button_name:
                current_keyboard[-1].append(new_button)
            else:
                current_keyboard.append([new_button])
                
            await client.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=msg_id,
                reply_markup=InlineKeyboardMarkup(current_keyboard)
            )
            
            convo["repost_data"] = {
                "chat_id": chat_id,
                "message_id": msg_id,
                "update_text": button_name
            }
            
            await status_msg.edit_text(
                f"✅ **Successfully Added: {button_name}**\n"
                f"The old post has been updated.\n\n"
                f"🚀 **Do you want to Repost to Channel?**\n"
                f"(So users get a notification)",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚀 Repost Full Post (Fresh)", callback_data="repost_full")],
                    [InlineKeyboardButton("🔔 Send Update Alert Only", callback_data="repost_alert")],
                    [InlineKeyboardButton("❌ Done (No Post)", callback_data="close_post")]
                ])
            )
            
        except Exception as e:
            logger.error(f"Edit Post Error: {e}")
            await status_msg.edit_text(f"❌ **Error:** {str(e)}")
        return

    elif state == "wait_file_upload":
        if not (message.video or message.document):
            return await message.reply_text("❌ Please send a **Video** or **Document** file.")
        
        is_batch = convo.get("is_batch_mode", False)
        current_qual = convo.get("current_quality", "custom")
        
        if current_qual not in convo.get("episode_counts", {}):
            if "episode_counts" not in convo:
                convo["episode_counts"] = {}
            convo["episode_counts"][current_qual] = 1

        count = convo["episode_counts"][current_qual]

        if is_batch:
            season_prefix = convo.get("batch_season_prefix", None)
            btn_name = f"{season_prefix} E{count}" if season_prefix else f"Episode {count}"
        elif current_qual == "custom":
            btn_name = convo.get("temp_btn_name", "Custom")
            current_qual = btn_name 
        else:
            btn_name = current_qual
        
        status_msg = await message.reply_text(f"🔄 **Processing '{btn_name}'...**")
        
        try:
            log_msg = await message.copy(chat_id=LOG_CHANNEL_ID, caption=f"#BACKUP\nUser: {uid}\nItem: {btn_name}")
            backup_file_id = log_msg.video.file_id if log_msg.video else log_msg.document.file_id
            
            details = convo['details']
            title = details.get('title') or details.get('name') or "Unknown"
            
            date = details.get("release_date") or details.get("first_air_date") or "----"
            year = date[:4]
            
            lang = convo.get("language", "Unknown")
            
            if isinstance(details.get("genres"), list) and len(details["genres"]) > 0:
                if isinstance(details["genres"][0], dict):
                    genre_str = ", ".join([g["name"] for g in details.get("genres", [])[:3]])
                else:
                    genre_str = str(details.get("genres")[0])
            else:
                genre_str = "N/A"

            file_caption = (
                f"🎬 **{title} ({year})**\n"
                f"🔰 **Quality:** {btn_name}\n"
                f"🔊 **Language:** {lang}\n"
                f"🎭 **Genre:** {genre_str}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🤖 @{await get_bot_username()}"
            )
            
            code = generate_random_code()
            user_data = await users_collection.find_one({'_id': uid})
            
            await files_collection.insert_one({
                "code": code, 
                "file_id": backup_file_id, 
                "log_msg_id": log_msg.id,
                "caption": file_caption, 
                "delete_timer": user_data.get('delete_timer', 0),
                "uploader_id": uid, 
                "created_at": datetime.now()
            })
            
            bot_uname = await get_bot_username()
            
            if BLOG_URL and "http" in BLOG_URL:
                base_blog = BLOG_URL.rstrip("/")
                final_long_url = f"{base_blog}/?code={code}"
            else:
                final_long_url = f"https://t.me/{bot_uname}?start={code}"
            
            short_link = await shorten_link(uid, final_long_url)
            
            # 🆕 FOLDER SAVING LOGIC
            if 'links' not in convo: convo['links'] = {}
            if current_qual not in convo['links']: convo['links'][current_qual] = []

            convo['links'][current_qual].append({
                "name": btn_name,
                "link": short_link
            })
            
            await message.delete()
            
            if is_batch:
                convo["episode_counts"][current_qual] += 1
                await status_msg.edit_text(
                    f"✅ **{btn_name} ({current_qual}) Saved!**\n\n👇 **পরবর্তী এপিসোড সেন্ড করুন...**\n(Or click Finish to return)",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Finish & Return", callback_data="back_panel")]])
                )
            else:
                await show_upload_panel(status_msg, uid, is_edit=False)
            
        except Exception as e:
            logger.error(f"Upload Error: {e}")
            await status_msg.edit_text(f"❌ **Error:** {str(e)}")

# ==============================================================================
# 12. FINAL POST PROCESSING
# ==============================================================================

@bot.on_callback_query(filters.regex("^proc_final"))
async def process_final_post(client, cb: CallbackQuery):
    uid = cb.from_user.id
    convo = user_conversations.get(uid)
    
    if not convo: return await cb.answer("Session expired.", show_alert=True)
    if not convo.get('links'): return await cb.answer("❌ No files uploaded!", show_alert=True)
        
    await cb.message.edit_text("🖼️ **Generating Post & Folders... Please wait...**")
    
    details = convo['details']
    m_type = details.get('media_type', 'movie')
    m_id = details.get('id')
    
    trailer_url = None
    if m_id and not convo.get('is_manual'):
        trailer_url = await asyncio.to_thread(get_tmdb_trailer, m_type, m_id)
    
    caption = await generate_channel_caption(
        convo['details'], convo.get('language', 'Unknown'), convo['links'], 
        is_manual=convo.get("is_manual", False), trailer_url=trailer_url
    )
    
    buttons = []
    priority = ["480p", "720p", "1080p"]
    
    def sort_key(k):
        if k in priority: return priority.index(k)
        nums = re.findall(r'\d+', k)
        if nums:
            try: return 100 + int(nums[-1])
            except: return 200
        return 300

    sorted_keys = sorted(convo['links'].keys(), key=sort_key)

    temp_row = []
    for qual in sorted_keys:
        files = convo['links'][qual]
        if not files:
            continue

        if len(files) == 1:
            # 📄 Single file, Normal button
            link = files[0]['link']
            btn_name = files[0]['name']

            if qual in priority:
                btn_text = f"📥 Download {qual}"
            else:
                btn_text = btn_name.replace("Episode", "Ep")

            if qual in priority:
                if temp_row:
                    buttons.append(temp_row)
                    temp_row = []
                buttons.append([InlineKeyboardButton(btn_text, url=link)])
            else:
                temp_row.append(InlineKeyboardButton(btn_text, url=link))
                if len(temp_row) == 3:
                    buttons.append(temp_row)
                    temp_row = []
        else:
            # 📁 Multiple files -> Folder button
            if temp_row:
                buttons.append(temp_row)
                temp_row = []

            batch_code = "b_" + generate_random_code(8)
            await batches_collection.insert_one({
                "batch_code": batch_code,
                "title": details.get('title') or details.get('name') or "Web Series",
                "quality": qual,
                "files": files # List of {'name': 'Ep 1', 'link': 'url'}
            })

            bot_uname = await get_bot_username()
            if BLOG_URL and "http" in BLOG_URL:
                base_blog = BLOG_URL.rstrip("/")
                final_long_url = f"{base_blog}/?code={batch_code}"
            else:
                final_long_url = f"https://t.me/{bot_uname}?start={batch_code}"

            short_link = await shorten_link(uid, final_long_url)

            folder_text = f"📁 {qual} Web Series (Folder)" if qual in priority else f"📁 {qual} (Folder)"
            buttons.append([InlineKeyboardButton(folder_text, url=short_link)])
            
    if temp_row:
        buttons.append(temp_row)
        
    user_data = await users_collection.find_one({'_id': uid})
    if user_data.get('tutorial_url'):
        buttons.append([InlineKeyboardButton("ℹ️ How to Download", url=user_data['tutorial_url'])])
    
    poster_input = None
    if details.get('poster_local_path') and os.path.exists(details['poster_local_path']):
        poster_input = details['poster_local_path']
    elif details.get('poster_path'):
        poster_input = f"https://image.tmdb.org/t/p/w500{details['poster_path']}"
        
    poster_buffer, error = await asyncio.to_thread(
        watermark_poster, poster_input, user_data.get('watermark_text'), convo.get('temp_badge_text')
    )
    
    if not poster_buffer: return await cb.message.edit_text(f"❌ Image Error: {error}")
    
    poster_buffer.seek(0)
    try:
        preview_msg = await client.send_photo(
            chat_id=uid, photo=poster_buffer, caption=caption, reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        return await cb.message.edit_text(f"❌ Failed to send preview: {e}")

    await cb.message.delete()
    
    convo['final_post_data'] = {
        'file_id': preview_msg.photo.file_id, 'caption': caption, 'buttons': buttons
    }
    
    channels = user_data.get('channel_ids', [])
    channel_btns = []
    
    if channels:
        for cid in channels:
            channel_btns.append([InlineKeyboardButton(f"📢 Post to: {cid}", callback_data=f"sndch_{cid}")])
    else:
        await client.send_message(uid, "⚠️ **No Channels Saved!** Add using `/addchannel <id>`.")
    
    channel_btns.append([InlineKeyboardButton("✅ DONE / CLOSE", callback_data="close_post")])
    
    await client.send_message(uid, "👇 **Select Channel to Publish:**", reply_markup=InlineKeyboardMarkup(channel_btns))

@bot.on_callback_query(filters.regex("^sndch_"))
async def send_to_channel_handler(client, cb: CallbackQuery):
    uid = cb.from_user.id
    target_cid = cb.data.split("_")[1]
    convo = user_conversations.get(uid)
    
    if not convo or 'final_post_data' not in convo: return await cb.answer("❌ Session Expired.", show_alert=True)
    
    data = convo['final_post_data']
    try:
        await client.send_photo(
            chat_id=int(target_cid), photo=data['file_id'], caption=data['caption'], reply_markup=InlineKeyboardMarkup(data['buttons'])
        )
        await cb.answer(f"✅ Posted to {target_cid}", show_alert=True)
    except Exception as e:
        await cb.answer(f"❌ Failed: {e}", show_alert=True)

@bot.on_callback_query(filters.regex("^close_post"))
async def close_post_handler(client, cb: CallbackQuery):
    uid = cb.from_user.id
    if uid in user_conversations:
        local_path = user_conversations[uid].get('details', {}).get('poster_local_path')
        if local_path and os.path.exists(local_path):
            try: os.remove(local_path)
            except: pass     
        user_conversations.pop(uid, None)
        
    await cb.message.delete()
    await cb.answer("✅ Session Closed.", show_alert=True)

if __name__ == "__main__":
    logger.info("🚀 Bot is starting...")
    bot.run()
