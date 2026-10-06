import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from google import genai
import os
import json
import re
import requests
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

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
    "Kraska xona": 1,
    "Fabrika": 10,
    "Ustanovka": 12
}

NEXT_STAGE = {
    "O'lchovlar": "Chizmalar",
    "Chizmalar": "Sharq yulduz",
    "Sharq yulduz": "Fabrika",
    "Fabrika": "Ustanovka",
    "Ustanovka": "TUGADI"
}

pending_tasks = {}

def get_supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def get_main_markup():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("рџ“Џ O'lchovlar"),
        KeyboardButton("вњЏпёЏ Chizmalar"),
        KeyboardButton("рџЊџ Sharq yulduz"),
        KeyboardButton("рџЏ­ Fabrika"),
        KeyboardButton("рџ›  Ustanovka")
    )
    return markup

def send_reminder(topic_id, task_desc):
    try:
        bot.send_message(GROUP_ID, f"вЏ° ESLATMA!\n\nUshbu vazifani bajarish vaqti keldi:\nрџ“Њ {task_desc}", message_thread_id=topic_id)
    except:
        pass

def extract_username(text):
    if not text:
        return None
    match = re.search(r'@([a-zA-Z0-9_]+)', text)
    if match:
        return match.group(1).lower()
    return None

def analyze_with_gemini(content_data, is_audio=False, forced_topic=None):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = f"""
    Sen aqlli menejer yordamchisisan. Hozirgi vaqt: {now_str}.
    Xabarni o'qi (yoki eshit) va tahlil qil. Agar bu topshiriq bo'lsa uni aniqla.
    Agar topshiriqda muddat (deadline) bo'lsa uni YYYY-MM-DD HH:MM formatida yoz. Yo'q bo'lsa null.
    Agar topshiriqda kimdir @ bilan belgilangan bo'lsa (masalan @alisher), o'shani assigned_to ga yoz (faqat ism qismini, @ siz). Yo'q bo'lsa null.
    """
    if forced_topic:
        prompt += f"\nBu topshiriq aniq '{forced_topic}' bo'limiga tegishli."
        
    prompt += """
    Faqat quyidagi JSON formatida qaytar:
    {
        "is_task": true,
        "topic": "O'lchovlar",
        "task_description": "Topshiriq matni",
        "assigned_to": "alisher",
        "deadline": null
    }
    """
    for attempt in range(3):
        try:
            if is_audio:
                response = client.models.generate_content(model='gemini-3.5-flash-lite', contents=[content_data, prompt])
            else:
                response = client.models.generate_content(model='gemini-3.5-flash-lite', contents=f"{prompt}\n\nXabar: {content_data}")
                
            result_text = response.text.strip().replace("```json", "").replace("```", "")
            return json.loads(result_text)
        except Exception as e:
            if attempt == 2:
                return {"error": str(e), "text": response.text if 'response' in locals() else "No response"}

