# -*- coding: utf-8 -*-

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

# ---- 1. CONFIGURATION AND SETUP ----
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
FORCE_SUB_CHANNEL = os.getenv("FORCE_SUB_CHANNEL")
INVITE_LINK = os.getenv("INVITE_LINK")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# ⭐️ Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---- ✨ MongoDB Database Setup ✨ ----
DB_URI = os.getenv("DATABASE_URI")
DB_NAME = os.getenv("DATABASE_NAME", "MovieBotDB")
if not DB_URI:
    logger.critical("CRITICAL: DATABASE_URI is not set. Bot cannot start without a database.")
    exit()
db_client = motor.motor_asyncio.AsyncIOMotorClient(DB_URI)
db = db_client[DB_NAME]
users_collection = db.users
files_collection = db.files  # New collection for File Store

# ---- Global Variables & Bot Initialization ----
user_conversations = {}
bot = Client("UltimateMovieBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
BOT_USERNAME = ""

# ---- Flask App (for Keep-Alive) ----
app = Flask(__name__)
@app.route('/')
def home(): return "✅ Bot is Running!"
Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080))), daemon=True).start()

# ---- 2. DECORATORS AND HELPER FUNCTIONS ----

async def get_bot_username():
    global BOT_USERNAME
    if not BOT_USERNAME:
        me = await bot.get_me()
        BOT_USERNAME = me.username
    return BOT_USERNAME

def humanbytes(size):
    if not size: return ""
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

def generate_random_code(length=8):
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))

async def auto_delete_message(client, chat_id, message_id, delay_seconds):
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
        try:
            await client.delete_messages(chat_id, message_id)
        except: pass

def download_cascade():
    cascade_file = "haarcascade_frontalface_default.xml"
    if not os.path.exists(cascade_file):
        url = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"
        try:
            r = requests.get(url, timeout=20); r.raise_for_status()
            with open(cascade_file, 'wb') as f: f.write(r.content)
        except: return None
    return cascade_file

def download_font():
    font_file = "HindSiliguri-Bold.ttf"
    if not os.path.exists(font_file):
        url = "https://github.com/google/fonts/raw/main/ofl/hindsiliguri/HindSiliguri-Bold.ttf"
        try:
            r = requests.get(url, timeout=20); r.raise_for_status()
            with open(font_file, 'wb') as f: f.write(r.content)
        except: return None
    return font_file

# --- DATABASE & PREMIUM HELPERS ---

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
    if user_id == OWNER_ID: return True
    user_data = await users_collection.find_one({'_id': user_id})
    return user_data.get('is_premium', False) if user_data else False

# --- DECORATORS ---

def force_subscribe(func):
    async def wrapper(client, message):
        if FORCE_SUB_CHANNEL:
            try:
                chat_id = int(FORCE_SUB_CHANNEL) if FORCE_SUB_CHANNEL.startswith("-100") else FORCE_SUB_CHANNEL
                await client.get_chat_member(chat_id, message.from_user.id)
            except UserNotParticipant:
                # Handle Deep Link Start (Allow start parameter even if not joined, but block content)
                if len(message.command) > 1:
                    start_arg = message.command[1]
                    join_link = INVITE_LINK or f"https://t.me/{FORCE_SUB_CHANNEL.replace('@', '')}"
                    # We ask them to join, then click "Try Again" which is the same start link
                    bot_uname = await get_bot_username()
                    return await message.reply_text(
                        "❗ **You must join our channel to download this file.**", 
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("👉 Join Channel", url=join_link)],
                            [InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{bot_uname}?start={start_arg}")]
                        ])
                    )
                else:
                    join_link = INVITE_LINK or f"https://t.me/{FORCE_SUB_CHANNEL.replace('@', '')}"
                    return await message.reply_text(
                        "❗ **You must join our channel to use this bot.**", 
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👉 Join Channel", url=join_link)]])
                    )
        await func(client, message)
    return wrapper

