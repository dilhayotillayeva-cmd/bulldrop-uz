import asyncio, logging, os, random, sqlite3
from pathlib import Path
from contextlib import closing
from datetime import datetime, timezone
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

TOKEN=os.getenv('BOT_TOKEN','').strip()
ADMIN_IDS={int(x.strip()) for x in os.getenv('ADMIN_IDS','').split(',') if x.strip().isdigit()}
DB_PATH=os.getenv('DB_PATH','bulldrop.sqlite3')
BOX_IMAGE=Path('quti_tanla.png')
if not TOKEN: raise RuntimeError('BOT_TOKEN environment variable is required.')
logging.basicConfig(level=logging.INFO)
bot=Bot(TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML)); dp=Dispatcher()
conn=sqlite3.connect(DB_PATH,check_same_thread=False); conn.row_factory=sqlite3.Row
conn.execute('PRAGMA journal_mode=WAL'); conn.execute('PRAGMA foreign_keys=ON')

def db(sql,params=(),fetch=False):
    with closing(conn.cursor()) as cur:
        cur.execute(sql,params)
        if fetch:return cur.fetchall()
        conn.commit(); return cur.lastrowid

def now(): return datetime.now(timezone.utc).isoformat()

def init_db():
    db('''CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,coins INTEGER NOT NULL DEFAULT 0,opened_boxes INTEGER NOT NULL DEFAULT 0,invited_by INTEGER,created_at TEXT NOT NULL)''')
    db('''CREATE TABLE IF NOT EXISTS referrals(id INTEGER PRIMARY KEY AUTOINCREMENT,inviter_id INTEGER NOT NULL,invited_id INTEGER UNIQUE NOT NULL,created_at TEXT NOT NULL)''')
    db('''CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT NOT NULL UNIQUE,title TEXT NOT NULL,username TEXT,created_at TEXT NOT NULL)''')
    db('''CREATE TABLE IF NOT EXISTS promo_codes(id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT NOT NULL UNIQUE,reward_coins INTEGER NOT NULL DEFAULT 0,max_uses INTEGER NOT NULL DEFAULT 1,uses INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)''')
    db('''CREATE TABLE IF NOT EXISTS promo_uses(id INTEGER PRIMARY KEY AUTOINCREMENT,promo_id INTEGER NOT NULL,user_id INTEGER NOT NULL,used_at TEXT NOT NULL,UNIQUE(promo_id,user_id))''')
    db('''CREATE TABLE IF NOT EXISTS rewards(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,reward_type TEXT NOT NULL,coins INTEGER NOT NULL DEFAULT 0,promo_code TEXT,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)''')
    db('''CREATE TABLE IF NOT EXISTS rounds(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,winning_box INTEGER NOT NULL,reward_id INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL,resolved_at TEXT)''')
    db('''CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)''')
    db('''CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,task_type TEXT NOT NULL DEFAULT 'link',target TEXT NOT NULL,reward_coins INTEGER NOT NULL DEFAULT 1,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)''')
    db('''CREATE TABLE IF NOT EXISTS task_completions(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER NOT NULL,user_id INTEGER NOT NULL,completed_at TEXT NOT NULL,UNIQUE(task_id,user_id))''')
    db('''CREATE TABLE IF NOT EXISTS user_languages(user_id INTEGER PRIMARY KEY,lang TEXT NOT NULL DEFAULT 'uz')''')

def lang(uid):
    r=db('SELECT lang FROM user_languages WHERE user_id=?',(uid,),True); return r[0]['lang'] if r else None

def set_lang(uid,l): db('INSERT INTO user_languages(user_id,lang) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET lang=excluded.lang',(uid,l))

def T(l,uz,ru): return ru if l=='ru' else uz

def ensure_user(obj,inviter=None):
    u=obj.from_user; rows=db('SELECT user_id FROM users WHERE user_id=?',(u.id,),True)
    if not rows:
        inv=inviter if inviter and inviter!=u.id else None
        db('INSERT INTO users(user_id,username,first_name,invited_by,created_at) VALUES(?,?,?,?,?)',(u.id,u.username or '',u.first_name or '',inv,now()))
        if inv and db('SELECT user_id FROM users WHERE user_id=?',(inv,),True):
            try: db('INSERT INTO referrals(inviter_id,invited_id,created_at) VALUES(?,?,?)',(inv,u.id,now())); db('UPDATE users SET coins=coins+1 WHERE user_id=?',(inv,))
            except sqlite3.IntegrityError: pass
    else: db('UPDATE users SET username=?,first_name=? WHERE user_id=?',(u.username or '',u.first_name or '',u.id))

