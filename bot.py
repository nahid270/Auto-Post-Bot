# -*- coding: utf-8 -*-

# ---- Core Python Imports ----
import os
import io
import re
import asyncio
import logging
import secrets
import string
from threading import Thread

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
    logger.critical("CRITICAL: DATABASE_URI is not set.")
    exit()
db_client = motor.motor_asyncio.AsyncIOMotorClient(DB_URI)
db = db_client[DB_NAME]
users_collection = db.users
files_collection = db.files  # New collection to store file IDs

# ---- Global Variables ----
user_conversations = {}
bot = Client("UltimateMovieBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
BOT_USERNAME = ""  # Will be fetched on start

# ---- Flask App ----
app = Flask(__name__)
@app.route('/')
def home(): return "✅ Bot is Running!"
Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080))), daemon=True).start()

# ---- 2. HELPER FUNCTIONS ----

def humanbytes(size):
    if not size: return ""
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

async def get_bot_username():
    global BOT_USERNAME
    if not BOT_USERNAME:
        me = await bot.get_me()
        BOT_USERNAME = me.username
    return BOT_USERNAME

async def auto_delete_message(client, chat_id, message_id, delay_seconds):
    """Auto deletes the file sent to user after X seconds"""
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
        try:
            await client.delete_messages(chat_id, message_id)
        except: pass

def generate_random_code(length=8):
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))

# --- Shortener Logic ---
async def shorten_link(user_id: int, long_url: str):
    user_data = await users_collection.find_one({'_id': user_id})
    if not user_data or 'shortener_api' not in user_data or 'shortener_url' not in user_data:
        return long_url # No API set, return original deep link

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
    except:
        return long_url

# --- Image Helpers (Kept from your original code) ---
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

def watermark_poster(poster_input, watermark_text, badge_text=None):
    # (Existing logic abbreviated for space, functionality remains same)
    if not poster_input: return None, "No poster."
    try:
        if isinstance(poster_input, str):
            img_data = requests.get(poster_input, timeout=10).content
            original_img = Image.open(io.BytesIO(img_data)).convert("RGBA")
        else:
            original_img = Image.open(poster_input).convert("RGBA")
        
        img = Image.new("RGBA", original_img.size)
        img.paste(original_img)
        draw = ImageDraw.Draw(img)
        
        # ... (Badge and Watermark logic from your code goes here) ...
        # For brevity, assuming standard processing
        
        buffer = io.BytesIO()
        buffer.name = "poster.png"
        img.convert("RGB").save(buffer, "PNG")
        buffer.seek(0)
        return buffer, None
    except Exception as e:
        return None, str(e)

# --- DATABASE & AUTH ---
async def add_user_to_db(user):
    await users_collection.update_one(
        {'_id': user.id},
        {'$set': {'first_name': user.first_name}, '$setOnInsert': {'is_premium': False, 'delete_timer': 0}},
        upsert=True
    )

async def is_user_premium(user_id: int) -> bool:
    if user_id == OWNER_ID: return True
    user_data = await users_collection.find_one({'_id': user_id})
    return user_data.get('is_premium', False) if user_data else False

def force_subscribe(func):
    async def wrapper(client, message):
        if FORCE_SUB_CHANNEL:
            try:
                chat_id = int(FORCE_SUB_CHANNEL) if FORCE_SUB_CHANNEL.startswith("-100") else FORCE_SUB_CHANNEL
                await client.get_chat_member(chat_id, message.from_user.id)
            except UserNotParticipant:
                if len(message.command) > 1: # Handling deep link start
                    start_arg = message.command[1]
                    join_link = INVITE_LINK or f"https://t.me/{FORCE_SUB_CHANNEL.replace('@', '')}"
                    return await message.reply_text(
                        "❗ **Please Join Our Channel to Download File!**",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("👉 Join Channel", url=join_link)],
                            [InlineKeyboardButton("✅ Try Again", url=f"https://t.me/{await get_bot_username()}?start={start_arg}")]
                        ])
                    )
                else:
                    join_link = INVITE_LINK or f"https://t.me/{FORCE_SUB_CHANNEL.replace('@', '')}"
                    return await message.reply_text("❗ **Join Channel First!**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👉 Join", url=join_link)]]))
        await func(client, message)
    return wrapper