def check_premium(func):
    async def wrapper(client, message):
        user_id = message.from_user.id
        if await is_user_premium(user_id):
            await func(client, message)
        else:
            await message.reply_text(
                "⛔ **Access Denied!**\nPremium Feature. Contact Admin.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👑 Contact Admin", user_id=OWNER_ID)]])
            )
    return wrapper

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
        else: return long_url
    except: return long_url

def format_runtime(minutes: int):
    if not minutes or not isinstance(minutes, int): return "N/A"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

# ---- 3. IMAGE PROCESSING (FULL ORIGINAL LOGIC) ----

def watermark_poster(poster_input, watermark_text: str, badge_text: str = None):
    if not poster_input: return None, "Poster not found."
    try:
        if isinstance(poster_input, str):
            img_data = requests.get(poster_input, timeout=20).content
            original_img = Image.open(io.BytesIO(img_data)).convert("RGBA")
        else:
            original_img = Image.open(poster_input).convert("RGBA")
        
        img = Image.new("RGBA", original_img.size)
        img.paste(original_img)
        draw = ImageDraw.Draw(img)

        # ---- Badge Text Logic ----
        if badge_text:
            badge_font_size = int(img.width / 9)
            font_path = download_font()
            try: badge_font = ImageFont.truetype(font_path, badge_font_size) if font_path else ImageFont.load_default()
            except: badge_font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            x = (img.width - text_width) / 2
            
            # Face Detection
            y_pos = img.height * 0.03
            cascade_path = download_cascade()
            if cascade_path:
                try:
                    cv_image = np.array(original_img.convert('RGB'))
                    gray = cv2.cvtColor(cv_image, cv2.COLOR_RGB2GRAY)
                    face_cascade = cv2.CascadeClassifier(cascade_path)
                    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
                    
                    padding = int(badge_font_size * 0.2)
                    text_box_y1 = y_pos + text_height + padding
                    is_collision = any(y_pos < (fy + fh) and text_box_y1 > fy for (fx, fy, fw, fh) in faces)
                    
                    if is_collision: y_pos = img.height * 0.25
                except: pass

            y = y_pos
            padding = int(badge_font_size * 0.15)
            rect_layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
            rect_draw = ImageDraw.Draw(rect_layer)
            rect_draw.rectangle((x - padding, y - padding, x + text_width + padding, y + text_height + padding), fill=(0, 0, 0, 160))
            img = Image.alpha_composite(img, rect_layer)
            draw = ImageDraw.Draw(img)

            gradient = Image.new('RGBA', (text_width, text_height + int(padding)), (0, 0, 0, 0))
            gradient_draw = ImageDraw.Draw(gradient)
            
            gradient_start_color = (255, 255, 0)
            gradient_end_color = (255, 69, 0)
            for i in range(text_width):
                ratio = i / text_width
                r = int(gradient_start_color[0] * (1 - ratio) + gradient_end_color[0] * ratio)
                g = int(gradient_start_color[1] * (1 - ratio) + gradient_end_color[1] * ratio)
                b = int(gradient_start_color[2] * (1 - ratio) + gradient_end_color[2] * ratio)
                gradient_draw.line([(i, 0), (i, text_height + padding)], fill=(r, g, b, 255))
            
            mask = Image.new('L', (text_width, text_height + int(padding)), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.text((0, 0), badge_text, font=badge_font, fill=255)
            
            try: img.paste(gradient, (int(x), int(y)), mask)
            except: draw.text((x, y), badge_text, font=badge_font, fill="white")

        # ---- Watermark Logic ----
        if watermark_text:
            font_size = int(img.width / 12)
            try:
                font_path = download_font()
                font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
            except: font = ImageFont.load_default()
            
            thumbnail = img.resize((150, 150))
            colors = thumbnail.getcolors(150*150)
            text_color = (255, 255, 255, 230)
            if colors:
                dominant_color = sorted(colors, key=lambda x: x[0], reverse=True)[0][1]
                text_color = (255 - dominant_color[0], 255 - dominant_color[1], 255 - dominant_color[2], 230)

            bbox = draw.textbbox((0, 0), watermark_text, font=font)
            text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            wx = (img.width - text_width) / 2
            wy = img.height - text_height - (img.height * 0.05)
            draw.text((wx + 2, wy + 2), watermark_text, font=font, fill=(0, 0, 0, 128))
            draw.text((wx, wy), watermark_text, font=font, fill=text_color)
            
        buffer = io.BytesIO()
        buffer.name = "poster.png"
        img.convert("RGB").save(buffer, "PNG")
        buffer.seek(0)
        return buffer, None
    except Exception as e:
        return None, f"Image processing error. Error: {e}"

# ---- 4. TMDB & CONTENT GENERATION ----

def search_tmdb(query: str):
    year, name = None, query.strip()
    match = re.search(r'(.+?)\s*\(?(\d{4})\)?$', query)
    if match: name, year = match.group(1).strip(), match.group(2)
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={name}&include_adult=true" + (f"&year={year}" if year else "")
    try: return [res for res in requests.get(url).json().get("results", []) if res.get("media_type") in ["movie", "tv"]][:5]
    except: return []

def get_tmdb_details(media_type: str, media_id: int):
    url = f"https://api.themoviedb.org/3/{media_type}/{media_id}?api_key={TMDB_API_KEY}"
    try: return requests.get(url).json()
    except: return None

async def generate_channel_caption(data: dict, language: str, short_links: dict):
    # Determine Genre
    if isinstance(data.get("genres"), list) and len(data["genres"]) > 0:
        genre_str = ", ".join([g["name"] for g in data.get("genres", [])[:3]])
    else: genre_str = str(data.get("genres", "N/A"))

    date = data.get("release_date") or data.get("first_air_date") or "----"
    
    info = {
        "title": data.get("title") or data.get("name") or "N/A",
        "year": date[:4],
        "genres": genre_str,
        "rating": f"{data.get('vote_average', 0):.1f}",
        "language": language,
        "runtime": format_runtime(data.get("runtime", 0) if 'runtime' in data else (data.get("episode_run_time") or [0])[0]),
    }

    caption_header = f"""🎬 **{info['title']} ({info['year']})**
━━━━━━━━━━━━━━━━━━━━━━━
⭐ **Rating:** {info['rating']}/10
🎭 **Genre:** {info['genres']}
🔊 **Language:** {info['language']}
⏰ **Runtime:** {info['runtime']}
━━━━━━━━━━━━━━━━━━━━━━━"""

    download_section = "👀 𝗪𝗔𝗧𝗖𝗛 𝗢𝗡𝗟𝗜𝗡𝗘/📤𝗗𝗢𝗪𝗡𝗟𝗢𝗔𝗗\n👇  ℍ𝕚𝕘𝕙 𝕊𝕡𝕖𝕖𝕕 | ℕ𝕠 𝔹𝕦𝕗𝕗𝕖𝕣𝕚𝕟𝕘  👇"
    
    # We don't put buttons here (buttons are inline), but if you want text links:
    # Here we typically just return the text description. The buttons are added by the bot.
    
    static_footer = """Movie ReQuest Group 
👇👇👇
https://t.me/Terabox_search_group

Premium Backup Group link 👇👇👇
https://t.me/+GL_XAS4MsJg4ODM1"""

    return f"{caption_header}\n\n{download_section}\n\n{static_footer}"

# ---- 5. BOT HANDLERS ----

@bot.on_message(filters.command("start") & filters.private)
@force_subscribe
async def start_cmd(client, message: Message):
    user = message.from_user
    uid = user.id
    await add_user_to_db(user)
    
    # --- DEEP LINK HANDLER (FILE RETRIEVAL) ---
    if len(message.command) > 1:
        code = message.command[1]
        file_data = await files_collection.find_one({"code": code})
        
        if file_data:
            msg = await message.reply_text("📂 **Fetching your file...**")
            
            # Use original file caption or generate fresh one
            caption = file_data.get("caption", "🎬 **Movie File**")
            caption += "\n\n**✅ Downloaded via MovieBot**"
            
            try:
                sent_msg = await client.send_cached_media(
                    chat_id=uid,
                    file_id=file_data["file_id"],
                    caption=caption
                )
                await msg.delete()
                
                # Auto Delete Logic
                timer = file_data.get("delete_timer", 0)
                if timer > 0:
                    asyncio.create_task(auto_delete_message(client, uid, sent_msg.id, timer))
                    await client.send_message(uid, f"⚠️ **File auto-delete enabled:** {int(timer/60)} Minutes.")
            except Exception as e:
                await msg.edit_text(f"❌ Error sending file: {e}")
        else:
            await message.reply_text("❌ **File link expired or invalid.**")
        return

    # --- NORMAL MENU ---
    if uid in user_conversations: del user_conversations[uid]
    is_premium = await is_user_premium(uid)
    is_owner = (uid == OWNER_ID)
    status_text = "💎 **Premium User**" if is_premium else "👤 **Free User**"
    
    if is_owner:
        welcome_text = f"👑 **Welcome Boss!**\n\nAdmin Control Panel:"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
             InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
            [InlineKeyboardButton("➕ Add Prem", callback_data="admin_add_premium"),
             InlineKeyboardButton("➖ Rem Prem", callback_data="admin_rem_premium")],
            [InlineKeyboardButton("⚙️ Set API", callback_data="set_api_help")]
        ])
    else:
        welcome_text = f"👋 **Hello {user.first_name}!**\nStatus: {status_text}\n\nUse `/post` to create new posts."
        user_buttons = [[InlineKeyboardButton("👤 Account", callback_data="my_account")]]
        if not is_premium: user_buttons.insert(0, [InlineKeyboardButton("💎 Buy Premium", user_id=OWNER_ID)])
        buttons = InlineKeyboardMarkup(user_buttons)

    await message.reply_text(welcome_text, reply_markup=buttons)