def language_kb(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🇺🇿 O‘zbekcha',callback_data='lang:uz'),InlineKeyboardButton(text='🇷🇺 Русский',callback_data='lang:ru')]])

async def require_language(obj):
    if lang(obj.from_user.id): return True
    text='🌐 Tilni tanlang:\n\n🇺🇿 O‘zbekcha yoki 🇷🇺 Русский'
    if isinstance(obj,CallbackQuery):
        try: await obj.answer()
        except: pass
        try: await obj.message.edit_text(text,reply_markup=language_kb())
        except: await obj.message.answer(text,reply_markup=language_kb())
    else: await obj.answer(text,reply_markup=language_kb())
    return False

async def is_subscribed(uid):
    channels=db('SELECT * FROM channels ORDER BY id',fetch=True)
    for c in channels:
        try:
            m=await bot.get_chat_member(c['chat_id'],uid)
            if m.status in ('left','kicked'): return False
        except: return False
    return True

def sub_kb(l):
    rows=[]
    for c in db('SELECT * FROM channels ORDER BY id',fetch=True):
        if c['username']: rows.append([InlineKeyboardButton(text=f"📢 {c['title']}",url=f"https://t.me/{c['username'].lstrip('@')}")])
    rows.append([InlineKeyboardButton(text=T(l,'✅ Obunani tekshirish','✅ Проверить подписку'),callback_data='check_sub')])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def require_subscription(obj):
    if not await require_language(obj): return False
    l=lang(obj.from_user.id) or 'uz'
    if await is_subscribed(obj.from_user.id): return True
    text=T(l,'🔒 <b>Botdan to‘liq foydalanish uchun kanal(lar)ga obuna bo‘ling!</b>\n\nObuna bo‘lgach, «✅ Obunani tekshirish» tugmasini bosing.','🔒 <b>Чтобы пользоваться ботом, подпишитесь на обязательный канал(ы)!</b>\n\nПосле подписки нажмите «✅ Проверить подписку».')
    if isinstance(obj,CallbackQuery):
        try: await obj.answer(T(l,'Avval majburiy kanalga obuna bo‘ling!','Сначала подпишитесь на обязательный канал!'),show_alert=True)
        except: pass
        try: await obj.message.edit_text(text,reply_markup=sub_kb(l))
        except: await obj.message.answer(text,reply_markup=sub_kb(l))
    else: await obj.answer(text,reply_markup=sub_kb(l))
    return False

def main_menu(l):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=T(l,'🎁 QUTILAR','🎁 КОРОБКИ')),KeyboardButton(text=T(l,'👤 PROFIL','👤 ПРОФИЛЬ'))],[KeyboardButton(text=T(l,'🏆 TOP','🏆 ТОП')),KeyboardButton(text=T(l,'👥 REFERAL','👥 РЕФЕРАЛ'))],[KeyboardButton(text=T(l,'🎯 VAZIFALAR','🎯 ЗАДАНИЯ'))]],resize_keyboard=True)

def admin_menu(l):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=T(l,'📄 Promokodlar','📄 Промокоды'),callback_data='adm_promos'),InlineKeyboardButton(text=T(l,'📊 Statistika','📊 Статистика'),callback_data='adm_stats')],[InlineKeyboardButton(text=T(l,'📢 Habar yuborish','📢 Рассылка'),callback_data='adm_broadcast'),InlineKeyboardButton(text=T(l,'🎁 Quti promokod','🎁 Награды коробок'),callback_data='adm_rewards')],[InlineKeyboardButton(text=T(l,'📢 Majburiy kanallar','📢 Обязательные каналы'),callback_data='adm_channels')],[InlineKeyboardButton(text=T(l,'🎯 Vazifalar','🎯 Задания'),callback_data='adm_tasks')],[InlineKeyboardButton(text=T(l,'🚪 Chiqish','🚪 Выйти'),callback_data='adm_exit')]])

def back_admin(l): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=T(l,'⬅️ Admin panel','⬅️ Панель администратора'),callback_data='adm_home')]])

def boxes_kb(rid,l):
    rows=[]; n=1
    for _ in range(5):
        row=[]
        for _ in range(5): row.append(InlineKeyboardButton(text='🎁',callback_data=f'box:{rid}:{n}')); n+=1
        rows.append(row)
    rows.append([InlineKeyboardButton(text=T(l,'⬅️ Menyu','⬅️ Меню'),callback_data='user_menu')]); return InlineKeyboardMarkup(inline_keyboard=rows)

def tasks_kb(rows,l):
    kb=[[InlineKeyboardButton(text=f"🎯 {t['title']} • +{t['reward_coins']} 🪙",callback_data=f"task:{t['id']}")] for t in rows]
    kb.append([InlineKeyboardButton(text=T(l,'⬅️ Menyu','⬅️ Меню'),callback_data='user_menu')]); return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.message(CommandStart())
async def start(m:Message):
    arg=m.text.split(maxsplit=1)[1] if len(m.text.split())>1 else ''; inv=int(arg[4:]) if arg.startswith('ref_') and arg[4:].isdigit() else None
    ensure_user(m,inv)
    if not await require_language(m): return
    if not await require_subscription(m): return
    l=lang(m.from_user.id)
    await m.answer(T(l,'🔥 <b>BULLDROP</b> ga xush kelibsiz!\n\n🪙 Referal orqali tanga yig‘ing.\n🎁 5 ta tanga bilan quti oching.','🔥 Добро пожаловать в <b>BULLDROP</b>!\n\n🪙 Получайте монеты через рефералов.\n🎁 Открывайте коробку за 5 монет.'),reply_markup=main_menu(l))

@dp.callback_query(F.data.startswith('lang:'))
async def choose_lang(c:CallbackQuery):
    l=c.data.split(':',1)[1]
    if l not in ('uz','ru'): return
    set_lang(c.from_user.id,l); ensure_user(c); await c.answer('🇺🇿' if l=='uz' else '🇷🇺')
    if not await require_subscription(c): return
    await c.message.answer(T(l,'🔥 <b>BULLDROP</b> ga xush kelibsiz!\n\n🪙 Referal orqali tanga yig‘ing.\n🎁 5 ta tanga bilan quti oching.','🔥 Добро пожаловать в <b>BULLDROP</b>!\n\n🪙 Получайте монеты через рефералов.\n🎁 Открывайте коробку за 5 монет.'),reply_markup=main_menu(l))