def process_analysis(analysis, message, file_id=None, file_type=None):
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
        assigned_to = analysis.get("assigned_to")
        
        if str(deadline_str).lower() == "null" or not deadline_str:
            deadline_str = None
        if str(assigned_to).lower() == "null" or not assigned_to:
            assigned_to = None
        elif assigned_to.startswith("@"):
            assigned_to = assigned_to[1:]
            
        if message.content_type in ['text', 'photo', 'video', 'document'] and not assigned_to:
            text_to_check = message.text or message.caption
            found_un = extract_username(text_to_check)
            if found_un: assigned_to = found_un

        topic_id = TOPIC_IDS.get(topic)
        
        if not topic_id:
            bot.reply_to(message, f"Bu bo'lim topilmadi: {topic}", reply_markup=get_main_markup())
            return

        if deadline_str:
            send_task_to_group(topic_id, topic, task_desc, deadline_str, assigned_to, message, file_id, file_type)
        else:
            chat_id = message.chat.id
            pending_tasks[chat_id] = {
                'topic_id': topic_id,
                'topic': topic,
                'task_desc': task_desc,
                'assigned_to': assigned_to,
                'file_id': file_id,
                'file_type': file_type
            }
            markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(KeyboardButton("вќЊ Yo'q, shart emas"), KeyboardButton("вЏі 1 soatdan keyin"))
            markup.add(KeyboardButton("рџЊ… Ertaga ertalab (09:00)"), KeyboardButton("вњЏпёЏ O'zim vaqtini aytaman"))
            bot.reply_to(message, "Topshiriq qabul qilindi. Bunga eslatma (taymer) qo'yamizmi?", reply_markup=markup)
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
    
    if text == "вќЊ Yo'q, shart emas":
        send_task_to_group(task_data['topic_id'], task_data['topic'], task_data['task_desc'], None, task_data['assigned_to'], message, task_data['file_id'], task_data['file_type'])
        del pending_tasks[chat_id]
    elif text == "вЏі 1 soatdan keyin":
        dt = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        send_task_to_group(task_data['topic_id'], task_data['topic'], task_data['task_desc'], dt, task_data['assigned_to'], message, task_data['file_id'], task_data['file_type'])
        del pending_tasks[chat_id]
    elif text == "рџЊ… Ertaga ertalab (09:00)":
        tomorrow = datetime.now() + timedelta(days=1)
        dt = tomorrow.strftime("%Y-%m-%d 09:00")
        send_task_to_group(task_data['topic_id'], task_data['topic'], task_data['task_desc'], dt, task_data['assigned_to'], message, task_data['file_id'], task_data['file_type'])
        del pending_tasks[chat_id]
    elif text == "вњЏпёЏ O'zim vaqtini aytaman":
        bot.send_message(chat_id, "Qachonga eslatishni gapiring yoki yozing (masalan, 'indinga soat 14:00 da'):")
        bot.register_next_step_handler(message, handle_custom_reminder_time)
    else:
        send_task_to_group(task_data['topic_id'], task_data['topic'], task_data['task_desc'], None, task_data['assigned_to'], message, task_data['file_id'], task_data['file_type'])
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
            response = client.models.generate_content(model='gemini-3.5-flash-lite', contents=[prompt, audio_file])
            os.remove("temp_time.ogg")
        else:
            response = client.models.generate_content(model='gemini-3.5-flash-lite', contents=f"{prompt}\n\nXabar: {message.text}")
            
        dt_str = response.text.strip().replace("`", "").strip()
        if "null" in dt_str.lower() or len(dt_str) < 10:
            dt_str = None
        send_task_to_group(task_data['topic_id'], task_data['topic'], task_data['task_desc'], dt_str, task_data['assigned_to'], message, task_data['file_id'], task_data['file_type'])
    except Exception as e:
        bot.send_message(chat_id, f"Vaqtni aniqlashda xato: {e}")
        send_task_to_group(task_data['topic_id'], task_data['topic'], task_data['task_desc'], None, task_data['assigned_to'], message, task_data['file_id'], task_data['file_type'])
    
    del pending_tasks[chat_id]

def send_task_to_group(topic_id, topic, task_desc, deadline_str, assigned_to, original_message, file_id=None, file_type=None):
    javob = f"рџ“ќ YANGA VAZIFA:\nрџЏў Bo'lim: {topic}\nрџ“Њ Vazifa: {task_desc}"
    if assigned_to:
        javob += f"\n\nрџ‘·вЂЌв™‚пёЏ Biriktirildi: @{assigned_to}"
        
    if deadline_str:
        try:
            deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
            scheduler.add_job(send_reminder, 'date', run_date=deadline_dt, args=[topic_id, task_desc])
            javob += f"\nвЏ° Eslatma: {deadline_str}"
        except:
            pass

    try:
        data = {
            "task_text": task_desc,
            "topic_name": topic,
            "deadline": deadline_str,
            "assigned_to": assigned_to,
            "status": "pending",
            "file_id": file_id,
            "file_type": file_type
        }
        res = requests.post(f"{SUPABASE_URL}/rest/v1/tasks", headers=get_supabase_headers(), json=data)
        res_data = res.json()
        
        if isinstance(res_data, list) and len(res_data) > 0:
            task_id = res_data[0]['id']
        else:
            raise Exception(f"Kutilmagan javob: {res_data}")
            
    except Exception as e:
        if original_message:
            bot.reply_to(original_message, f"Bazaga yozishda xato: {e}")
        return

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("вњ… Bajarildi", callback_data=f"done_{task_id}"))

    try:
        if file_id and file_type == 'photo':
            sent_msg = bot.send_photo(GROUP_ID, file_id, caption=javob, message_thread_id=topic_id, reply_markup=markup)
        elif file_id and file_type == 'video':
            sent_msg = bot.send_video(GROUP_ID, file_id, caption=javob, message_thread_id=topic_id, reply_markup=markup)
        elif file_id and file_type == 'document':
            sent_msg = bot.send_document(GROUP_ID, file_id, caption=javob, message_thread_id=topic_id, reply_markup=markup)
        else:
            sent_msg = bot.send_message(GROUP_ID, javob, message_thread_id=topic_id, reply_markup=markup)
        
        requests.patch(f"{SUPABASE_URL}/rest/v1/tasks?id=eq.{task_id}", 
                       headers=get_supabase_headers(), 
                       json={"message_id": sent_msg.message_id})
        
        if original_message and original_message.chat.id != GROUP_ID:
            bot.reply_to(original_message, f"вњ… Vazifa guruhdagi '{topic}' bo'limiga yuborildi.", reply_markup=get_main_markup())
    except Exception as e:
        if original_message:
            bot.reply_to(original_message, f"вќЊ Guruhga yuborishda xatolik yuz berdi: {e}", reply_markup=get_main_markup())

