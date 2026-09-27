
import asyncio
import logging
import os
import random
from pathlib import Path
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DB_PATH = os.getenv("DB_PATH", "bulldrop.sqlite3")
BOX_IMAGE = Path("quti_tanla.png")

logging.basicConfig(level=logging.INFO)

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required.")

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA foreign_keys=ON")

def db(sql, params=(), fetch=False, many=False):
    with closing(conn.cursor()) as cur:
        if many:
            cur.executemany(sql, params)
        else:
            cur.execute(sql, params)
        if fetch:
            return cur.fetchall()
        conn.commit()
        return cur.lastrowid

def init_db():
    db("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        coins INTEGER NOT NULL DEFAULT 0,
        opened_boxes INTEGER NOT NULL DEFAULT 0,
        invited_by INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY(invited_by) REFERENCES users(user_id)
    )""")
    db("""CREATE TABLE IF NOT EXISTS referrals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inviter_id INTEGER NOT NULL,
        invited_id INTEGER UNIQUE NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(inviter_id) REFERENCES users(user_id),
        FOREIGN KEY(invited_id) REFERENCES users(user_id)
    )""")
    db("""CREATE TABLE IF NOT EXISTS channels(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        username TEXT,
        created_at TEXT NOT NULL
    )""")
    db("""CREATE TABLE IF NOT EXISTS promo_codes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        reward_coins INTEGER NOT NULL DEFAULT 0,
        max_uses INTEGER NOT NULL DEFAULT 1,
        uses INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""")
    db("""CREATE TABLE IF NOT EXISTS promo_uses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        promo_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        used_at TEXT NOT NULL,
        UNIQUE(promo_id, user_id),
        FOREIGN KEY(promo_id) REFERENCES promo_codes(id),
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )""")
    db("""CREATE TABLE IF NOT EXISTS rewards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        reward_type TEXT NOT NULL,
        coins INTEGER NOT NULL DEFAULT 0,
        promo_code TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""")
    db("""CREATE TABLE IF NOT EXISTS rounds(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        winning_box INTEGER NOT NULL,
        reward_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(user_id),
        FOREIGN KEY(reward_id) REFERENCES rewards(id)
    )""")
    db("""CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")

def now():
    return datetime.now(timezone.utc).isoformat()

def ensure_user(user: Message | CallbackQuery, inviter_id=None):
    u = user.from_user
    exists = db("SELECT user_id FROM users WHERE user_id=?", (u.id,), True)
    if not exists:
        invited_by = inviter_id if inviter_id and inviter_id != u.id else None
        db("""INSERT INTO users(user_id,username,first_name,invited_by,created_at)
              VALUES(?,?,?,?,?)""",
           (u.id, u.username or "", u.first_name or "", invited_by, now()))
        if invited_by:
            # Only one legitimate referral reward per new user.
            inviter = db("SELECT user_id FROM users WHERE user_id=?", (invited_by,), True)
            if inviter:
                try:
                    db("INSERT INTO referrals(inviter_id,invited_id,created_at) VALUES(?,?,?)",
                       (invited_by, u.id, now()))
                    db("UPDATE users SET coins=coins+1 WHERE user_id=?", (invited_by,))
                except sqlite3.IntegrityError:
                    pass
    else:
        db("UPDATE users SET username=?, first_name=? WHERE user_id=?",
           (u.username or "", u.first_name or "", u.id))

async def is_subscribed(user_id: int) -> bool:
    channels = db("SELECT * FROM channels ORDER BY id", fetch=True)
    if not channels:
        return True
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch["chat_id"], user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception:
            # If Telegram cannot verify a channel, fail closed for mandatory subscription.
            return False
    return True