async def open_boxes(m):
    if not await require_subscription(m): return
    l=lang(m.from_user.id); u=db('SELECT * FROM users WHERE user_id=?',(m.from_user.id,),True)[0]
    if u['coins']<5: await m.answer(T(l,'🪙 <b>Quti ochish uchun 5 ta tanga kerak.</b>\n\nSizda yetarli tanga yo‘q.','🪙 <b>Для открытия коробки нужно 5 монет.</b>\n\nУ вас недостаточно монет.'),reply_markup=main_menu(l)); return
    rewards=db('SELECT * FROM rewards WHERE active=1 ORDER BY id',fetch=True)
    if not rewards: await m.answer(T(l,'🎁 Hozircha qutilar uchun mukofot sozlanmagan.','🎁 Награды для коробок пока не настроены.'),reply_markup=main_menu(l)); return
    reward=random.choice(rewards); win=random.randint(1,25)
    with conn:
        cur=conn.execute('UPDATE users SET coins=coins-5 WHERE user_id=? AND coins>=5',(m.from_user.id,))
        if cur.rowcount!=1: return
        rid=conn.execute('INSERT INTO rounds(user_id,winning_box,reward_id,created_at) VALUES(?,?,?,?)',(m.from_user.id,win,reward['id'],now())).lastrowid
    cap=T(l,'🎁 <b>QUTINI TANLANG!</b>\n\n🪙 Narxi: <b>5 tanga</b>\n🔐 25 ta qutidan faqat bittasida mukofot bor.','🎁 <b>ВЫБЕРИТЕ КОРОБКУ!</b>\n\n🪙 Цена: <b>5 монет</b>\n🔐 Только в одной из 25 коробок есть награда.')
    if BOX_IMAGE.exists():
        from aiogram.types import FSInputFile
        await m.answer_photo(FSInputFile(str(BOX_IMAGE)),caption=cap,reply_markup=boxes_kb(rid,l))
    else: await m.answer(cap,reply_markup=boxes_kb(rid,l))

@dp.message(F.text.in_({'🎁 QUTILAR','🎁 КОРОБКИ'}))
async def menu_boxes(m): await open_boxes(m)

@dp.message(F.text.in_({'👤 PROFIL','👤 ПРОФИЛЬ'}))
async def profile(m):
    if not await require_subscription(m): return
    l=lang(m.from_user.id); u=db('SELECT * FROM users WHERE user_id=?',(m.from_user.id,),True)[0]; me=await bot.get_me(); link=f'https://t.me/{me.username}?start=ref_{m.from_user.id}'
    if l=='ru': text=f"👤 <b>ПРОФИЛЬ</b>\n\nИмя: <b>{u['first_name'] or 'Неизвестно'}</b>\n🪙 Монет: <b>{u['coins']}</b>\n🔗 Ваша реферальная ссылка:\n<code>{link}</code>\n🎁 Открыто коробок: <b>{u['opened_boxes']}</b>"
    else: text=f"👤 <b>PROFIL</b>\n\nNomi: <b>{u['first_name'] or 'Nomaʼlum'}</b>\n🪙 Tangalar soni: <b>{u['coins']}</b>\n🔗 Referal havolangiz:\n<code>{link}</code>\n🎁 Ochilgan qutilar: <b>{u['opened_boxes']}</b>"
    await m.answer(text,reply_markup=main_menu(l))

@dp.message(F.text.in_({'🏆 TOP','🏆 ТОП'}))
async def top(m):
    if not await require_subscription(m): return
    l=lang(m.from_user.id); rows=db('SELECT username,first_name,coins FROM users ORDER BY coins DESC,user_id ASC LIMIT 10',fetch=True); text=T(l,'🏆 <b>TOP — Tangalar bo‘yicha</b>\n\n','🏆 <b>ТОП — по монетам</b>\n\n')
    for i,r in enumerate(rows,1): text+=f"{i}. {('@'+r['username']) if r['username'] else (r['first_name'] or ('Пользователь' if l=='ru' else 'Foydalanuvchi'))} — 🪙 <b>{r['coins']}</b>\n"
    await m.answer(text,reply_markup=main_menu(l))

@dp.message(F.text.in_({'👥 REFERAL','👥 РЕФЕРАЛ'}))
async def referral(m):
    if not await require_subscription(m): return
    l=lang(m.from_user.id); me=await bot.get_me(); link=f'https://t.me/{me.username}?start=ref_{m.from_user.id}'; count=db('SELECT COUNT(*) c FROM referrals WHERE inviter_id=?',(m.from_user.id,),True)[0]['c']
    text=T(l,f'👥 <b>REFERAL</b>\n\nHar bir yangi foydalanuvchi uchun: <b>+1 🪙</b>\nTaklif qilganlaringiz: <b>{count}</b>\n\n🔗 Sizning havolangiz:\n<code>{link}</code>',f'👥 <b>РЕФЕРАЛ</b>\n\nЗа каждого нового пользователя: <b>+1 🪙</b>\nПриглашено: <b>{count}</b>\n\n🔗 Ваша ссылка:\n<code>{link}</code>')
    await m.answer(text,reply_markup=main_menu(l))

@dp.message(F.text.in_({'🎯 VAZIFALAR','🎯 ЗАДАНИЯ'}))
async def tasks(m):
    if not await require_subscription(m): return
    l=lang(m.from_user.id); rows=db('''SELECT t.*,CASE WHEN tc.id IS NULL THEN 0 ELSE 1 END done FROM tasks t LEFT JOIN task_completions tc ON tc.task_id=t.id AND tc.user_id=? WHERE t.active=1 ORDER BY t.id DESC''',(m.from_user.id,),True)
    if not rows: await m.answer(T(l,'🎯 <b>VAZIFALAR</b>\n\nHozircha vazifalar mavjud emas.','🎯 <b>ЗАДАНИЯ</b>\n\nПока заданий нет.'),reply_markup=main_menu(l)); return
    available=[]; text=T(l,'🎯 <b>VAZIFALAR</b>\n\nVazifani bajaring va tanga oling!\n','🎯 <b>ЗАДАНИЯ</b>\n\nВыполняйте задания и получайте монеты!\n')
    for t in rows:
        if t['done']: text+=f"✅ {t['title']} — {('уже получено' if l=='ru' else 'allaqachon olingan')} <b>+{t['reward_coins']} 🪙</b>\n"
        else: text+=f"🔹 {t['title']} — <b>+{t['reward_coins']} 🪙</b>\n"; available.append(t)
    if available: await m.answer(text+'\n'+T(l,'Kerakli vazifani tanlang:','Выберите задание:'),reply_markup=tasks_kb(available,l))
    else: await m.answer(text+'\n'+T(l,'🎉 Barcha vazifalarni bajargansiz!','🎉 Вы выполнили все задания!'),reply_markup=main_menu(l))

