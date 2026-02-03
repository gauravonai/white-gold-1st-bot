# ============================================
# SHETKARI MITRA BOT - Main File
# White Gold Trust Farming Assistant
# ============================================

import os
import time
import json
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai
from youtube_transcript_api import YouTubeTranscriptApi
from googleapiclient.discovery import build
import requests

# ============================================
# CONFIGURATION - PUT YOUR KEYS HERE
# ============================================
TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN_HERE"        # From Step 3
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY_HERE"        # From Step 1
YOUTUBE_API_KEY = "YOUR_YOUTUBE_API_KEY_HERE"      # From Step 2
CHANNEL_ID = "UCxxxxxxxxxxxxxxxxxxxxxxxxx"          # White Gold Trust Channel ID

# ============================================
# SETUP
# ============================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

# Storage for transcripts (in memory)
video_database = {}

# ============================================
# YOUTUBE VIDEO FETCHER (Auto-fetch new videos)
# ============================================
def fetch_channel_videos():
    """Fetches all videos from White Gold Trust channel"""
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

    videos = []
    next_page = None

    while True:
        if next_page:
            response = youtube.playlistItems().list(
                playlistId=f"UU{CHANNEL_ID[2:]}",
                part='snippet,contentDetails',
                maxResults=50,
                pageToken=next_page
            ).execute()
        else:
            response = youtube.playlistItems().list(
                playlistId=f"UU{CHANNEL_ID[2:]}",
                part='snippet,contentDetails',
                maxResults=50
            ).execute()

        for item in response.get('items', []):
            video_id = item['snippet']['resourceId']['videoId']
            title = item['snippet']['title']
            upload_date = item['snippet']['publishedAt']
            duration_str = item['contentDetails'].get('duration', '0')

            # Parse duration (convert to minutes)
            duration_min = parse_duration(duration_str)

            # Filter: 2024-2025 videos, 30+ minutes
            upload_year = datetime.fromisoformat(upload_date.replace('Z', '+00:00')).year
            if upload_year >= 2024 and duration_min >= 30:
                videos.append({
                    'video_id': video_id,
                    'title': title,
                    'url': f"https://www.youtube.com/watch?v={video_id}",
                    'duration': duration_min,
                    'upload_date': upload_date
                })

        next_page = response.get('nextPageToken')
        if not next_page:
            break

    return videos


def parse_duration(duration_str):
    """Converts YouTube duration format to minutes"""
    import re
    duration_str = duration_str.replace('PT', '')
    hours = re.search(r'(\d+)H', duration_str)
    minutes = re.search(r'(\d+)M', duration_str)
    seconds = re.search(r'(\d+)S', duration_str)

    total_minutes = 0
    if hours: total_minutes += int(hours.group(1)) * 60
    if minutes: total_minutes += int(minutes.group(1))
    if seconds: total_minutes += int(seconds.group(1)) / 60

    return total_minutes


# ============================================
# TRANSCRIPT FETCHER (Auto-extract transcripts)
# ============================================
def get_transcript(video_id):
    """Gets transcript from YouTube video"""
    try:
        # Try Marathi first, then Hindi, then English
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        # Priority: Marathi > Hindi > English > Any available
        for lang in ['mr', 'hi', 'en']:
            try:
                transcript = transcript_list.find_transcript([lang])
                text_parts = transcript.fetch()
                full_text = ' '.join([part['text'] for part in text_parts])
                return full_text
            except:
                continue

        # If none found, get any available
        transcript = transcript_list.find_generated_transcript(['mr', 'hi', 'en'])
        text_parts = transcript.fetch()
        full_text = ' '.join([part['text'] for part in text_parts])
        return full_text

    except Exception as e:
        logger.error(f"Error getting transcript for {video_id}: {e}")
        return None