def subscribe_keyboard():
    channels = db("SELECT * FROM channels ORDER BY id", fetch=True)
    rows = []
    for ch in channels:
        if ch["username"]:
            url = f"https://t.me/{ch['username'].lstrip('@')}"
            rows.append([InlineKeyboardButton(text=f"📢 {ch['title']}", url=url)])
    rows.append([InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def require_subscription(obj) -> bool:
    user_id = obj.from_user.id
    if await is_subscribed(user_id):
        return True
    text = "🔒 <b>Botdan to‘liq foydalanish uchun kanal(lar)ga obuna bo‘ling!</b>\n\nObuna bo‘lgach, «✅ Obunani tekshirish» tugmasini bosing."
    if isinstance(obj, CallbackQuery):
        try:
            await obj.answer("Avval majburiy kanalga obuna bo‘ling!", show_alert=True)
        except Exception:
            pass
        try:
            await obj.message.edit_text(text, reply_markup=subscribe_keyboard())
        except Exception:
            await obj.message.answer(text, reply_markup=subscribe_keyboard())
    else:
        await obj.answer(text, reply_markup=subscribe_keyboard())
    return False

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎁 QUTILAR"), KeyboardButton(text="👤 PROFIL")],
            [KeyboardButton(text="🏆 TOP"), KeyboardButton(text="👥 REFERAL")],
        ],
        resize_keyboard=True
    )

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Promokodlar", callback_data="adm_promos"),
         InlineKeyboardButton(text="📊 Statistika", callback_data="adm_stats")],
        [InlineKeyboardButton(text="📢 Habar yuborish", callback_data="adm_broadcast"),
         InlineKeyboardButton(text="🎁 Quti promokod", callback_data="adm_rewards")],
        [InlineKeyboardButton(text="📢 Majburiy kanallar", callback_data="adm_channels")],
        [InlineKeyboardButton(text="🚪 Chiqish", callback_data="adm_exit")]
    ])

def back_admin():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm_home")]
    ])

def boxes_keyboard(round_id):
    rows=[]
    n=1
    for _ in range(5):
        row=[]
        for _ in range(5):
            row.append(InlineKeyboardButton(text="🎁", callback_data=f"box:{round_id}:{n}"))
            n += 1
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Menyu", callback_data="user_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def open_boxes(message: Message):
    if not await require_subscription(message):
        return
    u = db("SELECT * FROM users WHERE user_id=?", (message.from_user.id,), True)
    if not u:
        ensure_user(message)
        u = db("SELECT * FROM users WHERE user_id=?", (message.from_user.id,), True)
    if u[0]["coins"] < 5:
        await message.answer("🪙 <b>Quti ochish uchun 5 ta tanga kerak.</b>\n\nSizda yetarli tanga yo‘q.", reply_markup=main_menu())
        return
    rewards = db("SELECT * FROM rewards WHERE active=1 ORDER BY id", fetch=True)
    if not rewards:
        await message.answer("🎁 Hozircha qutilar uchun mukofot sozlanmagan.")
        return
    reward = random.choice(rewards)
    winning = random.randint(1,25)
    # Atomic-ish transaction: deduct exactly 5 coins before creating the round.
    with conn:
        cur = conn.execute("UPDATE users SET coins=coins-5 WHERE user_id=? AND coins>=5", (message.from_user.id,))
        if cur.rowcount != 1:
            await message.answer("🪙 Tanga yetarli emas.")
            return
        round_id = conn.execute(
            "INSERT INTO rounds(user_id,winning_box,reward_id,created_at) VALUES(?,?,?,?)",
            (message.from_user.id, winning, reward["id"], now())
        ).lastrowid
    caption = (
        "🎁 <b>QUTINI TANLANG!</b>\n\n"
        "🪙 Narxi: <b>5 tanga</b>\n"
        "🔐 25 ta qutidan faqat bittasida mukofot bor."
    )
    # Qutilar menyusi ustida promo rasmi ko‘rsatiladi.
    if BOX_IMAGE.exists():
        from aiogram.types import FSInputFile
        await message.answer_photo(
            photo=FSInputFile(str(BOX_IMAGE)),
            caption=caption,
            reply_markup=boxes_keyboard(round_id)
        )
    else:
        await message.answer(caption, reply_markup=boxes_keyboard(round_id))

@dp.message(CommandStart())
async def start(message: Message):
    arg = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    inviter = None
    if arg.startswith("ref_") and arg[4:].isdigit():
        inviter = int(arg[4:])
    ensure_user(message, inviter)
    if not await require_subscription(message):
        return
    await message.answer(
        "🔥 <b>BULLDROP</b> ga xush kelibsiz!\n\n"
        "🪙 Referal orqali tanga yig‘ing.\n"
        "🎁 5 ta tanga bilan quti oching.",
        reply_markup=main_menu()
    )

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    ensure_user(message)
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("🛠 <b>BULLDROP ADMIN PANEL</b>", reply_markup=admin_menu())