@dp.callback_query(F.data.startswith('task:'))
async def task_open(c):
    if not await require_subscription(c): return
    l=lang(c.from_user.id); tid=int(c.data.split(':')[1]); rows=db('SELECT t.*,tc.id completion_id FROM tasks t LEFT JOIN task_completions tc ON tc.task_id=t.id AND tc.user_id=? WHERE t.id=? AND t.active=1',(c.from_user.id,tid),True)
    if not rows: await c.answer(T(l,'Vazifa topilmadi.','Задание не найдено.'),show_alert=True); return
    t=rows[0]
    if t['completion_id']: await c.answer(T(l,'Bu vazifadan tanga allaqachon olingan.','За это задание монеты уже получены.'),show_alert=True); return
    if t['task_type']=='channel':
        try: member=await bot.get_chat_member(t['target'],c.from_user.id)
        except: member=None
        if not member or member.status in ('left','kicked'):
            url=f"https://t.me/{str(t['target']).lstrip('@')}" if str(t['target']).startswith('@') else 'https://t.me'
            kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=T(l,'📢 Kanalga kirish','📢 Перейти в канал'),url=url)],[InlineKeyboardButton(text=T(l,'✅ Tekshirish','✅ Проверить'),callback_data=f'task:{tid}')]])
            await c.message.answer(f"🎯 <b>{t['title']}</b>\n\n"+T(l,'Kanalga obuna bo‘ling, so‘ng «✅ Tekshirish»ni bosing.','Подпишитесь на канал, затем нажмите «✅ Проверить».')+f"\n\n🪙 {T(l,'Mukofot','Награда')}: <b>+{t['reward_coins']} {T(l,'tanga','монет')}</b>",reply_markup=kb); return
        await task_complete(c,tid); return
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=T(l,'🔗 Vazifani bajarish','🔗 Выполнить задание'),url=t['target'])],[InlineKeyboardButton(text=T(l,'✅ Bajarildi','✅ Выполнено'),callback_data=f'task_done:{tid}')],[InlineKeyboardButton(text=T(l,'⬅️ Vazifalar','⬅️ Задания'),callback_data='tasks_menu')]])
    await c.message.answer(f"🎯 <b>{t['title']}</b>\n\n"+T(l,'Vazifani bajaring va «✅ Bajarildi»ni bosing.','Выполните задание и нажмите «✅ Выполнено».')+f"\n\n🪙 {T(l,'Mukofot','Награда')}: <b>+{t['reward_coins']} {T(l,'tanga','монет')}</b>\n\n"+T(l,'⚠️ Link orqali vazifalarda bot tashqi sayt/ilovadagi amalni texnik tasdiqlay olmaydi.','⚠️ Для внешних ссылок бот не может автоматически проверить действие на сайте/в приложении.'),reply_markup=kb); await c.answer()

@dp.callback_query(F.data.startswith('task_done:'))
async def task_done(c):
    if not await require_subscription(c): return
    l=lang(c.from_user.id); tid=int(c.data.split(':')[1]); rows=db('SELECT * FROM tasks WHERE id=? AND active=1',(tid,),True)
    if not rows: await c.answer(T(l,'Vazifa topilmadi.','Задание не найдено.'),show_alert=True); return
    t=rows[0]
    if t['task_type']=='channel':
        try: member=await bot.get_chat_member(t['target'],c.from_user.id)
        except: member=None
        if not member or member.status in ('left','kicked'): await c.answer(T(l,'❌ Avval kanalga obuna bo‘ling.','❌ Сначала подпишитесь на канал.'),show_alert=True); return
    await task_complete(c,tid)

async def task_complete(c,tid):
    l=lang(c.from_user.id); trows=db('SELECT * FROM tasks WHERE id=? AND active=1',(tid,),True)
    if not trows: return
    t=trows[0]
    try:
        with conn:
            conn.execute('INSERT INTO task_completions(task_id,user_id,completed_at) VALUES(?,?,?)',(tid,c.from_user.id,now())); conn.execute('UPDATE users SET coins=coins+? WHERE user_id=?',(t['reward_coins'],c.from_user.id))
    except sqlite3.IntegrityError: await c.answer(T(l,'Bu vazifadan tanga allaqachon olingan.','За это задание монеты уже получены.'),show_alert=True); return
    await c.answer(f"🎉 +{t['reward_coins']} {T(l,'tanga!','монет!')}",show_alert=True); await c.message.answer(f"🎉 <b>{T(l,'VAZIFA BAJARILDI!','ЗАДАНИЕ ВЫПОЛНЕНО!')}</b>\n\n{t['title']}\n🪙 {T(l,'Mukofot','Награда')}: <b>+{t['reward_coins']} {T(l,'tanga','монет')}</b>",reply_markup=main_menu(l))

@dp.callback_query(F.data=='tasks_menu')
async def tasks_menu(c):
    if not await require_subscription(c): return
    l=lang(c.from_user.id); rows=db('SELECT t.*,CASE WHEN tc.id IS NULL THEN 0 ELSE 1 END done FROM tasks t LEFT JOIN task_completions tc ON tc.task_id=t.id AND tc.user_id=? WHERE t.active=1 ORDER BY t.id DESC',(c.from_user.id,),True); avail=[x for x in rows if not x['done']]
    await c.answer(); await c.message.answer(T(l,'🎯 <b>VAZIFALAR</b>\n\nVazifani tanlang:','🎯 <b>ЗАДАНИЯ</b>\n\nВыберите задание:') if avail else T(l,'🎯 <b>VAZIFALAR</b>\n\n🎉 Barcha vazifalarni bajargansiz!','🎯 <b>ЗАДАНИЯ</b>\n\n🎉 Вы выполнили все задания!'),reply_markup=tasks_kb(avail,l) if avail else main_menu(l))