@bot.on_callback_query(filters.regex(r"^(admin_|my_account|set_api_help)"))
async def menu_callbacks(client, cb: CallbackQuery):
    data = cb.data
    uid = cb.from_user.id
    
    if data == "my_account":
        status = "Premium 💎" if await is_user_premium(uid) else "Free 👤"
        await cb.answer(f"Status: {status}", show_alert=True)
    elif data == "set_api_help":
        await cb.answer("Use /setapi and /setdomain commands", show_alert=True)
    elif data.startswith("admin_"):
        if uid != OWNER_ID: return
        if data == "admin_stats":
            total = await users_collection.count_documents({})
            await cb.answer(f"📊 Users: {total}", show_alert=True)
        elif data == "admin_broadcast":
            await cb.message.edit_text("📢 **Broadcast Mode**\nSend message to broadcast.")
            user_conversations[uid] = {"state": "admin_broadcast_wait"}
        elif data == "admin_add_premium":
            await cb.message.edit_text("➕ **Add Premium**\nSend User ID.")
            user_conversations[uid] = {"state": "admin_add_prem_wait"}
        elif data == "admin_rem_premium":
            await cb.message.edit_text("➖ **Remove Premium**\nSend User ID.")
            user_conversations[uid] = {"state": "admin_rem_prem_wait"}