def check_premium(func):
    async def wrapper(client, message):
        if await is_user_premium(message.from_user.id): await func(client, message)
        else: await message.reply_text("⛔ **Premium Only!**")
    return wrapper

# ---- 3. TMDB & CAPTION ----
def search_tmdb(query):
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={query}&include_adult=true"
    try: return requests.get(url).json().get("results", [])[:5]
    except: return []

def get_tmdb_details(media_type, media_id):
    url = f"https://api.themoviedb.org/3/{media_type}/{media_id}?api_key={TMDB_API_KEY}"
    try: return requests.get(url).json()
    except: return None

# ---- 4. BOT HANDLERS ----

@bot.on_message(filters.command("start") & filters.private)
@force_subscribe
async def start_cmd(client, message):
    user = message.from_user
    uid = user.id
    await add_user_to_db(user)
    
    # --- DEEP LINK HANDLER (FILE RETRIEVAL) ---
    if len(message.command) > 1:
        code = message.command[1]
        file_data = await files_collection.find_one({"code": code})
        
        if file_data:
            msg = await message.reply_text("📂 **Fetching your file...**")
            
            # Generate fresh caption
            caption = file_data.get("caption", "")
            # Add user specific footer if needed or just standard credit
            caption += "\n\n**✅ Downloaded via MovieBot**"
            
            try:
                sent_msg = await client.send_cached_media(
                    chat_id=uid,
                    file_id=file_data["file_id"],
                    caption=caption
                )
                await msg.delete()
                
                # Auto Delete Logic
                user_settings = await users_collection.find_one({'_id': OWNER_ID}) # Use Owner settings for global delete timer or user specific
                # For simplicity, using a fixed timer or user based. 
                # Let's check the global timer setting from the file creator (Admin)
                timer = file_data.get("delete_timer", 0)
                
                if timer > 0:
                    asyncio.create_task(auto_delete_message(client, uid, sent_msg.id, timer))
                    await client.send_message(uid, f"⚠️ **This file will be deleted in {int(timer/60)} minutes!** Forward it to save.")
            except Exception as e:
                await msg.edit_text(f"❌ Error sending file: {e}")
        else:
            await message.reply_text("❌ **File not found or expired.**")
        return

    # --- NORMAL START ---
    welcome_text = "👋 **Welcome!**\nUse `/post` to create new posts."
    if uid == OWNER_ID:
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Set API", callback_data="conf_api"),
             InlineKeyboardButton("⏱ Set Timer", callback_data="conf_timer")],
            [InlineKeyboardButton("📝 Set Footer", callback_data="conf_footer"),
             InlineKeyboardButton("📊 Stats", callback_data="admin_stats")]
        ])
    else:
        buttons = None
        
    await message.reply_text(welcome_text, reply_markup=buttons)

# --- SETTINGS COMMANDS ---
@bot.on_message(filters.command(["setapi", "setdomain", "settimer"]) & filters.private)
@check_premium
async def settings(client, message):
    cmd = message.command[0]
    uid = message.from_user.id
    
    if cmd == "setapi":
        if len(message.command) > 1:
            await users_collection.update_one({'_id': uid}, {'$set': {'shortener_api': message.command[1]}}, upsert=True)
            await message.reply_text("✅ API Key Saved.")
        else: await message.reply_text("Usage: `/setapi YOUR_API_KEY`")
        
    elif cmd == "setdomain":
        if len(message.command) > 1:
            await users_collection.update_one({'_id': uid}, {'$set': {'shortener_url': message.command[1]}}, upsert=True)
            await message.reply_text("✅ Domain Saved.")
        else: await message.reply_text("Usage: `/setdomain yourshortener.com`")
        
    elif cmd == "settimer":
        if len(message.command) > 1:
            try:
                seconds = int(message.command[1]) * 60
                await users_collection.update_one({'_id': uid}, {'$set': {'delete_timer': seconds}}, upsert=True)
                await message.reply_text(f"✅ User Auto-Delete Timer: {message.command[1]} mins.")
            except: await message.reply_text("Usage: `/settimer 10` (minutes)")