@dp.callback_query(F.data=='check_sub')
async def check_sub(c):
    l=lang(c.from_user.id) or 'uz'
    if await is_subscribed(c.from_user.id):
        await c.answer(T(l,'✅ Obuna tasdiqlandi!','✅ Подписка подтверждена!'),show_alert=True); await c.message.answer(T(l,'🏠 Asosiy menyu','🏠 Главное меню'),reply_markup=main_menu(l))
    else: await c.answer(T(l,'❌ Hali barcha majburiy kanallarga obuna bo‘lmagansiz.','❌ Вы ещё не подписались на все обязательные каналы.'),show_alert=True)

@dp.callback_query(F.data=='user_menu')
async def user_menu(c):
    if not await require_subscription(c): return
    l=lang(c.from_user.id); await c.answer(); await c.message.answer(T(l,'🏠 Asosiy menyu','🏠 Главное меню'),reply_markup=main_menu(l))

@dp.callback_query(F.data.startswith('box:'))
async def box(c):
    if not await require_subscription(c): return
    l=lang(c.from_user.id); _,rid,b=c.data.split(':'); rid=int(rid); b=int(b); rows=db("SELECT * FROM rounds WHERE id=? AND user_id=? AND status='open'",(rid,c.from_user.id),True)
    if not rows: await c.answer(T(l,'Bu quti tanlovi tugagan.','Выбор этой коробки уже завершён.'),show_alert=True); return
    r=rows[0]; reward=db('SELECT * FROM rewards WHERE id=?',(r['reward_id'],),True)
    if not reward: await c.answer(T(l,'Mukofot topilmadi.','Награда не найдена.'),show_alert=True); return
    with conn: conn.execute("UPDATE rounds SET status='resolved',resolved_at=? WHERE id=? AND status='open'",(now(),rid)); conn.execute('UPDATE users SET opened_boxes=opened_boxes+1 WHERE user_id=?',(c.from_user.id,))
    if b==r['winning_box']:
        if reward[0]['reward_type']=='coins':
            amount=reward[0]['coins']; db('UPDATE users SET coins=coins+? WHERE user_id=?',(amount,c.from_user.id)); text=T(l,f'🎉 <b>TABRIKLAYMIZ!</b>\n\nSiz yutuqli qutini tanladingiz!\n\n🪙 Mukofot: <b>+{amount} tanga</b>',f'🎉 <b>ПОЗДРАВЛЯЕМ!</b>\n\nВы выбрали выигрышную коробку!\n\n🪙 Награда: <b>+{amount} монет</b>')
        else:
            code=reward[0]['promo_code'] or reward[0]['name']; text=T(l,f'🎉 <b>TABRIKLAYMIZ!</b>\n\n🎟️ Sizning promokodingiz:\n<code>{code}</code>',f'🎉 <b>ПОЗДРАВЛЯЕМ!</b>\n\n🎟️ Ваш промокод:\n<code>{code}</code>')
        await c.answer(T(l,'🎉 YUTUQ!','🎉 ВЫИГРЫШ!'),show_alert=True)
    else:
        text=T(l,'😔 <b>Afsus, omad kelmadi!</b>\n\nKelasi safar aniq o‘xshaydi 😉','😔 <b>К сожалению, не повезло!</b>\n\nВ следующий раз обязательно повезёт 😉'); await c.answer(T(l,'😔 Bu safar omad kelmadi.','😔 В этот раз не повезло.'),show_alert=True)
    await c.message.edit_text(text)

# ---------- ADMIN ----------
pending={}
def is_admin(c): return c.from_user.id in ADMIN_IDS

@dp.message(Command('admin'))
async def admin_cmd(m):
    ensure_user(m)
    if not is_admin(m): return
    if not await require_language(m): return
    l=lang(m.from_user.id); await m.answer('🛠 <b>BULLDROP ADMIN PANEL</b>' if l=='uz' else '🛠 <b>ПАНЕЛЬ АДМИНИСТРАТОРА BULLDROP</b>',reply_markup=admin_menu(l))

@dp.callback_query(F.data=='adm_home')
async def adm_home(c):
    if not is_admin(c): return
    l=lang(c.from_user.id) or 'uz'; await c.answer(); await c.message.edit_text('🛠 <b>BULLDROP ADMIN PANEL</b>' if l=='uz' else '🛠 <b>ПАНЕЛЬ АДМИНИСТРАТОРА BULLDROP</b>',reply_markup=admin_menu(l))

@dp.callback_query(F.data=='adm_exit')
async def adm_exit(c):
    if is_admin(c): await c.answer(); await c.message.delete()