# --- SETTINGS COMMANDS (Original + New) ---
@bot.on_message(filters.command(["setwatermark", "setapi", "setdomain", "settimer", "addchannel", "delchannel", "mychannels"]) & filters.private)
@force_subscribe
@check_premium
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
    elif cmd == "setdomain":
        if len(message.command) > 1:
            await users_collection.update_one({'_id': uid}, {'$set': {'shortener_url': message.command[1]}}, upsert=True)
            await message.reply_text("✅ Domain Saved.")
    elif cmd == "settimer":
        if len(message.command) > 1:
            try:
                secs = int(message.command[1]) * 60
                await users_collection.update_one({'_id': uid}, {'$set': {'delete_timer': secs}}, upsert=True)
                await message.reply_text(f"✅ Auto-Delete: {message.command[1]} mins.")
            except: await message.reply_text("Usage: `/settimer 10`")
    elif cmd == "addchannel":
        if len(message.command) > 1:
            await users_collection.update_one({'_id': uid}, {'$addToSet': {'channel_ids': message.command[1]}}, upsert=True)
            await message.reply_text("✅ Channel Added.")
    elif cmd == "delchannel":
        if len(message.command) > 1:
            await users_collection.update_one({'_id': uid}, {'$pull': {'channel_ids': message.command[1]}})
            await message.reply_text("✅ Channel Removed.")
    elif cmd == "mychannels":
        d = await users_collection.find_one({'_id': uid})
        chs = d.get('channel_ids', [])
        await message.reply_text(f"Channels:\n" + "\n".join(chs))