@bot.message_handler(func=lambda m: m.reply_to_message and m.chat.id == GROUP_ID and extract_username(m.text or m.caption))
def assign_worker_by_reply(message):
    if message.from_user.id not in MANAGERS:
        return
        
    reply_msg_id = message.reply_to_message.message_id
    new_worker = extract_username(message.text or message.caption)
    
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/tasks?message_id=eq.{reply_msg_id}&status=eq.pending", headers=get_supabase_headers())
        rows = res.json()
        if not rows: return
        row = rows[0]
        
        task_id = row['id']
        task_desc = row['task_text']
        topic = row['topic_name']
        deadline_str = row['deadline']
        file_id = row.get('file_id')
        file_type = row.get('file_type')
        
        requests.patch(f"{SUPABASE_URL}/rest/v1/tasks?id=eq.{task_id}", 
                       headers=get_supabase_headers(), 
                       json={"assigned_to": new_worker})
        
        javob = f"рџ“ќ YANGA VAZIFA:\nрџЏў Bo'lim: {topic}\nрџ“Њ Vazifa: {task_desc}\n\nрџ‘·вЂЌв™‚пёЏ Biriktirildi: @{new_worker}"
        if deadline_str:
            javob += f"\nвЏ° Eslatma: {deadline_str}"
            
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("вњ… Bajarildi", callback_data=f"done_{task_id}"))
        
        if file_id and file_type:
            bot.edit_message_caption(javob, chat_id=GROUP_ID, message_id=reply_msg_id, reply_markup=markup)
        else:
            bot.edit_message_text(javob, chat_id=GROUP_ID, message_id=reply_msg_id, reply_markup=markup)
            
        bot.delete_message(GROUP_ID, message.message_id) 
    except:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith('done_'))
def handle_done(call):
    task_id = int(call.data.split('_')[1])
    clicker_un = call.from_user.username.lower() if call.from_user.username else None
    user_name = call.from_user.first_name
    
    try:
        res = requests.get(f"{SUPABASE_URL}/rest/v1/tasks?id=eq.{task_id}", headers=get_supabase_headers())
        rows = res.json()
        if not rows:
            bot.answer_callback_query(call.id, "Xatolik: Vazifa bazadan topilmadi!")
            return
        row = rows[0]
        
        task_desc = row.get('task_text')
        current_topic = row.get('topic_name')
        status = row.get('status')
        assigned_to = row.get('assigned_to')
        file_id = row.get('file_id')
        file_type = row.get('file_type')
        
        if status == 'done':
            bot.answer_callback_query(call.id, "Bu vazifa allaqachon bajarilgan!")
            return
            
        if assigned_to:
            is_manager = call.from_user.id in MANAGERS
            if not is_manager and clicker_un != assigned_to.lower():
                bot.answer_callback_query(call.id, "вќЊ Kechirasiz, bu vazifa sizga biriktirilmagan!", show_alert=True)
                return
            
        requests.patch(f"{SUPABASE_URL}/rest/v1/tasks?id=eq.{task_id}", 
                       headers=get_supabase_headers(), 
                       json={"status": "done"})
        
        done_msg = f"вњ… BAJARILDI!\nрџЏў Bo'lim: {current_topic}\nрџ“Њ Vazifa: {task_desc}\n\nрџ‘·вЂЌв™‚пёЏ Tugatgan usta: {user_name}"
        
        if file_id and file_type:
            bot.edit_message_caption(done_msg, chat_id=call.message.chat.id, message_id=call.message.message_id)
        else:
            bot.edit_message_text(done_msg, chat_id=call.message.chat.id, message_id=call.message.message_id)
            
        bot.answer_callback_query(call.id, "Vazifa bajarildi deb belgilandi!")
        
        for manager in MANAGERS:
            try:
                bot.send_message(manager, f"рџ“€ HISOBOT: {current_topic} bo'limida ish tugatildi!\nрџ‘·вЂЌв™‚пёЏ Ustasi: {user_name}\nрџ“Њ Vazifa: {task_desc}")
            except:
                pass
                
        next_topic = NEXT_STAGE.get(current_topic)
        if next_topic:
            if next_topic == "TUGADI":
                for manager in MANAGERS:
                    try:
                        bot.send_message(manager, f"рџЋ‰ LOYIHA TO'LIQ TUGATILDI (Ustanovka yakunlandi)!\nрџ“Њ Vazifa: {task_desc}")
                    except:
                        pass
            else:
                next_topic_id = TOPIC_IDS.get(next_topic)
                if next_topic_id:
                    send_task_to_group(next_topic_id, next_topic, f"(Davomi) {task_desc}", None, None, None, file_id, file_type)
    except Exception as e:
        bot.answer_callback_query(call.id, f"Baza xatosi: {e}")

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
    bot.reply_to(message, "Assalomu alaykum, Rahbar! Pastdagi tezkor tugmalardan kerakli bo'limni tanlang yoki shunchaki vazifani yozing/gapiring/rasm yuboring:", reply_markup=get_main_markup())

