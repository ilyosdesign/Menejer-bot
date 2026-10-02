import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from google import genai
import sqlite3
import os
import json
import traceback
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8607831304:AAHCwiAP3uUy7jCgcBSf-7zEK2-qi7RJ7xM")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AQ.Ab8RN6LAhvQgqgOzsb9koGmeyul9obvFt0mTCnqJSUtwLYf3Cg")
MANAGERS = [355045101, 8591485024]
GROUP_ID = -1004334403913

bot = telebot.TeleBot(BOT_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

scheduler = BackgroundScheduler()
scheduler.start()

TOPIC_IDS = {
    "O'lchovlar": 4,
    "Chizmalar": 6,
    "Sharq yulduz": 8,
    "Fabrika": 10,
    "Ustanovka": 12
}

pending_tasks = {}

def get_main_markup():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("📏 O'lchovlar"),
        KeyboardButton("✏️ Chizmalar"),
        KeyboardButton("🏭 Fabrika"),
        KeyboardButton("🛠 Ustanovka"),
        KeyboardButton("🌟 Sharq yulduz")
    )
    return markup

def init_db():
    conn = sqlite3.connect('tasks.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_text TEXT,
            topic_name TEXT,
            deadline TEXT,
            status TEXT DEFAULT 'pending'
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def send_reminder(topic_id, task_desc):
    try:
        bot.send_message(GROUP_ID, f"⏰ ESLATMA!\n\nUshbu vazifani bajarish vaqti keldi:\n📌 {task_desc}", message_thread_id=topic_id)
    except:
        pass

def analyze_with_gemini(content_data, is_audio=False, forced_topic=None):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = f"""
    Sen aqlli menejer yordamchisisan. Hozirgi vaqt: {now_str}.
    Xabarni o'qi (yoki eshit) va tahlil qil. Agar bu topshiriq bo'lsa uni aniqla.
    Shuningdek, topshiriq ichida muddat (masalan, 'ertaga soat 10 da', '3 kundan keyin' kabi) bo'lsa, uni hisoblab 'deadline' ga yoz. 
    Agar aniq vaqt yo'q bo'lsa JSON da null qaytar.
    """
    if forced_topic:
        prompt += f"\nBu topshiriq aniq '{forced_topic}' bo'limiga tegishli."
        
    prompt += """
    Faqat quyidagi JSON formatida qaytar:
    {
        "is_task": true,
        "topic": "O'lchovlar",
        "task_description": "Topshiriq matni",
        "deadline": null
    }
    """
    for attempt in range(3):
        try:
            if is_audio:
                response = client.models.generate_content(model='gemini-3.8-flash', contents=[content_data, prompt])
            else:
                response = client.models.generate_content(model='gemini-3.8-flash', contents=f"{prompt}\n\nXabar: {content_data}")
                
            result_text = response.text.strip().replace("```json", "").replace("```", "")
            return json.loads(result_text)
        except Exception as e:
            if attempt == 2:
                return {"error": str(e), "text": response.text if 'response' in locals() else "No response"}

def process_analysis(analysis, message):
    if not analysis:
        bot.reply_to(message, "Kechirasiz, Gemini tizimida xatolik yuz berdi.", reply_markup=get_main_markup())
        return

    if "error" in analysis:
        bot.reply_to(message, f"Tahlil xatosi: {analysis['error']}\n\nJavob: {analysis.get('text', '')}", reply_markup=get_main_markup())
        return

    if analysis.get("is_task"):
        topic = analysis.get("topic")
        task_desc = analysis.get("task_description")
        deadline_str = analysis.get("deadline")
        
        # Boshqa tilda kelsa yoki null string kelsa tozalash
        if str(deadline_str).lower() == "null" or not deadline_str:
            deadline_str = None

        topic_id = TOPIC_IDS.get(topic)
        
        if not topic_id:
            bot.reply_to(message, f"Bu bo'lim topilmadi: {topic}", reply_markup=get_main_markup())
            return

        if deadline_str:
            send_task_to_group(topic_id, topic, task_desc, deadline_str, message)
        else:
            chat_id = message.chat.id
            pending_tasks[chat_id] = {
                'topic_id': topic_id,
                'topic': topic,
                'task_desc': task_desc
            }
            markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(KeyboardButton("❌ Yo'q, shart emas"), KeyboardButton("⏳ 1 soatdan keyin"))
            markup.add(KeyboardButton("🌅 Ertaga ertalab (09:00)"), KeyboardButton("✏️ O'zim vaqtini aytaman"))
            msg = bot.reply_to(message, "Topshiriq qabul qilindi. Bunga eslatma (taymer) qo'yamizmi?", reply_markup=markup)
            bot.register_next_step_handler(message, handle_reminder_choice)
    else:
        bot.reply_to(message, "Bu gapdan topshiriqni topa olmadim yoki bo'lim noto'g'ri.", reply_markup=get_main_markup())

def handle_reminder_choice(message):
    chat_id = message.chat.id
    if chat_id not in pending_tasks:
        bot.reply_to(message, "Xatolik, boshqatdan urining.", reply_markup=get_main_markup())
        return
        
    task_data = pending_tasks[chat_id]
    text = message.text
    
    if text == "❌ Yo'q, shart emas":
        send_task_to_group(task_data['topic_id'], task_data['topic'], task_data['task_desc'], None, message)
        del pending_tasks[chat_id]
    elif text == "⏳ 1 soatdan keyin":
        dt = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        send_task_to_group(task_data['topic_id'], task_data['topic'], task_data['task_desc'], dt, message)
        del pending_tasks[chat_id]
    elif text == "🌅 Ertaga ertalab (09:00)":
        tomorrow = datetime.now() + timedelta(days=1)
        dt = tomorrow.strftime("%Y-%m-%d 09:00")
        send_task_to_group(task_data['topic_id'], task_data['topic'], task_data['task_desc'], dt, message)
        del pending_tasks[chat_id]
    elif text == "✏️ O'zim vaqtini aytaman":
        msg = bot.send_message(chat_id, "Qachonga eslatishni gapiring yoki yozing (masalan, 'indinga soat 14:00 da'):")
        bot.register_next_step_handler(message, handle_custom_reminder_time)
    else:
        send_task_to_group(task_data['topic_id'], task_data['topic'], task_data['task_desc'], None, message)
        del pending_tasks[chat_id]

def handle_custom_reminder_time(message):
    chat_id = message.chat.id
    if chat_id not in pending_tasks:
        return
    task_data = pending_tasks[chat_id]
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = f"Hozir: {now_str}. Xabardan sana va soatni ajratib YYYY-MM-DD HH:MM formatida qaytar. Tushunarsiz bo'lsa null qaytar."
    
    try:
        if message.content_type == 'voice':
            file_info = bot.get_file(message.voice.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            with open("temp_time.ogg", 'wb') as f: f.write(downloaded_file)
            audio_file = client.files.upload(file="temp_time.ogg")
            response = client.models.generate_content(model='gemini-3.8-flash', contents=[prompt, audio_file])
            os.remove("temp_time.ogg")
        else:
            response = client.models.generate_content(model='gemini-3.8-flash', contents=f"{prompt}\n\nXabar: {message.text}")
            
        dt_str = response.text.strip().replace("`", "").strip()
        if "null" in dt_str.lower() or len(dt_str) < 10:
            dt_str = None
        send_task_to_group(task_data['topic_id'], task_data['topic'], task_data['task_desc'], dt_str, message)
    except Exception as e:
        bot.send_message(chat_id, f"Vaqtni aniqlashda xato: {e}")
        send_task_to_group(task_data['topic_id'], task_data['topic'], task_data['task_desc'], None, message)
    
    del pending_tasks[chat_id]

def send_task_to_group(topic_id, topic, task_desc, deadline_str, original_message):
    javob = f"📝 Yangi vazifa:\n🏢 Bo'lim: {topic}\n📌 Vazifa: {task_desc}"
    
    if deadline_str:
        try:
            deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
            scheduler.add_job(send_reminder, 'date', run_date=deadline_dt, args=[topic_id, task_desc])
            javob += f"\n\n⏰ Eslatma belgilandi: {deadline_str}"
        except:
            pass

    try:
        bot.send_message(GROUP_ID, javob, message_thread_id=topic_id)
        if original_message and original_message.chat.id != GROUP_ID:
            bot.reply_to(original_message, f"✅ Vazifa guruhdagi '{topic}' bo'limiga yuborildi.", reply_markup=get_main_markup())
    except Exception as e:
        bot.reply_to(original_message, f"❌ Guruhga yuborishda xatolik yuz berdi: {e}\n(Bot guruhda adminligini va Guruh ID to'g'riligini tekshiring)", reply_markup=get_main_markup())

@bot.message_handler(commands=['id'])
def send_id(message):
    thread_id = message.message_thread_id
    chat_id = message.chat.id
    bot.reply_to(message, f"Guruh ID: {chat_id}\nMavzu (Topic) ID: {thread_id}")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if message.from_user.id not in MANAGERS:
        bot.reply_to(message, "Kechirasiz, men faqat Rahbar bilan ishlayman.")
        return
    bot.reply_to(message, "Assalomu alaykum, Rahbar! Pastdagi tezkor tugmalardan kerakli bo'limni tanlang yoki shunchaki vazifani yozing/gapiring:", reply_markup=get_main_markup())

@bot.message_handler(content_types=['text'])
def handle_text_messages(message):
    if message.text.lower() == "bajarildi" and message.reply_to_message:
        bot.reply_to(message, "✅ Barakalla! Bu ish bajarilganlar ro'yxatiga o'tkazildi.")
        return
    if message.from_user.id not in MANAGERS:
        return
    topics = ["O'lchovlar", "Chizmalar", "Fabrika", "Ustanovka", "Sharq yulduz"]
    for t in topics:
        if t in message.text:
            msg = bot.reply_to(message, f"Ajoyib! {t} bo'limi uchun topshiriqni matn ko'rinishida yozing yoki ovozli xabar (voice) yuboring:")
            bot.register_next_step_handler(message, process_direct_task, t)
            return
    analysis = analyze_with_gemini(message.text, is_audio=False)
    process_analysis(analysis, message)

def process_direct_task(message, forced_topic):
    if message.content_type == 'text' and any(t in message.text for t in ["O'lchovlar", "Chizmalar", "Fabrika", "Ustanovka", "Sharq yulduz"]):
        handle_text_messages(message)
        return
    if message.content_type == 'text':
        analysis = analyze_with_gemini(message.text, is_audio=False, forced_topic=forced_topic)
        process_analysis(analysis, message)
    elif message.content_type == 'voice':
        handle_direct_voice(message, forced_topic)

def handle_direct_voice(message, forced_topic):
    status_msg = bot.reply_to(message, "🎤 Ovozli xabar o'qilmoqda...")
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open("temp_voice.ogg", 'wb') as new_file: new_file.write(downloaded_file)
        audio_file = client.files.upload(file="temp_voice.ogg")
        analysis = analyze_with_gemini(audio_file, is_audio=True, forced_topic=forced_topic)
        os.remove("temp_voice.ogg")
        bot.delete_message(message.chat.id, status_msg.message_id)
        process_analysis(analysis, message)
    except Exception as e:
        bot.edit_message_text(f"Xato yuz berdi: {e}", chat_id=message.chat.id, message_id=status_msg.message_id)

@bot.message_handler(content_types=['voice'])
def handle_voice_messages(message):
    if message.from_user.id not in MANAGERS:
        return
    status_msg = bot.reply_to(message, "🎤 Ovozli xabar o'qilmoqda...")
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open("temp_voice.ogg", 'wb') as new_file: new_file.write(downloaded_file)
        audio_file = client.files.upload(file="temp_voice.ogg")
        analysis = analyze_with_gemini(audio_file, is_audio=True)
        os.remove("temp_voice.ogg")
        bot.delete_message(message.chat.id, status_msg.message_id)
        process_analysis(analysis, message)
    except Exception as e:
        bot.edit_message_text(f"Xato yuz berdi: {e}", chat_id=message.chat.id, message_id=status_msg.message_id)

from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
import os

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot ishlayapti!")

def run_dummy_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

if __name__ == '__main__':
    print("Bot ishga tushdi...")
    # Render.com port talabi uchun veb-serverni alohida oqimda ishga tushiramiz
    Thread(target=run_dummy_server, daemon=True).start()
    try:
        bot.infinity_polling()
    except Exception as e:
        print("Xatolik:", e)