# ---- POST GENERATION (MANUAL & TMDB) ----

@bot.on_message(filters.command("post") & filters.private)
@force_subscribe
@check_premium
async def post_search(client, message: Message):
    if len(message.command) == 1: return await message.reply_text("**Usage:** `/post Name`")
    query = " ".join(message.command[1:]).strip()
    results = search_tmdb(query)
    
    buttons = []
    for r in results:
        m_type = r.get('media_type', 'movie')
        title = r.get('title') or r.get('name')
        year = (r.get('release_date') or r.get('first_air_date') or '----')[:4]
        buttons.append([InlineKeyboardButton(f"🎬 {title} ({year})", callback_data=f"sel_{m_type}_{r['id']}")])
    
    buttons.append([InlineKeyboardButton("📝 Create Manually", callback_data="manual_start")])
    await message.reply_text(f"🔍 Results for `{query}`", reply_markup=InlineKeyboardMarkup(buttons))

@bot.on_callback_query(filters.regex("^manual_"))
async def manual_handler(client, cb: CallbackQuery):
    data = cb.data
    uid = cb.from_user.id
    if data == "manual_start":
        await cb.message.edit_text("Type?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 Movie", callback_data="manual_type_movie"),
             InlineKeyboardButton("📺 Series", callback_data="manual_type_tv")]
        ]))
    elif data.startswith("manual_type_"):
        m_type = data.split("_")[2]
        user_conversations[uid] = {
            "details": {"media_type": m_type},
            "links": {},
            "state": "wait_manual_title",
            "is_manual": True
        }
        await cb.message.edit_text(f"📝 **Manual {m_type}**\nSend Title:")

@bot.on_callback_query(filters.regex("^sel_"))
async def selection_cb(client, cb: CallbackQuery):
    _, m_type, mid = cb.data.split("_")
    details = get_tmdb_details(m_type, mid)
    uid = cb.from_user.id
    user_conversations[uid] = {"details": details, "links": {}, "state": "wait_lang", "is_manual": False}
    
    langs = [["English", "Hindi"], ["Bengali", "Dual Audio"]]
    buttons = [[InlineKeyboardButton(l, callback_data=f"lang_{l}") for l in row] for row in langs]
    await cb.message.edit_text(f"Selected: **{details.get('title') or details.get('name')}**\nSelect Language:", reply_markup=InlineKeyboardMarkup(buttons))

@bot.on_callback_query(filters.regex("^lang_"))
async def lang_cb(client, cb: CallbackQuery):
    lang = cb.data.split("_")[1]
    user_conversations[cb.from_user.id]["language"] = lang
    await show_upload_panel(cb.message, cb.from_user.id)