@bot.message_handler(content_types=['text', 'photo', 'video', 'document'])
def handle_media_and_text(message):
    if message.from_user.id not in MANAGERS:
        return
        
    text_to_check = message.text or message.caption or ""
    
    file_id = None
    file_type = None
    
    if message.content_type == 'photo':
        file_id = message.photo[-1].file_id
        file_type = 'photo'
    elif message.content_type == 'video':
        file_id = message.video.file_id
        file_type = 'video'
    elif message.content_type == 'document':
        file_id = message.document.file_id
        file_type = 'document'
        
    topics = ["O'lchovlar", "Chizmalar", "Sharq yulduz", "Kraska xona", "Fabrika", "Ustanovka"]
    for t in topics:
        if t in text_to_check:
            bot.reply_to(message, f"Ajoyib! {t} bo'limi uchun topshiriq matnini yozing (yoki rasm/ovozli xabar yuboring):")
            bot.register_next_step_handler(message, process_direct_task, t, file_id, file_type)
            return
            
    if text_to_check.strip() == "" and file_id:
        bot.reply_to(message, "Iltimos, rasm yoki video ostiga vazifa nima ekanligini yozib yuboring (masalan: 'Chizmalarga, shuni chizish kerak').", reply_markup=get_main_markup())
        return
        
    analysis = analyze_with_gemini(text_to_check, is_audio=False)
    process_analysis(analysis, message, file_id, file_type)

def process_direct_task(message, forced_topic, prev_file_id=None, prev_file_type=None):
    text_to_check = message.text or message.caption or ""
    
    file_id = prev_file_id
    file_type = prev_file_type
    
    if message.content_type == 'photo':
        file_id = message.photo[-1].file_id
        file_type = 'photo'
    elif message.content_type == 'video':
        file_id = message.video.file_id
        file_type = 'video'
    elif message.content_type == 'document':
        file_id = message.document.file_id
        file_type = 'document'

    if message.content_type in ['text', 'photo', 'video', 'document']:
        analysis = analyze_with_gemini(text_to_check, is_audio=False, forced_topic=forced_topic)
        process_analysis(analysis, message, file_id, file_type)
    elif message.content_type == 'voice':
        handle_direct_voice(message, forced_topic, file_id, file_type)

def handle_direct_voice(message, forced_topic, file_id=None, file_type=None):
    status_msg = bot.reply_to(message, "рџЋ¤ Ovozli xabar o'qilmoqda...")
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open("temp_voice.ogg", 'wb') as new_file: new_file.write(downloaded_file)
        audio_file = client.files.upload(file="temp_voice.ogg")
        analysis = analyze_with_gemini(audio_file, is_audio=True, forced_topic=forced_topic)
        os.remove("temp_voice.ogg")
        bot.delete_message(message.chat.id, status_msg.message_id)
        process_analysis(analysis, message, file_id, file_type)
    except Exception as e:
        bot.edit_message_text(f"Xato yuz berdi: {e}", chat_id=message.chat.id, message_id=status_msg.message_id)

@bot.message_handler(content_types=['voice'])
def handle_voice_messages(message):
    if message.from_user.id not in MANAGERS:
        return
    status_msg = bot.reply_to(message, "рџЋ¤ Ovozli xabar o'qilmoqda...")
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open("temp_voice.ogg", 'wb') as new_file: new_file.write(downloaded_file)
        audio_file = client.files.upload(file="temp_voice.ogg")
        analysis = analyze_with_gemini(audio_file, is_audio=True)
        os.remove("temp_voice.ogg")
        bot.delete_message(message.chat.id, status_msg.message_id)
        process_analysis(analysis, message, None, None)
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
    Thread(target=run_dummy_server, daemon=True).start()
    try:
        bot.infinity_polling()
    except Exception as e:
        print("Xatolik:", e)