@dp.callback_query(F.data=='adm_stats')
async def adm_stats(c):
    if not is_admin(c): return
    l=lang(c.from_user.id) or 'uz'; users=db('SELECT COUNT(*) c FROM users',fetch=True)[0]['c']; boxes=db('SELECT COALESCE(SUM(opened_boxes),0) c FROM users',fetch=True)[0]['c']; coins=db('SELECT COALESCE(SUM(coins),0) c FROM users',fetch=True)[0]['c']; refs=db('SELECT COUNT(*) c FROM referrals',fetch=True)[0]['c']; promos=db('SELECT COALESCE(SUM(uses),0) c FROM promo_codes',fetch=True)[0]['c']
    text=T(l,f'📊 <b>STATISTIKA</b>\n\n👥 Foydalanuvchilar: <b>{users}</b>\n🎁 Ochilgan qutilar: <b>{boxes}</b>\n🪙 Hozirgi jami balans: <b>{coins}</b>\n👥 Referallar: <b>{refs}</b>\n🎟️ Promokod ishlatilishi: <b>{promos}</b>',f'📊 <b>СТАТИСТИКА</b>\n\n👥 Пользователи: <b>{users}</b>\n🎁 Открыто коробок: <b>{boxes}</b>\n🪙 Общий текущий баланс: <b>{coins}</b>\n👥 Рефералы: <b>{refs}</b>\n🎟️ Использовано промокодов: <b>{promos}</b>')
    await c.message.edit_text(text,reply_markup=back_admin(l))

@dp.callback_query(F.data=='adm_channels')
async def adm_channels(c):
    if not is_admin(c): return
    l=lang(c.from_user.id) or 'uz'; rows=db('SELECT * FROM channels ORDER BY id',fetch=True); text=('📢 <b>MAJBURIY KANALLAR</b>\n\n' if l=='uz' else '📢 <b>ОБЯЗАТЕЛЬНЫЕ КАНАЛЫ</b>\n\n')
    for x in rows:text+=f"#{x['id']} — {x['title']} — <code>{x['chat_id']}</code>\n"
    if not rows:text+=T(l,'Hozircha kanal qo‘shilmagan.','Каналов пока нет.')+'\n'
    kb=[[InlineKeyboardButton(text=T(l,'➕ Kanal qo‘shish','➕ Добавить канал'),callback_data='adm_ch_add')],[InlineKeyboardButton(text=T(l,'➖ Kanal olib tashlash','➖ Удалить канал'),callback_data='adm_ch_remove')],[InlineKeyboardButton(text=T(l,'⬅️ Admin panel','⬅️ Панель администратора'),callback_data='adm_home')]]; await c.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data=='adm_ch_add')
async def adm_ch_add(c):
    if not is_admin(c): return
    pending[c.from_user.id]={'action':'add_channel'}; l=lang(c.from_user.id) or 'uz'; await c.message.answer(T(l,'➕ Kanal username yoki chat ID yuboring. Bot kanalga admin bo‘lishi kerak.','➕ Отправьте username или chat ID канала. Бот должен быть администратором канала.')); await c.answer()

@dp.callback_query(F.data=='adm_ch_remove')
async def adm_ch_remove(c):
    if not is_admin(c): return
    l=lang(c.from_user.id) or 'uz'; rows=db('SELECT * FROM channels ORDER BY id',fetch=True)
    if not rows: await c.answer(T(l,'Kanal yo‘q.','Каналов нет.'),show_alert=True); return
    kb=[[InlineKeyboardButton(text=f"❌ {x['title']}",callback_data=f"adm_ch_del:{x['id']}")] for x in rows]; kb.append([InlineKeyboardButton(text=T(l,'⬅️ Orqaga','⬅️ Назад'),callback_data='adm_channels')]); await c.message.edit_text(T(l,'O‘chiriladigan kanalni tanlang:','Выберите канал для удаления:'),reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith('adm_ch_del:'))
async def adm_ch_del(c):
    if not is_admin(c): return
    db('DELETE FROM channels WHERE id=?',(int(c.data.split(':')[1]),)); await c.answer('✅'); await adm_channels(c)

@dp.callback_query(F.data=='adm_tasks')
async def adm_tasks(c):
    if not is_admin(c): return
    l=lang(c.from_user.id) or 'uz'; rows=db('SELECT * FROM tasks ORDER BY id DESC',fetch=True); text='🎯 <b>'+('VAZIFALAR' if l=='uz' else 'ЗАДАНИЯ')+'</b>\n\n'
    for t in rows:
        n=db('SELECT COUNT(*) c FROM task_completions WHERE task_id=?',(t['id'],),True)[0]['c']; text+=f"#{t['id']} {'🟢' if t['active'] else '🔴'} — {t['title']} — +{t['reward_coins']} 🪙 — 👥 {n}\n"
    if not rows:text+=T(l,'Hozircha vazifa yo‘q.','Пока заданий нет.')+'\n'
    kb=[[InlineKeyboardButton(text=T(l,'➕ Vazifa qo‘shish','➕ Добавить задание'),callback_data='adm_task_add')],[InlineKeyboardButton(text=T(l,'➖ Vazifani olib tashlash','➖ Удалить задание'),callback_data='adm_task_del')],[InlineKeyboardButton(text=T(l,'📊 Bajarilganlar statistikasi','📊 Статистика выполнений'),callback_data='adm_task_stats')],[InlineKeyboardButton(text=T(l,'⬅️ Admin panel','⬅️ Панель администратора'),callback_data='adm_home')]]; await c.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)); await c.answer()

@dp.callback_query(F.data=='adm_task_add')
async def adm_task_add(c):
    if not is_admin(c): return
    pending[c.from_user.id]={'action':'task_add'}; l=lang(c.from_user.id) or 'uz'
    txt=T(l,'➕ <b>Vazifa qo‘shish</b>\n\nFormat: <code>Nomi | turi | link/chat_id | tanga</code>\n\nchannel = kanalga obuna\nlink = video/like/link\n\nMisol: <code>YouTube like | link | https://youtube.com/... | 5</code>\n\nBekor qilish: /cancel','➕ <b>Добавить задание</b>\n\nФормат: <code>Название | тип | ссылка/chat_id | монеты</code>\n\nchannel = подписка на канал\nlink = видео/лайк/ссылка\n\nПример: <code>Лайк YouTube | link | https://youtube.com/... | 5</code>\n\nОтмена: /cancel'); await c.message.answer(txt); await c.answer()