async def show_upload_panel(message, uid):
    # This panel asks for FILES to generate short links
    buttons = [
        [InlineKeyboardButton("📤 Upload 480p", callback_data="up_480p")],
        [InlineKeyboardButton("📤 Upload 720p", callback_data="up_720p")],
        [InlineKeyboardButton("📤 Upload 1080p", callback_data="up_1080p")],
        [InlineKeyboardButton("🇧🇩 Badge: Bangla", callback_data="bdg_bangla"),
         InlineKeyboardButton("⏭ Skip Badge", callback_data="bdg_skip")],
         [InlineKeyboardButton("✅ DONE & POST", callback_data="proc_final")]
    ]
    
    links = user_conversations[uid].get('links', {})
    badge = user_conversations[uid].get('temp_badge_text', 'Not Set')
    status = "\n".join([f"✅ {k} Added" for k in links.keys()])
    text = f"📂 **Files Added:**\n{status}\n\n🏷 **Badge:** {badge}\n\n👇 **Select Quality to Upload File:**"
    
    if isinstance(message, Message): await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else: await message.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

@bot.on_callback_query(filters.regex("^up_"))
async def upload_req(client, cb: CallbackQuery):
    qual = cb.data.split("_")[1]
    uid = cb.from_user.id
    user_conversations[uid]["current_quality"] = qual
    user_conversations[uid]["state"] = "wait_file_upload"
    await cb.message.edit_text(f"📤 **Send Video for {qual}**\n\n(Forward file here, bot will shorten it)", 
                               reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_panel")]]))

@bot.on_callback_query(filters.regex("^bdg_"))
async def badge_cb(client, cb: CallbackQuery):
    action = cb.data.split("_")[1]
    uid = cb.from_user.id
    if action == "bangla": user_conversations[uid]['temp_badge_text'] = "বাংলা ডাবিং"
    elif action == "skip": user_conversations[uid]['temp_badge_text'] = None
    await show_upload_panel(cb, uid)

@bot.on_callback_query(filters.regex("^back_panel"))
async def back_p(client, cb): await show_upload_panel(cb, cb.from_user.id)

@bot.on_message(filters.private & (filters.text | filters.photo | filters.video | filters.document))
@check_premium
async def main_handler(client, message: Message):
    uid = message.from_user.id
    convo = user_conversations.get(uid)
    if not convo or "state" not in convo: return
    
    state = convo["state"]
    text = message.text
    
    # --- ADMIN ---
    if state == "admin_broadcast_wait":
        if uid != OWNER_ID: return
        msg = await message.reply_text("Broadcasting...")
        c = 0
        async for u in users_collection.find({}):
            try:
                await message.copy(u['_id'])
                c+=1
                await asyncio.sleep(0.1)
            except: pass
        await msg.edit_text(f"Sent to {c}")
        del user_conversations[uid]
        return

    # --- MANUAL MODE ---
    if state == "wait_manual_title":
        convo["details"]["title"] = text
        convo["details"]["name"] = text
        convo["state"] = "wait_manual_year"
        await message.reply_text("Send Year (e.g. 2024):")
    elif state == "wait_manual_year":
        convo["details"]["release_date"] = f"{text}-01-01"
        convo["state"] = "wait_manual_rating"
        await message.reply_text("Send Rating (e.g. 8.5):")
    elif state == "wait_manual_rating":
        convo["details"]["vote_average"] = float(text)
        convo["state"] = "wait_manual_genres"
        await message.reply_text("Send Genres (e.g. Action, Drama):")
    elif state == "wait_manual_genres":
        convo["details"]["genres"] = [{"name": g.strip()} for g in text.split(",")]
        convo["state"] = "wait_manual_poster"
        await message.reply_text("Send Poster Photo:")
    elif state == "wait_manual_poster":
        if not message.photo: return await message.reply_text("Send Photo.")
        photo = await client.download_media(message, in_memory=True)
        convo["details"]["poster_bytes"] = photo
        convo["state"] = "wait_lang"
        await message.reply_text("Poster Saved. Type Language (e.g. Hindi):")
    elif state == "wait_lang" and convo.get("is_manual"):
        convo["language"] = text
        # Go to upload panel
        await show_upload_panel(message, uid)

    # --- FILE UPLOAD & SHORTENING ---
    elif state == "wait_file_upload" and (message.video or message.document):
        qual = convo["current_quality"]
        media = message.video or message.document
        
        # 1. Generate Link Code
        code = generate_random_code()
        bot_uname = await get_bot_username()
        
        # 2. Prepare File Caption
        details = convo['details']
        ftitle = details.get('title') or details.get('name') or "Movie"
        tmdb_caption = f"🎬 **{ftitle}**\n🔰 Quality: {qual}\n📦 Size: {humanbytes(media.file_size)}"
        
        # 3. Save to DB
        udata = await users_collection.find_one({'_id': uid})
        timer = udata.get('delete_timer', 0)
        
        await files_collection.insert_one({
            "code": code,
            "file_id": media.file_id,
            "caption": tmdb_caption,
            "delete_timer": timer,
            "uploader_id": uid,
            "created_at": datetime.now()
        })
        
        # 4. Shorten
        deep = f"https://t.me/{bot_uname}?start={code}"
        msg = await message.reply_text("🔄 Shortening...")
        short = await shorten_link(uid, deep)
        
        convo['links'][qual] = short
        await msg.edit_text(f"✅ {qual} Linked!")
        await show_upload_panel(message, uid)

@bot.on_callback_query(filters.regex("^proc_final"))
async def process_final(client, cb: CallbackQuery):
    uid = cb.from_user.id
    convo = user_conversations.get(uid)
    if not convo: return await cb.answer("Expired", show_alert=True)
    
    await cb.message.edit_text("🖼️ **Processing Image & Caption...**")
    
    # 1. Prepare Caption
    caption = await generate_channel_caption(convo['details'], convo.get('language', 'Unknown'), convo['links'])
    
    # 2. Prepare Buttons
    buttons = []
    for qual, link in convo['links'].items():
        buttons.append([InlineKeyboardButton(f"📥 Download {qual}", url=link)])
    
    # 3. Prepare Image (Watermark + Badge)
    details = convo['details']
    user_data = await users_collection.find_one({'_id': uid})
    watermark_text = user_data.get('watermark_text')
    badge_text = convo.get('temp_badge_text')
    
    poster_input = None
    if details.get('poster_bytes'):
        poster_input = details['poster_bytes']
        poster_input.seek(0)
    elif details.get('poster_path'):
        poster_input = f"https://image.tmdb.org/t/p/w500{details['poster_path']}"
        
    poster_buffer, error = watermark_poster(poster_input, watermark_text, badge_text)
    
    # 4. Send
    channels = user_data.get('channel_ids', [])
    if channels and poster_buffer:
        poster_buffer.seek(0)
        # We need to send to multiple channels, so we can't consume the buffer once.
        # Ideally upload once and get file_id, but here simple loop:
        for cid in channels:
            poster_buffer.seek(0)
            try: await client.send_photo(int(cid), poster_buffer, caption=caption, reply_markup=InlineKeyboardMarkup(buttons))
            except Exception as e: await cb.message.reply_text(f"Failed {cid}: {e}")
        await cb.message.edit_text("✅ Posted!")
    elif poster_buffer:
        poster_buffer.seek(0)
        await client.send_photo(uid, poster_buffer, caption=caption, reply_markup=InlineKeyboardMarkup(buttons))
        await cb.message.edit_text("✅ Preview Sent (No channels added).")
    else:
        await cb.message.edit_text(f"Error: {error}")
        
    if uid in user_conversations: del user_conversations[uid]

if __name__ == "__main__":
    bot.run()
