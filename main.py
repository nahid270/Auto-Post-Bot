# -*- coding: utf-8 -*-

# ==============================================================================
# 🎬 ULTIMATE MOVIE BOT - FINAL VERSION (VARIABLE FIX)
# ==============================================================================
# Fix Log:
# 1. Fixed 'UnboundLocalError: local variable msg' in file upload section.
# 2. Replaced 'msg.delete()' with 'message.delete()'.
# ==============================================================================

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
            if poster_input.startswith("http"): # URL
                img_data = requests.get(poster_input, timeout=15).content
                original_img = Image.open(io.BytesIO(img_data)).convert("RGBA")
            else: # Local File
                if os.path.exists(poster_input):
                    original_img = Image.open(poster_input).convert("RGBA")
                else:
                    return None, f"Local file not found: {poster_input}"
        else: # BytesIO
            original_img = Image.open(poster_input).convert("RGBA")
            
        if not original_img:
            return None, "Failed to load image."
        
        img = Image.new("RGBA", original_img.size)
        img.paste(original_img)
        draw = ImageDraw.Draw(img)

        # ---- Badge Logic ----
        if badge_text:
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

def search_tmdb(query: str):
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={query}&include_adult=true"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        return [res for res in results if res.get("media_type") in ["movie", "tv"]][:5]
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
        return r.json()
    except Exception:
        return None