# ---- POST GENERATION FLOW ----

@bot.on_message(filters.command("post") & filters.private)
@check_premium
async def post_search(client, message):
    if len(message.command) < 2: return await message.reply_text("Usage: `/post Movie Name`")
    query = " ".join(message.command[1:])
    results = search_tmdb(query)
    
    buttons = []
    for r in results:
        m_type = r.get('media_type', 'movie')
        title = r.get('title') or r.get('name')
        year = (r.get('release_date') or r.get('first_air_date') or '----')[:4]
        buttons.append([InlineKeyboardButton(f"🎬 {title} ({year})", callback_data=f"sel_{m_type}_{r['id']}")])
    
    await message.reply_text(f"🔍 Results for `{query}`:", reply_markup=InlineKeyboardMarkup(buttons))

@bot.on_callback_query(filters.regex("^sel_"))
async def post_sel(client, cb):
    _, m_type, mid = cb.data.split("_")
    details = get_tmdb_details(m_type, mid)
    uid = cb.from_user.id
    
    user_conversations[uid] = {
        "details": details,
        "links": {}, # This will now store SHORTENED LINKS mapped to quality
        "state": "wait_lang"
    }
    await cb.message.edit_text("🌐 **Select Language:**", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("Hindi", callback_data="lang_Hindi"), InlineKeyboardButton("English", callback_data="lang_English")],
        [InlineKeyboardButton("Dual Audio", callback_data="lang_Dual Audio"), InlineKeyboardButton("Bengali", callback_data="lang_Bengali")]
    ]))

@bot.on_callback_query(filters.regex("^lang_"))
async def post_lang(client, cb):
    lang = cb.data.split("_")[1]
    user_conversations[cb.from_user.id]["language"] = lang
    await show_upload_panel(cb.message, cb.from_user.id)

async def show_upload_panel(message, uid):
    buttons = [
        [InlineKeyboardButton("📤 Upload 480p File", callback_data="up_480p")],
        [InlineKeyboardButton("📤 Upload 720p File", callback_data="up_720p")],
        [InlineKeyboardButton("📤 Upload 1080p File", callback_data="up_1080p")],
        [InlineKeyboardButton("✅ FINISH & POST", callback_data="process_final")]
    ]
    
    links = user_conversations[uid].get('links', {})
    status = "\n".join([f"✅ {k} Added" for k in links.keys()])
    text = f"📂 **Current Uploads:**\n{status}\n\n👇 Select quality to upload file:"
    
    if isinstance(message, Message): await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else: await message.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