# ============================================
# DATABASE UPDATER (Runs every 1 hour)
# ============================================
def update_video_database():
    """Updates the video database with new videos"""
    global video_database

    logger.info("Checking for new videos...")
    videos = fetch_channel_videos()

    new_count = 0
    for video in videos:
        if video['video_id'] not in video_database:
            # New video found! Get transcript
            logger.info(f"New video found: {video['title']}")
            transcript = get_transcript(video['video_id'])

            if transcript:
                video['transcript'] = transcript
                video_database[video['video_id']] = video
                new_count += 1
                logger.info(f"Added: {video['title']}")
            else:
                logger.warning(f"No transcript for: {video['title']}")

    logger.info(f"Update complete. {new_count} new videos added. Total: {len(video_database)}")


# ============================================
# AI ANSWER GENERATOR
# ============================================
def detect_language(text):
    """Simple language detection"""
    marathi_words = ['कसे', 'करावे', 'आहे', 'आहेत', 'ला', 'ची', 'चे', 'काय', 'कधी', 'कुठे', 'किती', 'पिक', 'शेती', 'पाणी', 'खत']
    hindi_words = ['कैसे', 'करें', 'है', 'हैं', 'का', 'की', 'के', 'को', 'में', 'और', 'या', 'फसल', 'खेती', 'पानी', 'खाद']

    marathi_count = sum(1 for word in marathi_words if word in text)
    hindi_count = sum(1 for word in hindi_words if word in text)

    if marathi_count > hindi_count:
        return 'marathi'
    elif hindi_count > marathi_count:
        return 'hindi'
    elif any(ord(c) > 127 for c in text):
        return 'marathi'  # Default to Marathi if Devanagari detected
    else:
        return 'english'


def build_knowledge_base():
    """Builds knowledge base text from all transcripts"""
    knowledge = ""
    for vid_id, vid_data in video_database.items():
        knowledge += f"\n{'='*50}\n"
        knowledge += f"Video: {vid_data['title']}\n"
        knowledge += f"Link: {vid_data['url']}\n"
        knowledge += f"Duration: {vid_data['duration']:.0f} minutes\n"
        knowledge += f"{'='*50}\n"
        knowledge += f"{vid_data.get('transcript', 'No transcript available')}\n"
    return knowledge


def get_ai_answer(question, language):
    """Gets answer from Gemini AI"""

    knowledge_base = build_knowledge_base()

    # Language-specific instructions
    lang_instructions = {
        'marathi': {
            'instruction': 'मराठीत उत्तर द्या.',
            'not_available': 'माफ करा, या विषयावर व्हाईट गोल्ड ट्रस्टच्या व्हिडिओंमध्ये माहिती उपलब्ध नाही.\n\nसध्या उपलब्ध विषय:\n',
            'watch_video': 'संपूर्ण माहितीसाठी हा व्हिडिओ पहा: '
        },
        'hindi': {
            'instruction': 'हिंदी में जवाब दें।',
            'not_available': 'क्षमा करें, इस विषय पर व्हाइट गोल्ड ट्रस्ट के वीडियो में जानकारी उपलब्ध नहीं है।\n\nवर्तमान उपलब्ध विषय:\n',
            'watch_video': 'पूरी जानकारी के लिए यह वीडियो देखें: '
        },
        'english': {
            'instruction': 'Answer in English.',
            'not_available': 'Sorry, information on this topic is not available in White Gold Trust videos.\n\nCurrently available topics:\n',
            'watch_video': 'Watch this video for complete information: '
        }
    }

    lang_info = lang_instructions.get(language, lang_instructions['english'])

    prompt = f"""You are शेतकरी मित्र (Farmer's Friend), an agricultural advisor based EXCLUSIVELY on White Gold Trust (Gajanan Jadhao) YouTube video transcripts.

CRITICAL RULES:
⛔ RULE 1: NEVER use your general knowledge. ONLY answer from transcripts below.
⛔ RULE 2: If information is NOT in transcripts → Say "not available"
⛔ RULE 3: {lang_info['instruction']}
⛔ RULE 4: Give detailed bullet point answers (5-8 points)
⛔ RULE 5: Always end with relevant video link

BEFORE ANSWERING - CHECK:
"Is this EXACT information in the transcripts below?"
- YES → Answer with details in bullet points
- NO → Say "{lang_info['not_available']}"
- UNSURE → Say "not available"

KNOWLEDGE BASE (ONLY SOURCE OF TRUTH):
{knowledge_base}

FARMER'S QUESTION: {question}

RESPOND in {language} with bullet points. End with video link.
If not available, say: "{lang_info['not_available']}" and list available video topics.
"""

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return "Sorry, there was an error. Please try again. / कृपया पुन्हा प्रयत्न करा."