@dp.callback_query(F.data=='adm_task_del')
async def adm_task_del(c):
    if not is_admin(c): return
    l=lang(c.from_user.id) or 'uz'; rows=db('SELECT * FROM tasks WHERE active=1 ORDER BY id DESC',fetch=True)
    if not rows: await c.answer(T(l,'O‘chirish uchun vazifa yo‘q.','Нет заданий для удаления.'),show_alert=True); return
    kb=[[InlineKeyboardButton(text=f"❌ #{t['id']} {t['title']}",callback_data=f"adm_task_delid:{t['id']}")] for t in rows]; kb.append([InlineKeyboardButton(text=T(l,'⬅️ Orqaga','⬅️ Назад'),callback_data='adm_tasks')]); await c.message.edit_text(T(l,'O‘chiriladigan vazifani tanlang:','Выберите задание для удаления:'),reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith('adm_task_delid:'))
async def adm_task_delid(c):
    if not is_admin(c): return
    db('UPDATE tasks SET active=0 WHERE id=?',(int(c.data.split(':')[1]),)); await c.answer('✅'); await adm_tasks(c)

@dp.callback_query(F.data=='adm_task_stats')
async def adm_task_stats(c):
    if not is_admin(c): return
    l=lang(c.from_user.id) or 'uz'; rows=db('SELECT t.id,t.title,COUNT(tc.id) completed FROM tasks t LEFT JOIN task_completions tc ON tc.task_id=t.id GROUP BY t.id ORDER BY t.id DESC',fetch=True); total=db('SELECT COUNT(DISTINCT user_id) c FROM task_completions',fetch=True)[0]['c']; done=db('SELECT COUNT(*) c FROM task_completions',fetch=True)[0]['c']; text=T(l,f'📊 <b>VAZIFALAR STATISTIKASI</b>\n\n👥 Vazifa bajargan foydalanuvchilar: <b>{total}</b>\n✅ Jami bajarilgan vazifalar: <b>{done}</b>\n\n',f'📊 <b>СТАТИСТИКА ЗАДАНИЙ</b>\n\n👥 Пользователей: <b>{total}</b>\n✅ Всего выполнений: <b>{done}</b>\n\n')
    for r in rows:text+=f"#{r['id']} — {r['title']} — 👥 <b>{r['completed']}</b>\n"
    await c.message.edit_text(text,reply_markup=back_admin(l))

# Existing reward/promo admin controls retained
@dp.callback_query(F.data=='adm_rewards')
async def adm_rewards(c):
    if not is_admin(c): return
    l=lang(c.from_user.id) or 'uz'; rows=db('SELECT * FROM rewards WHERE active=1 ORDER BY id',fetch=True); text=T(l,'🎁 <b>QUTI MUKOFOTLARI</b>\n\n','🎁 <b>НАГРАДЫ КОРОБОК</b>\n\n')
    for r in rows:text+=f"#{r['id']} — {r['name']} — {r['coins']} 🪙" if r['reward_type']=='coins' else f"#{r['id']} — {r['name']} — 🎟️ {r['promo_code']}"; text+='\n'
    kb=[[InlineKeyboardButton(text=T(l,'➕ Tanga mukofoti','➕ Награда монетами'),callback_data='adm_reward_coin')],[InlineKeyboardButton(text=T(l,'➕ Promokod mukofoti','➕ Промокод-награда'),callback_data='adm_reward_promo')],[InlineKeyboardButton(text=T(l,'➖ Mukofotni o‘chirish','➖ Удалить награду'),callback_data='adm_reward_del')],[InlineKeyboardButton(text=T(l,'⬅️ Admin panel','⬅️ Панель администратора'),callback_data='adm_home')]]; await c.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data=='adm_reward_coin')
async def adm_reward_coin(c):
    if not is_admin(c): return
    pending[c.from_user.id]={'action':'reward_coin'}; await c.message.answer('Nomi | tanga_soni'); await c.answer()
@dp.callback_query(F.data=='adm_reward_promo')
async def adm_reward_promo(c):
    if not is_admin(c): return
    pending[c.from_user.id]={'action':'reward_promo'}; await c.message.answer('Nomi | PROMOKOD'); await c.answer()