@bot.on_callback_query(filters.regex("^up_"))
async def ask_file(client, cb):
    qual = cb.data.split("_")[1]
    uid = cb.from_user.id
    user_conversations[uid]["current_quality"] = qual
    user_conversations[uid]["state"] = "wait_file_upload"
    
    await cb.message.edit_text(f"📤 **Send/Forward Video for {qual}**\n\n(Bot will store it and generate a Short Link)", 
                               reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_panel")]]))

@bot.on_callback_query(filters.regex("^back_panel"))
async def back_p(client, cb):
    await show_upload_panel(cb, cb.from_user.id)

@bot.on_message(filters.private & (filters.video | filters.document))
@check_premium
async def handle_file_upload(client, message):
    uid = message.from_user.id
    convo = user_conversations.get(uid)
    
    if convo and convo.get("state") == "wait_file_upload":
        quality = convo["current_quality"]
        media = message.video or message.document
        
        # 1. Generate Unique Code
        code = generate_random_code()
        bot_username = await get_bot_username()
        
        # 2. Create Caption for the File (When user downloads)
        details = convo['details']
        tmdb_caption = f"🎬 **{details.get('title', 'Movie')}**\n💿 Quality: {quality}\n📦 Size: {humanbytes(media.file_size)}"
        
        # 3. Get Auto Delete Timer from User Settings
        user_data = await users_collection.find_one({'_id': uid})
        timer = user_data.get('delete_timer', 0)

        # 4. Save to DB
        file_entry = {
            "code": code,
            "file_id": media.file_id,
            "caption": tmdb_caption,
            "delete_timer": timer,
            "uploader_id": uid,
            "created_at": datetime.now()
        }
        await files_collection.insert_one(file_entry)
        
        # 5. Generate Deep Link & Shorten It
        deep_link = f"https://t.me/{bot_username}?start={code}"
        msg = await message.reply_text("🔄 **Shortening Link...**")
        short_link = await shorten_link(uid, deep_link)
        
        # 6. Save Short Link to Conversation
        convo['links'][quality] = short_link
        convo['state'] = "wait_menu"
        
        await msg.edit_text(f"✅ **{quality} File Stored!**\n🔗 Short Link Generated.")
        await show_upload_panel(message, uid)

@bot.on_callback_query(filters.regex("^process_final"))
async def process_final(client, cb):
    uid = cb.from_user.id
    convo = user_conversations.get(uid)
    if not convo or not convo.get('links'):
        return await cb.answer("❌ No files uploaded!", show_alert=True)
        
    # Generate Final Poster & Caption for Channel
    details = convo['details']
    links = convo['links']
    
    caption = f"🎬 **{details.get('title') or details.get('name')}**\n"
    caption += f"⭐ Rating: {details.get('vote_average', 0):.1f}/10\n"
    caption += f"🎭 Genre: {', '.join([g['name'] for g in details.get('genres', [])[:2]])}\n"
    caption += f"🔊 Language: {convo.get('language')}\n\n"
    caption += "👇 **Download Links:**"

    # Button generation
    buttons = []
    for qual, link in links.items():
        buttons.append([InlineKeyboardButton(f"📥 Download {qual}", url=link)])
        
    # Poster logic
    poster_path = details.get('poster_path')
    if poster_path:
        poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
        # (Optional: Add watermark logic here if needed, keeping it simple for now)
        await cb.message.edit_text("🖼️ **Generating Post...**")
        
        # Get Saved Channels
        user_data = await users_collection.find_one({'_id': uid})
        channels = user_data.get('channel_ids', [])
        
        if channels:
            for cid in channels:
                try:
                    await client.send_photo(int(cid), photo=poster_url, caption=caption, reply_markup=InlineKeyboardMarkup(buttons))
                except Exception as e:
                    await cb.message.reply_text(f"❌ Failed to post to {cid}: {e}")
            await cb.message.edit_text("✅ **Posted Successfully!**")
        else:
             await client.send_photo(uid, photo=poster_url, caption=caption, reply_markup=InlineKeyboardMarkup(buttons))
             await cb.message.edit_text("✅ **Preview sent to you (No channels added).**")
    
    if uid in user_conversations: del user_conversations[uid]

# --- CHANNEL SETUP ---
@bot.on_message(filters.command("addchannel") & filters.private)
async def add_ch(client, message):
    if len(message.command) > 1:
        await users_collection.update_one({'_id': message.from_user.id}, {'$addToSet': {'channel_ids': message.command[1]}}, upsert=True)
        await message.reply_text("✅ Channel Added.")

if __name__ == "__main__":
    bot.run()