# ==============================================================================
# CAPTION GENERATOR
# ==============================================================================
async def generate_channel_caption(data: dict, language: str, short_links: dict, is_manual: bool = False):
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
🔊 **Language:** {language}
━━━━━━━━━━━━━━━━━━━━━━━"""

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
    
    # --- FILE RETRIEVAL SYSTEM ---
    if len(message.command) > 1:
        code = message.command[1]
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
        welcome_text = f"👋 **Hello {user.first_name}!**\n\nYour Status: {status_text}\n\n👇 **Available Commands:**\n`/post` - Auto TMDB Post (Premium)\n`/manual` - Manual Post (Free for All)"
        
        user_buttons = [[InlineKeyboardButton("👤 My Account", callback_data="my_account")]]
        if not is_premium:
            user_buttons.insert(0, [InlineKeyboardButton("💎 Buy Premium Access", user_id=OWNER_ID)])
            
        buttons = InlineKeyboardMarkup(user_buttons)

    await message.reply_text(welcome_text, reply_markup=buttons)

# --- Callback Handler ---

@bot.on_callback_query(filters.regex(r"^(admin_|my_account|api_help)"))
async def callback_handler(client, cb: CallbackQuery):
    data = cb.data
    uid = cb.from_user.id
    
    if data == "my_account":
        status = "Premium 💎" if await is_user_premium(uid) else "Free 👤"
        await cb.answer(f"User: {cb.from_user.first_name}\nStatus: {status}", show_alert=True)
        
    elif data == "api_help":
        help_text = "**⚙️ Commands:**\n`/setapi <key>`\n`/setwatermark <text>`\n`/settutorial <link>`\n`/addchannel <id>`"
        await cb.answer(help_text, show_alert=True)
        
    elif data.startswith("admin_") and uid == OWNER_ID:
        if data == "admin_stats":
            total = await users_collection.count_documents({})
            prem = await users_collection.count_documents({'is_premium': True})
            await cb.answer(f"📊 Total Users: {total}\n💎 Premium: {prem}", show_alert=True)
            
        elif data == "admin_broadcast":
            await cb.message.edit_text("📢 **Broadcast Mode**\n\nSend message to broadcast.")
            user_conversations[uid] = {"state": "admin_broadcast_wait"}
            
        elif "add_premium" in data:
            await cb.message.edit_text("➕ **Add Premium**\n\nSend User ID.")
            user_conversations[uid] = {"state": "admin_add_prem_wait"}
            
        elif "rem_premium" in data:
            await cb.message.edit_text("➖ **Remove Premium**\n\nSend User ID.")
            user_conversations[uid] = {"state": "admin_rem_prem_wait"}

# --- Settings Commands ---

@bot.on_message(filters.command(["setwatermark", "setapi", "settimer", "addchannel", "delchannel", "mychannels", "settutorial"]) & filters.private)
@force_subscribe
async def settings_commands(client, message: Message):
    cmd = message.command[0].lower()
    uid = message.from_user.id
    
    if cmd == "setwatermark":
        text = " ".join(message.command[1:])
        await users_collection.update_one({'_id': uid}, {'$set': {'watermark_text': text}}, upsert=True)
        await message.reply_text(f"✅ Watermark set: `{text}`")

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
# 6. AUTO POST (TMDB)
# ==============================================================================

@bot.on_message(filters.command("post") & filters.private)
@force_subscribe
@check_premium
async def post_search_cmd(client, message: Message):
    if len(message.command) == 1:
        return await message.reply_text("**Usage:** `/post Movie Name`")
    
    query = " ".join(message.command[1:]).strip()
    msg = await message.reply_text(f"🔍 **Searching...**\n`{query}`")
    
    results = search_tmdb(query)
    
    buttons = []
    for r in results:
        m_type = r.get('media_type', 'movie')
        title = r.get('title') or r.get('name')
        year = (r.get('release_date') or r.get('first_air_date') or '----')[:4]
        buttons.append([InlineKeyboardButton(f"🎬 {title} ({year})", callback_data=f"sel_{m_type}_{r['id']}")])
    
    if not results:
        await msg.edit_text("❌ **No TMDB results found!**")
    else:
        await msg.edit_text(f"👇 **Found {len(results)} Result(s):**", reply_markup=InlineKeyboardMarkup(buttons))

# ==============================================================================
# 7. MANUAL POST SYSTEM
# ==============================================================================

@bot.on_message(filters.command("manual") & filters.private)
@force_subscribe
async def manual_cmd_start(client, message: Message):
    """Dedicated Manual Post Entry Point."""
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
# 8. UPLOAD PANEL & HANDLERS
# ==============================================================================

@bot.on_callback_query(filters.regex("^sel_"))
async def media_selected(client, cb: CallbackQuery):
    _, m_type, mid = cb.data.split("_")
    details = get_tmdb_details(m_type, mid)
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
    await cb.message.edit_text(f"✅ Selected: **{details.get('title') or details.get('name')}**\n\n🌐 **Select Language:**", reply_markup=InlineKeyboardMarkup(buttons))

@bot.on_callback_query(filters.regex("^lang_"))
async def language_selected(client, cb: CallbackQuery):
    lang = cb.data.split("_")[1]
    uid = cb.from_user.id
    user_conversations[uid]["language"] = lang
    # For Auto post, we can edit the message
    await show_upload_panel(cb.message, uid, is_edit=True)

async def show_upload_panel(message, uid, is_edit=False):
    """
    Shows the panel to upload files.
    Safely handles both Message objects (Reply) and Callback objects (Edit).
    """
    buttons = [
        [InlineKeyboardButton("📤 Upload 480p", callback_data="up_480p")],
        [InlineKeyboardButton("📤 Upload 720p", callback_data="up_720p")],
        [InlineKeyboardButton("📤 Upload 1080p", callback_data="up_1080p")],
        [InlineKeyboardButton("➕ Custom Button / Episode", callback_data="add_custom_btn")],
        [InlineKeyboardButton("🎨 Add Badge", callback_data="set_badge")],
        [InlineKeyboardButton("✅ FINISH & POST", callback_data="proc_final")]
    ]
    
    convo = user_conversations.get(uid, {})
    links = convo.get('links', {})
    badge = convo.get('temp_badge_text', 'None')
    
    status_text = "\n".join([f"✅ **{k}** Added" for k in links.keys()])
    if not status_text: status_text = "No files added yet."
    
    text = (f"📂 **File Manager**\n\n{status_text}\n\n"
            f"🏷 **Badge:** {badge}\n\n"
            f"👇 **Tap a button to upload a file for that quality:**")
    
    if is_edit:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        # Crucial for Manual Flow to prevent edit crash
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

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
    
    await cb.message.edit_text(
        f"📤 **Upload Mode: {qual}**\n\n"
        "👉 **Forward** or **Send** the video file here.\n"
        "🤖 Bot will backup to Log Channel & create a Short Link.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_panel")]])
    )

@bot.on_callback_query(filters.regex("^set_badge"))
async def badge_menu_handler(client, cb: CallbackQuery):
    uid = cb.from_user.id
    user_conversations[uid]["state"] = "wait_badge_text"
    await cb.message.edit_text("✍️ **Enter the text for the Badge:**\n(e.g., 4K HDR, Dual Audio)")

@bot.on_callback_query(filters.regex("^back_panel"))
async def back_button(client, cb: CallbackQuery):
    # Back button always edits
    await show_upload_panel(cb.message, cb.from_user.id, is_edit=True)

# ==============================================================================
# 9. MAIN MESSAGE HANDLER (TEXT & FILES)
# ==============================================================================

@bot.on_message(filters.private & (filters.text | filters.video | filters.document | filters.photo))
async def main_conversation_handler(client, message: Message):
    uid = message.from_user.id
    convo = user_conversations.get(uid)
    
    if not convo or "state" not in convo:
        return
    
    state = convo["state"]
    text = message.text
    
    # --- ADMIN BROADCAST & PREMIUM ---
    if state == "admin_broadcast_wait":
        if uid != OWNER_ID: return
        msg = await message.reply_text("📣 **Broadcasting...**")
        async for u in users_collection.find({}):
            try: await message.copy(chat_id=u['_id']); await asyncio.sleep(0.05)
            except: pass
        await msg.edit_text("✅ Broadcast complete.")
        del user_conversations[uid]
        return
        
    elif state == "admin_add_prem_wait":
        if uid != OWNER_ID: return
        try:
            await users_collection.update_one({'_id': int(text)}, {'$set': {'is_premium': True}}, upsert=True)
            await message.reply_text(f"✅ Premium Added to ID: `{text}`")
        except: await message.reply_text("❌ Invalid ID.")
        del user_conversations[uid]
        return
        
    elif state == "admin_rem_prem_wait":
        if uid != OWNER_ID: return
        try:
            await users_collection.update_one({'_id': int(text)}, {'$set': {'is_premium': False}})
            await message.reply_text(f"✅ Premium Removed from ID: `{text}`")
        except: await message.reply_text("❌ Invalid ID.")
        del user_conversations[uid]
        return

    # --- MANUAL MODE INPUTS (FIXED) ---
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
            # Just ask for language, no suggestions
            await message.reply_text("✅ Poster Saved.\n\n🌐 **Enter Language:**")
        except Exception as e:
            await msg.edit_text(f"❌ Error: {e}")

    elif state == "wait_lang" and convo.get("is_manual"):
        convo["language"] = text
        # Use Reply to avoid crashing
        await show_upload_panel(message, uid, is_edit=False)

    # --- BADGE & CUSTOM BUTTON ---
    elif state == "wait_badge_text":
        convo["temp_badge_text"] = text
        await show_upload_panel(message, uid, is_edit=False)

    elif state == "wait_custom_btn_name":
        convo["temp_btn_name"] = text
        convo["current_quality"] = "custom"
        convo["state"] = "wait_file_upload"
        await message.reply_text(f"📤 **Upload File for: '{text}'**\n👉 Send Video/File now.")

    # --- FILE UPLOAD LOGIC ---
    elif state == "wait_file_upload":
        if not (message.video or message.document):
            return await message.reply_text("❌ Please send a **Video** or **Document** file.")
        
        if convo["current_quality"] == "custom": btn_name = convo["temp_btn_name"]
        else: btn_name = convo["current_quality"]
        
        status_msg = await message.reply_text("🔄 **Processing File...**")
        
        try:
            # Forward to Log
            log_msg = await message.copy(chat_id=LOG_CHANNEL_ID, caption=f"#BACKUP\nUser: {uid}\nItem: {btn_name}")
            backup_file_id = log_msg.video.file_id if log_msg.video else log_msg.document.file_id
            
            # Save DB
            code = generate_random_code()
            details = convo['details']
            title = details.get('title') or details.get('name') or "Unknown"
            
            # Caption for File
            tmdb_caption = f"🎬 **Movie:** {title}\n🔰 **Quality:** {btn_name}"
            
            user_data = await users_collection.find_one({'_id': uid})
            
            await files_collection.insert_one({
                "code": code, "file_id": backup_file_id, "log_msg_id": log_msg.id,
                "caption": tmdb_caption, "delete_timer": user_data.get('delete_timer', 0),
                "uploader_id": uid, "created_at": datetime.now()
            })
            
            # Shorten
            bot_uname = await get_bot_username()
            short_link = await shorten_link(uid, f"https://t.me/{bot_uname}?start={code}")
            
            convo['links'][btn_name] = short_link
            
            await message.delete() # Corrected variable name
            # Reply with panel (fresh message)
            await show_upload_panel(status_msg, uid, is_edit=False)
            
        except Exception as e:
            logger.error(f"Upload Error: {e}")
            await status_msg.edit_text(f"❌ **Error:** {str(e)}")

# ==============================================================================
# 10. FINAL POST PROCESSING
# ==============================================================================

@bot.on_callback_query(filters.regex("^proc_final"))
async def process_final_post(client, cb: CallbackQuery):
    uid = cb.from_user.id
    convo = user_conversations.get(uid)
    
    if not convo: return await cb.answer("Session expired.", show_alert=True)
    if not convo['links']: return await cb.answer("❌ No files uploaded!", show_alert=True)
        
    await cb.message.edit_text("🖼️ **Generating Post...**")
    
    # 1. Caption
    caption = await generate_channel_caption(
        convo['details'], convo.get('language', 'Unknown'), convo['links'], is_manual=convo.get("is_manual", False)
    )
    
    # 2. Buttons
    buttons = []
    priority = ["480p", "720p", "1080p"]
    # Sort: Priority first, then others
    sorted_keys = sorted(convo['links'].keys(), key=lambda x: priority.index(x) if x in priority else 99)

    for qual in sorted_keys:
        link = convo['links'][qual]
        btn_text = f"📥 Download {qual}" if qual in priority else f"📥 {qual}"
        buttons.append([InlineKeyboardButton(btn_text, url=link)])
        
    user_data = await users_collection.find_one({'_id': uid})
    if user_data.get('tutorial_url'):
        buttons.append([InlineKeyboardButton("ℹ️ How to Download", url=user_data['tutorial_url'])])
    
    # 3. Poster Processing
    details = convo['details']
    poster_input = None
    if details.get('poster_local_path') and os.path.exists(details['poster_local_path']):
        poster_input = details['poster_local_path']
    elif details.get('poster_path'):
        poster_input = f"https://image.tmdb.org/t/p/w500{details['poster_path']}"
        
    poster_buffer, error = watermark_poster(poster_input, user_data.get('watermark_text'), convo.get('temp_badge_text'))
    
    if not poster_buffer: return await cb.message.edit_text(f"❌ Image Error: {error}")
    
    # 4. Send Preview
    poster_buffer.seek(0)
    try:
        preview_msg = await client.send_photo(
            chat_id=uid, photo=poster_buffer, caption=caption, reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        return await cb.message.edit_text(f"❌ Failed to send preview: {e}")

    await cb.message.delete()
    
    # 5. Store for Channel Post
    convo['final_post_data'] = {
        'file_id': preview_msg.photo.file_id, 'caption': caption, 'buttons': buttons
    }
    
    # 6. Channel Selection
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
        del user_conversations[uid]
        
    await cb.message.delete()
    await cb.answer("✅ Session Closed.", show_alert=True)

if __name__ == "__main__":
    logger.info("🚀 Bot is starting...")
    bot.run()