@dp.callback_query(F.data=='adm_reward_del')
async def adm_reward_del(c):
    if not is_admin(c): return
    l=lang(c.from_user.id) or 'uz'; rows=db('SELECT * FROM rewards WHERE active=1 ORDER BY id',fetch=True); kb=[[InlineKeyboardButton(text=f"❌ {r['name']}",callback_data=f"adm_reward_delid:{r['id']}")] for r in rows]; kb.append([InlineKeyboardButton(text='⬅️',callback_data='adm_rewards')]); await c.message.edit_text(T(l,'O‘chiriladigan mukofotni tanlang:','Выберите награду для удаления:'),reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
@dp.callback_query(F.data.startswith('adm_reward_delid:'))
async def adm_reward_delid(c):
    if not is_admin(c): return
    db('UPDATE rewards SET active=0 WHERE id=?',(int(c.data.split(':')[1]),)); await adm_rewards(c)

@dp.callback_query(F.data=='adm_promos')
async def adm_promos(c):
    if not is_admin(c): return
    l=lang(c.from_user.id) or 'uz'; rows=db('SELECT * FROM promo_codes ORDER BY id DESC LIMIT 30',fetch=True); text=T(l,'📄 <b>PROMOKODLAR</b>\n\n','📄 <b>ПРОМОКОДЫ</b>\n\n')
    for p in rows:text+=f"<code>{p['code']}</code> — {p['uses']}/{p['max_uses']} — {'🟢' if p['active'] else '🔴'}\n"
    kb=[[InlineKeyboardButton(text=T(l,'➕ Promokod qo‘shish','➕ Добавить промокод'),callback_data='adm_promo_add')],[InlineKeyboardButton(text=T(l,'➖ Promokod o‘chirish','➖ Удалить промокод'),callback_data='adm_promo_del')],[InlineKeyboardButton(text=T(l,'⬅️ Admin panel','⬅️ Панель администратора'),callback_data='adm_home')]]; await c.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
@dp.callback_query(F.data=='adm_promo_add')
async def adm_promo_add(c):
    if not is_admin(c): return
    pending[c.from_user.id]={'action':'promo_add'}; await c.message.answer('KOD | tanga_mukofoti | foydalanish_limiti'); await c.answer()
@dp.callback_query(F.data=='adm_promo_del')
async def adm_promo_del(c):
    if not is_admin(c): return
    l=lang(c.from_user.id) or 'uz'; rows=db('SELECT * FROM promo_codes WHERE active=1 ORDER BY id DESC',fetch=True); kb=[[InlineKeyboardButton(text=f"❌ {p['code']}",callback_data=f"adm_promo_delid:{p['id']}")] for p in rows]; kb.append([InlineKeyboardButton(text='⬅️',callback_data='adm_promos')]); await c.message.edit_text(T(l,'O‘chiriladigan promokodni tanlang:','Выберите промокод для удаления:'),reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
@dp.callback_query(F.data.startswith('adm_promo_delid:'))
async def adm_promo_delid(c):
    if not is_admin(c): return
    db('UPDATE promo_codes SET active=0 WHERE id=?',(int(c.data.split(':')[1]),)); await adm_promos(c)
@dp.callback_query(F.data=='adm_broadcast')
async def adm_broadcast(c):
    if not is_admin(c): return
    pending[c.from_user.id]={'action':'broadcast'}; await c.message.answer('📢 Xabarni yuboring. /cancel'); await c.answer()

@dp.message(Command('cancel'))
async def cancel(m):
    if m.from_user.id in pending: pending.pop(m.from_user.id,None); await m.answer('❌ Bekor qilindi.')

@dp.message()
async def pending_handler(m):
    if m.from_user.id not in ADMIN_IDS:
        if not await require_subscription(m): return
        return
    if not await require_language(m): return
    action=pending.get(m.from_user.id)
    if not action:return
    typ=action['action']; l=lang(m.from_user.id) or 'uz'
    try:
        if typ=='add_channel':
            chat=await bot.get_chat(m.text.strip()); await bot.get_chat_member(chat.id,m.from_user.id); db('INSERT OR IGNORE INTO channels(chat_id,title,username,created_at) VALUES(?,?,?,?)',(str(chat.id),chat.title or m.text.strip(),chat.username,now())); await m.answer(T(l,'✅ Kanal qo‘shildi.','✅ Канал добавлен.'),reply_markup=admin_menu(l))
        elif typ=='task_add':
            title,tt,target,reward=[x.strip() for x in m.text.split('|')]; reward=int(reward); tt=tt.lower();
            if tt not in ('channel','link') or reward<=0: raise ValueError('channel/link va tanga noto‘g‘ri')
            if tt=='channel': await bot.get_chat(target)
            elif not target.startswith(('http://','https://')): raise ValueError('Link http:// yoki https:// bilan boshlansin')
            db('INSERT INTO tasks(title,task_type,target,reward_coins,created_at) VALUES(?,?,?,?,?)',(title,tt,target,reward,now())); await m.answer(T(l,'✅ Vazifa qo‘shildi.','✅ Задание добавлено.'),reply_markup=admin_menu(l))
        elif typ=='reward_coin':
            name,amount=[x.strip() for x in m.text.split('|',1)]; db('INSERT INTO rewards(name,reward_type,coins,created_at) VALUES(?,?,?,?)',(name,'coins',int(amount),now()))
            # fix reward_type if malformed insert above
            db('UPDATE rewards SET reward_type=? WHERE name=? ORDER BY id DESC LIMIT 1',('coins',name)); await m.answer(T(l,'✅ Mukofot qo‘shildi.','✅ Награда добавлена.'),reply_markup=admin_menu(l))
        elif typ=='reward_promo':
            name,code=[x.strip() for x in m.text.split('|',1)]; db('INSERT INTO rewards(name,reward_type,promo_code,created_at) VALUES(?,?,?,?)',(name,'promo',code,now())); await m.answer(T(l,'✅ Promokod mukofoti qo‘shildi.','✅ Промокод-награда добавлена.'),reply_markup=admin_menu(l))
        elif typ=='promo_add':
            code,reward,limit=[x.strip() for x in m.text.split('|')]; db('INSERT INTO promo_codes(code,reward_coins,max_uses,created_at) VALUES(?,?,?,?)',(code.upper(),int(reward),int(limit),now())); await m.answer(T(l,'✅ Promokod qo‘shildi.','✅ Промокод добавлен.'),reply_markup=admin_menu(l))
        elif typ=='broadcast':
            sent=0
            for u in db('SELECT user_id FROM users',fetch=True):
                try: await bot.copy_message(u['user_id'],m.chat.id,m.message_id); sent+=1
                except: pass
            await m.answer(f'📢 {sent} '+T(l,'ta foydalanuvchiga yuborildi.','пользователям отправлено.'),reply_markup=admin_menu(l))
    except Exception as e: await m.answer(f'❌ {str(e)[:300]}')
    finally: pending.pop(m.from_user.id,None)

async def health(request): return web.Response(text='BULLDROP BOT OK')
async def main():
    init_db(); app=web.Application(); app.router.add_get('/health',health); app.router.add_get('/',health); runner=web.AppRunner(app); await runner.setup(); await web.TCPSite(runner,'0.0.0.0',int(os.getenv('PORT','10000'))).start(); logging.info('BULLDROP bot started'); await dp.start_polling(bot)
if __name__=='__main__': asyncio.run(main())