@dp.message(F.text == "🎁 QUTILAR")
async def menu_boxes(message: Message):
    await open_boxes(message)

@dp.message(F.text == "👤 PROFIL")
async def profile(message: Message):
    if not await require_subscription(message): return
    ensure_user(message)
    u = db("SELECT * FROM users WHERE user_id=?", (message.from_user.id,), True)[0]
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
    await message.answer(
        f"👤 <b>PROFIL</b>\n\n"
        f"Nomi: <b>{u['first_name'] or 'Nomaʼlum'}</b>\n"
        f"🪙 Tangalar soni: <b>{u['coins']}</b>\n"
        f"🔗 Referal havolangiz:\n<code>{link}</code>\n"
        f"🎁 Ochilgan qutilar: <b>{u['opened_boxes']}</b>",
        reply_markup=main_menu()
    )

@dp.message(F.text == "🏆 TOP")
async def top(message: Message):
    if not await require_subscription(message): return
    rows = db("SELECT username,first_name,coins FROM users ORDER BY coins DESC, user_id ASC LIMIT 10", fetch=True)
    text = "🏆 <b>TOP — Tangalar bo‘yicha</b>\n\n"
    for i, r in enumerate(rows, 1):
        name = f"@{r['username']}" if r["username"] else r["first_name"] or "Foydalanuvchi"
        text += f"{i}. {name} — 🪙 <b>{r['coins']}</b>\n"
    await message.answer(text or "Hozircha TOP bo‘sh.", reply_markup=main_menu())

@dp.message(F.text == "👥 REFERAL")
async def referral(message: Message):
    if not await require_subscription(message): return
    ensure_user(message)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
    count = db("SELECT COUNT(*) c FROM referrals WHERE inviter_id=?", (message.from_user.id,), True)[0]["c"]
    await message.answer(
        f"👥 <b>REFERAL</b>\n\n"
        f"Har bir yangi foydalanuvchi uchun: <b>+1 🪙</b>\n"
        f"Taklif qilganlaringiz: <b>{count}</b>\n\n"
        f"🔗 Sizning havolangiz:\n<code>{link}</code>",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery):
    if await is_subscribed(call.from_user.id):
        await call.answer("✅ Obuna tasdiqlandi!", show_alert=True)
        try:
            await call.message.edit_text("✅ <b>Obuna tasdiqlandi!</b>\n\nBotdan to‘liq foydalanishingiz mumkin.")
        except Exception:
            pass
        await call.message.answer("🏠 Asosiy menyu", reply_markup=main_menu())
    else:
        await call.answer("❌ Hali barcha majburiy kanallarga obuna bo‘lmagansiz.", show_alert=True)

@dp.callback_query(F.data == "user_menu")
async def user_menu(call: CallbackQuery):
    if not await require_subscription(call): return
    await call.answer()
    await call.message.answer("🏠 Asosiy menyu", reply_markup=main_menu())

@dp.callback_query(F.data.startswith("box:"))
async def choose_box(call: CallbackQuery):
    if not await require_subscription(call): return
    try:
        _, rid, box = call.data.split(":")
        rid, box = int(rid), int(box)
    except Exception:
        await call.answer("Xatolik.", show_alert=True)
        return
    r = db("SELECT * FROM rounds WHERE id=? AND user_id=? AND status='open'", (rid, call.from_user.id), True)
    if not r:
        await call.answer("Bu quti tanlovi tugagan.", show_alert=True)
        return
    round_row = r[0]
    reward = db("SELECT * FROM rewards WHERE id=?", (round_row["reward_id"],), True)
    if not reward:
        await call.answer("Mukofot topilmadi.", show_alert=True)
        return
    with conn:
        conn.execute("UPDATE rounds SET status='resolved',resolved_at=? WHERE id=? AND status='open'", (now(), rid))
        conn.execute("UPDATE users SET opened_boxes=opened_boxes+1 WHERE user_id=?", (call.from_user.id,))
    if box == round_row["winning_box"]:
        if reward[0]["reward_type"] == "coins":
            amount = reward[0]["coins"]
            db("UPDATE users SET coins=coins+? WHERE user_id=?", (amount, call.from_user.id))
            result = f"🎉 <b>TABRIKLAYMIZ!</b>\n\nSiz yutuqli qutini tanladingiz!\n\n🪙 Mukofot: <b>+{amount} tanga</b>"
        else:
            code = reward[0]["promo_code"] or reward[0]["name"]
            result = f"🎉 <b>TABRIKLAYMIZ!</b>\n\n🎟️ Sizning promokodingiz:\n<code>{code}</code>"
        await call.answer("🎉 YUTUQ!", show_alert=True)
    else:
        result = "😔 <b>Afsus, omad kelmadi!</b>\n\nKelasi safar aniq o‘xshaydi 😉"
        await call.answer("😔 Bu safar omad kelmadi.", show_alert=True)
    try:
        await call.message.edit_text(result)
    except Exception:
        await call.message.answer(result)