# ============================================
# TELEGRAM BOT HANDLERS
# ============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command"""
    message = """🌾 नमस्कार! मी शेतकरी मित्र आहे!

मी व्हाईट गोल्ड ट्रस्ट (गजानन जाधव सर) च्या YouTube व्हिडिओंवर आधारित शेती सल्लागार आहे.

तुम्ही मला प्रश्न विचारू शकता:
🇮🇳 मराठीत
🇮🇳 हिंदीत
🇬🇧 English मध्ये

📌 उदाहरण प्रश्न:
- संत्र्याची लागवड कशी करावी?
- गर्मियों में पानी का प्रबंधन कैसे करें?
- How to manage orange crops?

📝 आपचा प्रश्न लिहा 👇"""

    await update.message.reply_text(message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /help command"""
    message = """📚 कसे वापरावे:

1️⃣ तुमचा प्रश्न टाइप करा
2️⃣ Send करा
3️⃣ उत्तर मिळेल!

📌 Commands:
/start - बॉट सुरू करा
/help - मदत
/videos - सगळे उपलब्ध व्हिडिओ पहा
/status - बॉटचा status"""

    await update.message.reply_text(message)


async def list_videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all available videos"""
    if not video_database:
        await update.message.reply_text("⏳ व्हिडिओ लोड होत आहेत... कृपया थांबा.")
        return

    message = "📹 उपलब्ध व्हिडिओ:\n\n"
    for i, (vid_id, vid) in enumerate(video_database.items(), 1):
        message += f"{i}. {vid['title']}\n"
        message += f"   ⏱️ {vid['duration']:.0f} min | 🔗 {vid['url']}\n\n"

    await update.message.reply_text(message)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows bot status"""
    message = f"""📊 बॉट Status:
✅ बॉट चालू आहे
📹 व्हिडिओ: {len(video_database)}
🕐 Last Update: {datetime.now().strftime('%d/%m/%Y %H:%M')}
🤖 AI Model: Gemini 2.0 Flash"""

    await update.message.reply_text(message)


async def handle_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles farmer's questions"""
    question = update.message.text

    # Show typing indicator
    await update.message.reply_text("🔍 उत्तर शोधत आहे... / Searching...")

    # Check if database is loaded
    if not video_database:
        await update.message.reply_text("⏳ कृपया थांबा, व्हिडिओ लोड होत आहेत...")
        return

    # Detect language
    language = detect_language(question)

    # Get AI answer
    answer = get_ai_answer(question, language)

    # Send answer
    await update.message.reply_text(answer)


# ============================================
# MAIN - START THE BOT
# ============================================
async def periodic_update(context):
    """Runs every hour to check for new videos"""
    update_video_database()


def main():
    """Starts the Telegram bot"""
    # Initial database load
    logger.info("Starting Shetkari Mitra Bot...")
    update_video_database()

    # Create Telegram bot
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("videos", list_videos))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_question))

    # Start periodic update (every 3600 seconds = 1 hour)
    application.job_queue.put_repeating_job(periodic_update, interval=3600, first=10)

    # Start bot
    logger.info("Bot is running! 🌾")
    application.run_polling()


if __name__ == "__main__":
    main()