# ---------- ADMIN ----------

def admin_only(call):
    return call.from_user.id in ADMIN_IDS

@dp.callback_query(F.data == "adm_home")
async def adm_home(call: CallbackQuery):
    if not admin_only(call): return
    await call.answer()
    await call.message.edit_text("🛠 <b>BULLDROP ADMIN PANEL</b>", reply_markup=admin_menu())

@dp.callback_query(F.data == "adm_exit")
async def adm_exit(call: CallbackQuery):
    if not admin_only(call): return
    await call.answer()
    await call.message.delete()

@dp.callback_query(F.data == "adm_stats")
async def adm_stats(call: CallbackQuery):
    if not admin_only(call): return
    users = db("SELECT COUNT(*) c FROM users", fetch=True)[0]["c"]
    boxes = db("SELECT COALESCE(SUM(opened_boxes),0) c FROM users", fetch=True)[0]["c"]
    coins = db("SELECT COALESCE(SUM(coins),0) c FROM users", fetch=True)[0]["c"]
    refs = db("SELECT COUNT(*) c FROM referrals", fetch=True)[0]["c"]
    promos = db("SELECT COALESCE(SUM(uses),0) c FROM promo_codes", fetch=True)[0]["c"]
    await call.message.edit_text(
        f"📊 <b>STATISTIKA</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users}</b>\n"
        f"🎁 Ochilgan qutilar: <b>{boxes}</b>\n"
        f"🪙 Hozirgi jami balans: <b>{coins}</b>\n"
        f"👥 Referallar: <b>{refs}</b>\n"
        f"🎟️ Promokod ishlatilishi: <b>{promos}</b>",
        reply_markup=back_admin()
    )

@dp.callback_query(F.data == "adm_channels")
async def adm_channels(call: CallbackQuery):
    if not admin_only(call): return
    chans = db("SELECT * FROM channels ORDER BY id", fetch=True)
    text = "📢 <b>MAJBURIY KANALLAR</b>\n\n"
    if chans:
        for c in chans:
            text += f"#{c['id']} — {c['title']} — <code>{c['chat_id']}</code>\n"
    else:
        text += "Hozircha kanal qo‘shilmagan.\n"
    kb = [
        [InlineKeyboardButton(text="➕ Kanal qo‘shish", callback_data="adm_ch_add")],
        [InlineKeyboardButton(text="➖ Kanal olib tashlash", callback_data="adm_ch_remove")],
        [InlineKeyboardButton(text="📋 Kanallar ro‘yxati", callback_data="adm_channels")],
        [InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm_home")]
    ]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

pending = {}

@dp.callback_query(F.data == "adm_ch_add")
async def adm_ch_add(call: CallbackQuery):
    if not admin_only(call): return
    pending[call.from_user.id] = {"action":"add_channel"}
    await call.message.answer(
        "➕ Kanal qo‘shish\n\n"
        "Botni kanalga <b>admin</b> qiling va kanal username yoki chat ID sini yuboring.\n"
        "Masalan: <code>@bulldrop_uz</code>\n\n"
        "⚠️ Private kanal uchun chat ID (-100...) va bot admin huquqi kerak."
    )
    await call.answer()

@dp.callback_query(F.data == "adm_ch_remove")
async def adm_ch_remove(call: CallbackQuery):
    if not admin_only(call): return
    chans = db("SELECT * FROM channels ORDER BY id", fetch=True)
    if not chans:
        await call.answer("Olib tashlash uchun kanal yo‘q.", show_alert=True); return
    kb=[]
    for c in chans:
        kb.append([InlineKeyboardButton(text=f"❌ {c['title']}", callback_data=f"adm_ch_del:{c['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_channels")])
    await call.message.edit_text("Olib tashlanadigan kanalni tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@dp.callback_query(F.data.startswith("adm_ch_del:"))
async def adm_ch_del(call: CallbackQuery):
    if not admin_only(call): return
    cid=int(call.data.split(":")[1])
    db("DELETE FROM channels WHERE id=?", (cid,))
    await call.answer("✅ Kanal olib tashlandi.")
    await adm_channels(call)

@dp.callback_query(F.data == "adm_rewards")
async def adm_rewards(call: CallbackQuery):
    if not admin_only(call): return
    rows=db("SELECT * FROM rewards WHERE active=1 ORDER BY id", fetch=True)
    text="🎁 <b>QUTI MUKOFOTLARI</b>\n\n"
    if rows:
        for r in rows:
            val=f"{r['coins']} 🪙" if r["reward_type"]=="coins" else f"🎟️ {r['promo_code']}"
            text += f"#{r['id']} — {r['name']} — {val}\n"
    else:
        text += "Mukofot yo‘q.\n"
    kb=[
        [InlineKeyboardButton(text="➕ Tanga mukofoti", callback_data="adm_reward_coin")],
        [InlineKeyboardButton(text="➕ Promokod mukofoti", callback_data="adm_reward_promo")],
        [InlineKeyboardButton(text="➖ Mukofotni o‘chirish", callback_data="adm_reward_del")],
        [InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm_home")]
    ]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "adm_reward_coin")
async def adm_reward_coin(call: CallbackQuery):
    if not admin_only(call): return
    pending[call.from_user.id]={"action":"reward_coin"}
    await call.message.answer("Tanga mukofoti uchun: <code>Nomi | tanga_soni</code>\nMasalan: <code>25 tanga | 25</code>")
    await call.answer()

@dp.callback_query(F.data == "adm_reward_promo")
async def adm_reward_promo(call: CallbackQuery):
    if not admin_only(call): return
    pending[call.from_user.id]={"action":"reward_promo"}
    await call.message.answer("Promokod mukofoti uchun: <code>Nomi | PROMOKOD</code>\nMasalan: <code>500 GOLD | GOLD500</code>")
    await call.answer()

@dp.callback_query(F.data == "adm_reward_del")
async def adm_reward_del(call: CallbackQuery):
    if not admin_only(call): return
    rows=db("SELECT * FROM rewards WHERE active=1 ORDER BY id", fetch=True)
    kb=[[InlineKeyboardButton(text=f"❌ {r['name']}", callback_data=f"adm_reward_delid:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_rewards")])
    await call.message.edit_text("O‘chiriladigan mukofotni tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@dp.callback_query(F.data.startswith("adm_reward_delid:"))
async def adm_reward_delid(call: CallbackQuery):
    if not admin_only(call): return
    rid=int(call.data.split(":")[1])
    db("UPDATE rewards SET active=0 WHERE id=?", (rid,))
    await call.answer("✅ Mukofot o‘chirildi.")
    await adm_rewards(call)

@dp.callback_query(F.data == "adm_promos")
async def adm_promos(call: CallbackQuery):
    if not admin_only(call): return
    rows=db("SELECT * FROM promo_codes ORDER BY id DESC LIMIT 30", fetch=True)
    text="📄 <b>PROMOKODLAR</b>\n\n"
    for p in rows:
        text += f"<code>{p['code']}</code> — {p['uses']}/{p['max_uses']} — {'🟢' if p['active'] else '🔴'}\n"
    if not rows: text += "Promokodlar yo‘q.\n"
    kb=[
        [InlineKeyboardButton(text="➕ Promokod qo‘shish", callback_data="adm_promo_add")],
        [InlineKeyboardButton(text="➖ Promokod o‘chirish", callback_data="adm_promo_del")],
        [InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm_home")]
    ]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "adm_promo_add")
async def adm_promo_add(call: CallbackQuery):
    if not admin_only(call): return
    pending[call.from_user.id]={"action":"promo_add"}
    await call.message.answer("Promokod yuboring:\n<code>KOD | tanga_mukofoti | foydalanish_limiti</code>\nMasalan: <code>GOLD500 | 50 | 100</code>")
    await call.answer()

@dp.callback_query(F.data == "adm_promo_del")
async def adm_promo_del(call: CallbackQuery):
    if not admin_only(call): return
    rows=db("SELECT * FROM promo_codes WHERE active=1 ORDER BY id DESC", fetch=True)
    kb=[[InlineKeyboardButton(text=f"❌ {p['code']}", callback_data=f"adm_promo_delid:{p['id']}")] for p in rows]
    kb.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_promos")])
    await call.message.edit_text("O‘chiriladigan promokodni tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@dp.callback_query(F.data.startswith("adm_promo_delid:"))
async def adm_promo_delid(call: CallbackQuery):
    if not admin_only(call): return
    pid=int(call.data.split(":")[1])
    db("UPDATE promo_codes SET active=0 WHERE id=?", (pid,))
    await call.answer("✅ Promokod o‘chirildi.")
    await adm_promos(call)

@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast(call: CallbackQuery):
    if not admin_only(call): return
    pending[call.from_user.id]={"action":"broadcast"}
    await call.message.answer("📢 Hamma foydalanuvchilarga yuboriladigan xabarni yuboring.\nBekor qilish: /cancel")
    await call.answer()

@dp.message(Command("cancel"))
async def cancel(message: Message):
    if message.from_user.id in pending:
        pending.pop(message.from_user.id, None)
        await message.answer("❌ Bekor qilindi.")

@dp.message()
async def pending_handler(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        # Every non-command action is also blocked until mandatory subscription.
        if not await require_subscription(message):
            return
        return
    action = pending.get(message.from_user.id)
    if not action:
        return
    typ = action["action"]
    try:
        if typ == "add_channel":
            value=message.text.strip()
            chat=await bot.get_chat(value)
            # username may be absent for private channels
            username = chat.username
            title = chat.title or value
            # Verify bot can inspect membership before accepting.
            await bot.get_chat_member(chat.id, message.from_user.id)
            db("INSERT OR IGNORE INTO channels(chat_id,title,username,created_at) VALUES(?,?,?,?)",
               (str(chat.id), title, username, now()))
            await message.answer(f"✅ Kanal qo‘shildi: <b>{title}</b>", reply_markup=admin_menu())
        elif typ == "reward_coin":
            name, amount = [x.strip() for x in message.text.split("|",1)]
            amount=int(amount)
            if amount<=0: raise ValueError()
            db("INSERT INTO rewards(name,reward_type,coins,created_at) VALUES(?,?,?,?)",(name,"coins",amount,now()))
            await message.answer("✅ Tanga mukofoti qo‘shildi.", reply_markup=admin_menu())
        elif typ == "reward_promo":
            name, code = [x.strip() for x in message.text.split("|",1)]
            db("INSERT INTO rewards(name,reward_type,promo_code,created_at) VALUES(?,?,?,?)",(name,"promo",code,now()))
            await message.answer("✅ Promokod mukofoti qo‘shildi.", reply_markup=admin_menu())
        elif typ == "promo_add":
            code, reward, limit = [x.strip() for x in message.text.split("|")]
            db("INSERT INTO promo_codes(code,reward_coins,max_uses,created_at) VALUES(?,?,?,?)",
               (code.upper(), int(reward), int(limit), now()))
            await message.answer("✅ Promokod qo‘shildi.", reply_markup=admin_menu())
        elif typ == "broadcast":
            sent=0
            users=db("SELECT user_id FROM users", fetch=True)
            for u in users:
                try:
                    await bot.copy_message(u["user_id"], message.chat.id, message.message_id)
                    sent += 1
                except Exception:
                    pass
            await message.answer(f"📢 Xabar yuborildi: <b>{sent}</b> ta foydalanuvchi.", reply_markup=admin_menu())
    except Exception as e:
        await message.answer(f"❌ Xatolik: <code>{str(e)[:500]}</code>\n\nQaytadan urinib ko‘ring yoki /cancel bosing.")
        return
    finally:
        pending.pop(message.from_user.id, None)

async def health(request):
    return web.Response(text="BULLDROP BOT OK")

async def start_web_server():
    port = int(os.getenv("PORT", "10000"))
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info("Health server listening on port %s", port)
    return runner

async def main():
    init_db()
    runner = await start_web_server()
    logging.info("BULLDROP bot started")
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
