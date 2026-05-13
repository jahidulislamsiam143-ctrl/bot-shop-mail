import sqlite3
import csv
import io
import logging
import os
import hashlib
from datetime import datetime, timedelta
from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ════════════════════════════════════════════════
#              LOGGING & CONFIGURATION
# ════════════════════════════════════════════════
logging.basicConfig(
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

BOT_TOKEN  = "8513630170:AAHE7VtWv8949YhQbxkNB7zZHpMDVfV0KJk"   # ← replace
ADMIN_IDS  = {6644381377}             # ← replace / add more IDs
CURRENCY   = "৳"

# ════════════════════════════════════════════════
#                  DATABASE SETUP
# ════════════════════════════════════════════════
DB = 'shop.db'

def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()

    # Core tables
    c.executescript('''
        CREATE TABLE IF NOT EXISTS emails (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            product_type TEXT NOT NULL,
            data         TEXT NOT NULL,
            status       TEXT DEFAULT 'available',
            sold_to      INTEGER,
            sold_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS products (
            name         TEXT PRIMARY KEY,
            price        REAL NOT NULL,
            description  TEXT DEFAULT '',
            category     TEXT DEFAULT 'General',
            min_order    INTEGER DEFAULT 1,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS users (
            user_id      INTEGER PRIMARY KEY,
            username     TEXT,
            full_name    TEXT,
            balance      REAL DEFAULT 0.0,
            total_spent  REAL DEFAULT 0.0,
            joined_at    TEXT DEFAULT (datetime('now')),
            is_banned    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS orders (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            product    TEXT NOT NULL,
            qty        INTEGER NOT NULL,
            total      REAL NOT NULL,
            date       TEXT NOT NULL,
            status     TEXT DEFAULT 'completed'
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            type       TEXT NOT NULL,
            amount     REAL NOT NULL,
            trx_id     TEXT,
            method     TEXT,
            status     TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            reviewed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS announcements (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            message    TEXT,
            sent_at    TEXT DEFAULT (datetime('now')),
            reach      INTEGER DEFAULT 0
        );
    ''')

    # Default settings
    defaults = [
        ('support_user',  '@Support_Username'),
        ('payment_user',  '@Payment_Admin'),
        ('bkash',         '01978766528'),
        ('nagad',         '01978766528'),
        ('binance',       '1196047280'),
        ('welcome_msg',   'আমাদের শপে স্বাগতম! 🎉'),
        ('min_recharge',  '10'),
        ('shop_name',     'My Digital Shop'),
        ('maintenance',   '0'),
    ]
    for key, val in defaults:
        c.execute('INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)', (key, val))

    conn.commit()
    conn.close()
    logger.info("Database initialized.")

init_db()

# ════════════════════════════════════════════════
#                   HELPERS
# ════════════════════════════════════════════════
def get_setting(key: str) -> str:
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone(); conn.close()
    return row['value'] if row else "Not Set"

def set_setting(key: str, value: str):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
    conn.commit(); conn.close()

def get_products():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM products ORDER BY name")
    res = c.fetchall(); conn.close()
    return res

def get_user(uid: int):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    row = c.fetchone(); conn.close()
    return row

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def is_banned(uid: int) -> bool:
    u = get_user(uid)
    return bool(u and u['is_banned'])

def stock_count(product_type: str) -> int:
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM emails WHERE status='available' AND product_type=?", (product_type,))
    row = c.fetchone(); conn.close()
    return row['cnt']

def get_all_user_ids():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_banned=0")
    rows = c.fetchall(); conn.close()
    return [r['user_id'] for r in rows]

def upsert_user(uid: int, username: str = None, full_name: str = None):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?,?,?)",
        (uid, username, full_name)
    )
    if username or full_name:
        conn.execute(
            "UPDATE users SET username=COALESCE(?,username), full_name=COALESCE(?,full_name) WHERE user_id=?",
            (username, full_name, uid)
        )
    conn.commit(); conn.close()

def format_date(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%d %b %Y, %I:%M %p")
    except:
        return dt_str or "N/A"

def generate_order_id() -> str:
    return hashlib.md5(datetime.now().isoformat().encode()).hexdigest()[:8].upper()

# Helper function to check if a line is a header
def is_header_line(line: str) -> bool:
    """Check if a line looks like a header row"""
    line_lower = line.lower().strip()
    header_keywords = [
        'email', 'mail', 'data', 'account', 'user', 'pass', 'password',
        'email:pass', 'mail:pass', 'email:password', 'username', 'login',
        'id', 'name', 'type', 'product', 'category', 'item', 'goods',
        'পন্য', 'ডাটা', 'ইমেইল', 'মেইল', 'তথ্য'
    ]
    return any(keyword == line_lower for keyword in header_keywords)

# ════════════════════════════════════════════════
#                   KEYBOARDS
# ════════════════════════════════════════════════
def main_menu(uid: int):
    kb = [
        ["🛒 পন্য কিনুন", "💰 ব্যালেন্স রিচার্জ"],
        ["👤 আমার প্রোফাইল", "📊 স্টক চেক"],
        ["📞 কাস্টমার সাপোর্ট"]
    ]
    if is_admin(uid):
        kb.append(["⚙️ অ্যাডমিন প্যানেল"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def admin_menu():
    kb = [
        ["➕ স্টক আপডেট", "📦 পন্য ম্যানেজ"],
        ["💵 ব্যালেন্স ম্যানেজ", "🔧 বট সেটিংস"],
        ["📢 ব্রডকাস্ট", "👥 ইউজার ম্যানেজ"],
        ["📊 এনালিটিক্স", "📋 অর্ডার হিস্টরি"],
        ["💹 ট্রানজেকশন লগ", "📥 ইউজার অর্ডার ডাউনলোড"],
        ["🔙 মেইন মেনু"]
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def product_mgmt_menu():
    kb = [
        ["🆕 নতুন পন্য", "❌ পন্য ডিলিট"],
        ["🏷 দাম পরিবর্তন", "📝 বিবরণ আপডেট"],
        ["🔙 অ্যাডমিন প্যানেল"]
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def user_mgmt_menu():
    kb = [
        ["🔍 ইউজার খুঁজুন", "🚫 ব্যান ম্যানেজ"],
        ["💰 ব্যালেন্স সেট", "📤 ব্যালেন্স রিসেট"],
        ["👥 সকল ইউজার", "🔙 অ্যাডমিন প্যানেল"]
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def payment_method_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 bKash", callback_data="method_bkash"),
         InlineKeyboardButton("📱 Nagad", callback_data="method_nagad")],
        [InlineKeyboardButton("💳 Binance", callback_data="method_binance")],
        [InlineKeyboardButton("❌ বাতিল", callback_data="cancel_payment")]
    ])

def settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 bKash নম্বর", callback_data="set_bkash"),
         InlineKeyboardButton("📱 Nagad নম্বর", callback_data="set_nagad")],
        [InlineKeyboardButton("💳 Binance ID", callback_data="set_binance"),
         InlineKeyboardButton("🎧 Support", callback_data="set_support_user")],
        [InlineKeyboardButton("🏪 শপের নাম", callback_data="set_shop_name"),
         InlineKeyboardButton("📢 স্বাগত বার্তা", callback_data="set_welcome_msg")],
        [InlineKeyboardButton("💰 Min Recharge", callback_data="set_min_recharge"),
         InlineKeyboardButton("🔧 মেইনটেন্যান্স ON/OFF", callback_data="toggle_maintenance")]
    ])

# ════════════════════════════════════════════════
#              /start COMMAND
# ════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = user.id

    upsert_user(uid, user.username, user.full_name)

    if is_banned(uid):
        await update.message.reply_text("🚫 আপনার অ্যাকাউন্ট ব্যান করা হয়েছে। সাপোর্টে যোগাযোগ করুন।")
        return

    if get_setting('maintenance') == '1' and not is_admin(uid):
        await update.message.reply_text("🔧 বটটি এখন মেইনটেন্যান্সে আছে। কিছুক্ষণ পর আবার চেষ্টা করুন।")
        return

    shop = get_setting('shop_name')
    welcome = get_setting('welcome_msg')
    await update.message.reply_text(
        f"🏪 <b>{shop}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{welcome}\n\n"
        f"👤 <b>নাম:</b> {user.full_name}\n"
        f"🆔 <b>আইডি:</b> <code>{uid}</code>\n\n"
        f"💡 <b>কী করতে পারবেন?</b>\n"
        f"  🛒 পণ্য কিনুন — ডিজিটাল পণ্য সংগ্রহ করুন\n"
        f"  💰 রিচার্জ করুন — bKash / Nagad / Binance\n"
        f"  👤 প্রোফাইল — ব্যালেন্স ও অর্ডার দেখুন\n"
        f"  📞 সাপোর্ট — যেকোনো সমস্যায় যোগাযোগ করুন\n\n"
        f"⬇️ নিচের মেনু থেকে শুরু করুন:",
        reply_markup=main_menu(uid),
        parse_mode="HTML"
    )

# ════════════════════════════════════════════════
#           CALLBACK QUERY HANDLER
# ════════════════════════════════════════════════
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data  = query.data
    uid   = query.from_user.id
    await query.answer()

    # ── Payment method selection ──
    if data.startswith("method_"):
        method = data.replace("method_", "")
        amt    = context.user_data.get('a', '0')
        num    = get_setting(method)
        method_labels = {'bkash': 'bKash', 'nagad': 'Nagad', 'binance': 'Binance'}
        label = method_labels.get(method, method)
        context.user_data['pay_method'] = method
        context.user_data['state'] = 'waiting_trx_id'
        msg = (
            f"💳 <b>পেমেন্ট বিস্তারিত — {label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{'📱' if method != 'binance' else '💳'} <b>নম্বর/আইডি:</b> <code>{num}</code>\n"
            f"💵 <b>পরিমাণ:</b> {CURRENCY}{amt}\n\n"
            f"📋 <b>পেমেন্ট করার নিয়ম:</b>\n"
            f"  ১. উপরের নম্বরে {'সেন্ড মানি' if method != 'binance' else 'Transfer'} করুন\n"
            f"  ২. পেমেন্ট সম্পন্ন হলে TrxID/Transaction ID কপি করুন\n"
            f"  ৩. নিচে TrxID টাইপ করে পাঠান\n\n"
            f"⚠️ <b>সতর্কতা:</b> ভুল TrxID দিলে পেমেন্ট বাতিল হবে।\n"
            f"⏳ অ্যাডমিন সাধারণত ৫-১৫ মিনিটের মধ্যে অনুমোদন করেন।\n\n"
            f"👇 এখন আপনার <b>TrxID</b> পাঠান:"
        )
        await query.message.reply_text(msg, parse_mode="HTML")
        return

    elif data == "cancel_payment":
        context.user_data.clear()
        await query.message.reply_text("❌ পেমেন্ট বাতিল করা হয়েছে।", reply_markup=main_menu(uid))
        return

    # ── TrxID submission ──
    elif data == "submit_trx":
        context.user_data['state'] = 'waiting_trx_id'
        await query.message.reply_text(
            "📩 <b>TrxID সাবমিট:</b>\n\nটাকা পাঠানোর পর প্রাপ্ত <b>TrxID</b> লিখুন।",
            parse_mode="HTML"
        )
        return

    # ── Admin approve/reject payment ──
    elif data.startswith(("accept_", "cancel_")):
        try:
            parts    = data.split("_")
            action   = parts[0]
            target   = int(parts[1])
            amt      = float(parts[2])
            trx_id   = parts[3]
            method   = parts[4] if len(parts) > 4 else "N/A"
            now      = datetime.now().isoformat()

            conn = get_conn()
            if action == "accept":
                conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amt, target))
                conn.execute(
                    "UPDATE transactions SET status='approved', reviewed_at=? WHERE trx_id=? AND user_id=?",
                    (now, trx_id, target)
                )
                conn.commit(); conn.close()
                await query.edit_message_text(
                    f"✅ <b>অনুমোদিত!</b>\n👤 ইউজার: <code>{target}</code>\n💰 পরিমাণ: {CURRENCY}{amt}\n🔑 TrxID: {trx_id}\n📅 {format_date(now)}",
                    parse_mode="HTML"
                )
                await context.bot.send_message(
                    chat_id=target,
                    text=(
                        f"🎉 <b>পেমেন্ট অনুমোদিত!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"✅ <b>{CURRENCY}{amt}</b> আপনার ওয়ালেটে যোগ হয়েছে!\n"
                        f"🔑 TrxID: <code>{trx_id}</code>\n"
                        f"📅 সময়: {format_date(now)}\n\n"
                        f"🛒 এখন পণ্য কিনতে পারবেন। ধন্যবাদ!"
                    ),
                    parse_mode="HTML"
                )
            else:
                conn.execute(
                    "UPDATE transactions SET status='rejected', reviewed_at=? WHERE trx_id=? AND user_id=?",
                    (now, trx_id, target)
                )
                conn.commit(); conn.close()
                await query.edit_message_text(
                    f"❌ <b>প্রত্যাখ্যাত!</b>\n👤 ইউজার: <code>{target}</code>\n💰 পরিমাণ: {CURRENCY}{amt}\n🔑 TrxID: {trx_id}",
                    parse_mode="HTML"
                )
                await context.bot.send_message(
                    chat_id=target,
                    text=(
                        f"⚠️ <b>পেমেন্ট প্রত্যাখ্যাত!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"❌ আপনার TrxID <code>{trx_id}</code> ভেরিফাই করা যায়নি।\n\n"
                        f"🔎 <b>সম্ভাব্য কারণ:</b>\n"
                        f"  • TrxID ভুল বা অসম্পূর্ণ\n"
                        f"  • পেমেন্ট সম্পন্ন হয়নি\n"
                        f"  • ভুল নম্বরে পাঠানো হয়েছে\n\n"
                        f"📞 সমস্যা থাকলে সাপোর্টে যোগাযোগ করুন।"
                    ),
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Callback error: {e}")
        return

    # ── Settings edit ──
    elif data.startswith("set_"):
        key = data.replace("set_", "")
        context.user_data['state'] = f'edit_{key}'
        labels = {
            'bkash': 'bKash নম্বর', 'nagad': 'Nagad নম্বর', 'binance': 'Binance আইডি',
            'support_user': 'সাপোর্ট ইউজারনেম', 'shop_name': 'শপের নাম',
            'welcome_msg': 'স্বাগত বার্তা', 'min_recharge': 'মিনিমাম রিচার্জ'
        }
        label = labels.get(key, key)
        await query.message.reply_text(f"📝 <b>{label} আপডেট করুন:</b>\n\nনতুন ভ্যালু লিখে পাঠান।", parse_mode="HTML")
        return

    elif data == "toggle_maintenance":
        current = get_setting('maintenance')
        new_val = '0' if current == '1' else '1'
        set_setting('maintenance', new_val)
        status = "চালু 🔴" if new_val == '1' else "বন্ধ 🟢"
        await query.message.reply_text(f"🔧 মেইনটেন্যান্স মোড এখন {status}")
        return

    # ── Product rename ──
    elif data.startswith("p_rename_"):
        context.user_data['old_n']  = data.replace("p_rename_", "")
        context.user_data['state']  = 'en_rename'
        await query.message.reply_text(f"✏️ <b>{context.user_data['old_n']}</b> এর নতুন নাম লিখুন:", parse_mode="HTML")
        return

    # ── Admin download user last order ──
    elif data.startswith("dl_order_"):
        parts    = data.split("_")
        t_uid    = int(parts[2])
        order_id = int(parts[3])
        conn     = get_conn()
        order    = conn.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, t_uid)).fetchone()
        if not order:
            conn.close()
            await query.message.reply_text("❌ অর্ডার পাওয়া যায়নি।")
            return
        sold_items = conn.execute(
            "SELECT data FROM emails WHERE sold_to=? AND product_type=? AND sold_at=?",
            (t_uid, order['product'], order['date'])
        ).fetchall()
        conn.close()

        if not sold_items:
            await query.message.reply_text(
                f"⚠️ এই অর্ডারের ডেটা পাওয়া যায়নি।\n"
                f"অর্ডার ID: #{order_id} | পণ্য: {order['product']}"
            )
            return

        out    = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['Data'])
        for row in sold_items:
            writer.writerow([row['data']])
        out.seek(0)

        caption = (
            f"📥 <b>অ্যাডমিন ডাউনলোড</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 ইউজার ID: <code>{t_uid}</code>\n"
            f"🆔 অর্ডার ID: <code>#{order_id}</code>\n"
            f"📦 পণ্য: <b>{order['product']}</b>\n"
            f"🔢 পরিমাণ: {order['qty']} পিস\n"
            f"💸 মোট: {CURRENCY}{order['total']:.2f}\n"
            f"📅 তারিখ: {order['date']}\n"
            f"📊 ডেটা সংখ্যা: {len(sold_items)} টি"
        )
        await query.message.reply_document(
            document=io.BytesIO(out.getvalue().encode()),
            filename=f"User_{t_uid}_Order_{order_id}_{order['product']}.csv",
            caption=caption,
            parse_mode="HTML"
        )
        return

    # ── Ban/Unban ──
    elif data.startswith("ban_") or data.startswith("unban_"):
        action, target_id = data.split("_", 1)
        val = 1 if action == "ban" else 0
        conn = get_conn()
        conn.execute("UPDATE users SET is_banned=? WHERE user_id=?", (val, int(target_id)))
        conn.commit(); conn.close()
        label = "ব্যান" if val else "আনব্যান"
        await query.edit_message_text(f"{'🚫' if val else '✅'} ইউজার <code>{target_id}</code> কে {label} করা হয়েছে।", parse_mode="HTML")
        if val:
            try:
                await context.bot.send_message(chat_id=int(target_id), text="🚫 আপনার অ্যাকাউন্ট ব্যান করা হয়েছে।")
            except:
                pass
        return

# ════════════════════════════════════════════════
#            MAIN MESSAGE HANDLER
# ════════════════════════════════════════════════
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    uid   = user.id
    msg   = update.message
    text  = msg.text if msg.text else ""
    state = context.user_data.get('state')

    upsert_user(uid, user.username, user.full_name)

    if is_banned(uid):
        await msg.reply_text("🚫 আপনার অ্যাকাউন্ট ব্যান করা হয়েছে।")
        return

    if get_setting('maintenance') == '1' and not is_admin(uid):
        await msg.reply_text("🔧 মেইনটেন্যান্স চলছে। পরে আসুন।")
        return

    # ════ STATE MACHINE ════

    # ── TrxID waiting ──
    if state == 'waiting_trx_id' and text:
        amt     = context.user_data.get('a', '0')
        method  = context.user_data.get('pay_method', 'N/A')
        trx_id  = text.strip()
        now     = datetime.now().isoformat()

        conn = get_conn()
        conn.execute(
            "INSERT INTO transactions (user_id, type, amount, trx_id, method, status, created_at) VALUES (?,?,?,?,?,?,?)",
            (uid, 'recharge', float(amt), trx_id, method, 'pending', now)
        )
        conn.commit(); conn.close()

        admin_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ অনুমোদন", callback_data=f"accept_{uid}_{amt}_{trx_id}_{method}"),
             InlineKeyboardButton("❌ প্রত্যাখ্যান", callback_data=f"cancel_{uid}_{amt}_{trx_id}_{method}")]
        ])
        u = get_user(uid)
        uname = f"@{u['username']}" if u and u['username'] else "N/A"

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"🔔 <b>নতুন রিচার্জ রিকোয়েস্ট</b>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"👤 ইউজার: {uname} (<code>{uid}</code>)\n"
                        f"💰 পরিমাণ: {CURRENCY}{amt}\n"
                        f"📱 মাধ্যম: {method.upper()}\n"
                        f"🔑 TrxID: <code>{trx_id}</code>\n"
                        f"📅 সময়: {format_date(now)}"
                    ),
                    reply_markup=admin_kb,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Admin notify error: {e}")

        await msg.reply_text(
            f"✅ <b>TrxID সফলভাবে জমা হয়েছে!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 পরিমাণ: {CURRENCY}{amt}\n"
            f"📱 মাধ্যম: {method.upper()}\n"
            f"🔑 TrxID: <code>{trx_id}</code>\n"
            f"📅 সময়: {format_date(now)}\n\n"
            f"⏳ অ্যাডমিন শীঘ্রই ভেরিফাই করবেন।\n"
            f"অনুমোদন হলে আপনাকে নোটিফিকেশন পাঠানো হবে।",
            reply_markup=main_menu(uid),
            parse_mode="HTML"
        )
        context.user_data.clear()
        return

    # ── Admin: waiting user ID for order download ──
    elif state == 'download_order_uid' and is_admin(uid):
        try:
            t_uid = int(text.strip())
            conn  = get_conn()
            all_orders = conn.execute(
                "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (t_uid,)
            ).fetchall()
            u = conn.execute("SELECT * FROM users WHERE user_id=?", (t_uid,)).fetchone()
            conn.close()

            if not all_orders:
                await msg.reply_text(
                    f"❌ ইউজার <code>{t_uid}</code> এর কোনো অর্ডার পাওয়া যায়নি।",
                    parse_mode="HTML"
                )
                context.user_data.clear()
                return

            uname      = f"@{u['username']}" if u and u['username'] else "N/A"
            uname_full = u['full_name'] if u and u['full_name'] else "N/A"

            buttons = []
            for o in all_orders:
                label = f"#{o['id']} | {o['product']} × {o['qty']} | {o['date'][:10]}"
                buttons.append([InlineKeyboardButton(
                    f"📥 {label}",
                    callback_data=f"dl_order_{t_uid}_{o['id']}"
                )])
            kb = InlineKeyboardMarkup(buttons)

            await msg.reply_text(
                f"👤 <b>ইউজার অর্ডার তালিকা</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 ID: <code>{t_uid}</code>\n"
                f"👤 নাম: {uname_full}\n"
                f"📱 ইউজার: {uname}\n"
                f"🛒 সর্বশেষ {len(all_orders)}টি অর্ডার দেখানো হচ্ছে\n\n"
                f"👇 যে অর্ডারটি ডাউনলোড করতে চান সেটিতে চাপুন:",
                reply_markup=kb,
                parse_mode="HTML"
            )
            context.user_data.clear()
        except ValueError:
            await msg.reply_text("❌ সঠিক ইউজার ID (সংখ্যা) দিন।")
        return

    # ── Product rename ──
    elif state == 'en_rename' and is_admin(uid):
        old_n = context.user_data.get('old_n')
        new_n = text.strip()
        conn  = get_conn()
        conn.execute("UPDATE products SET name=? WHERE name=?", (new_n, old_n))
        conn.execute("UPDATE emails SET product_type=? WHERE product_type=?", (new_n, old_n))
        conn.commit(); conn.close()
        await msg.reply_text(f"✅ <b>{old_n}</b> → <b>{new_n}</b> নাম পরিবর্তন সফল।", parse_mode="HTML")
        context.user_data.clear()
        return

    # ── File upload (admin stock) ── FIXED VERSION ──
    if msg.document and is_admin(uid) and state == 'waiting_file':
        p_type  = context.user_data.get('selected_p_type')
        file    = await msg.document.get_file()
        content = await file.download_as_bytearray()
        lines   = content.decode('utf-8').splitlines()
        conn    = get_conn()
        added   = 0
        skipped = 0
        
        logger.info(f"Processing file upload for product: {p_type}")
        logger.info(f"Total lines in file: {len(lines)}")
        
        # Process each line
        for i, line in enumerate(lines):
            line = line.strip()
            
            # Skip empty lines
            if not line:
                skipped += 1
                continue
            
            # Skip first line (likely header)
            if i == 0:
                if is_header_line(line):
                    logger.info(f"Skipping header line {i+1}: {line}")
                    skipped += 1
                    continue
                # If first line doesn't look like header but could be, check more carefully
                if line.lower() in ['email', 'mail', 'data', 'email:pass', 'mail:pass']:
                    logger.info(f"Skipping identified header line {i+1}: {line}")
                    skipped += 1
                    continue
            
            # Skip lines that look like headers
            if is_header_line(line):
                logger.info(f"Skipping potential header line {i+1}: {line}")
                skipped += 1
                continue
            
            # Insert valid data
            try:
                conn.execute("INSERT INTO emails (product_type, data) VALUES (?,?)", (p_type, line))
                added += 1
                logger.info(f"Added line {i+1}: {line[:50]}...")
            except Exception as e:
                logger.error(f"Error inserting line {i+1}: {e}")
                skipped += 1
        
        conn.commit()
        current_stock = stock_count(p_type)
        conn.close()
        
        if added > 0:
            await msg.reply_text(
                f"✨ <b>স্টক আপডেট সফল!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 পণ্য: <b>{p_type}</b>\n"
                f"✅ যোগ হয়েছে: {added} টি আইটেম\n"
                f"⏭️ বাদ দেওয়া হয়েছে: {skipped} টি\n"
                f"📊 বর্তমান স্টক: {current_stock} টি\n\n"
                f"💡 বাদ দেওয়া লাইনগুলো হেডার বা ফাঁকা ছিল।",
                parse_mode="HTML"
            )
        else:
            await msg.reply_text(
                f"⚠️ <b>কোনো ডেটা যোগ করা যায়নি!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 পণ্য: <b>{p_type}</b>\n"
                f"📄 মোট লাইন: {len(lines)}\n"
                f"⏭️ বাদ: {skipped} টি\n\n"
                f"🔍 <b>সম্ভাব্য কারণ:</b>\n"
                f"  • ফাইলটি খালি অথবা শুধু হেডার আছে\n"
                f"  • সব লাইন বাদ পড়েছে\n"
                f"  • ফরম্যাট সঠিক নয়\n\n"
                f"💡 <b>.txt</b> ফাইলে প্রতি লাইনে একটি করে ডেটা দিন।\n"
                f"প্রথম লাইনটি হেডার হলে তা বাদ দেওয়া হবে।",
                parse_mode="HTML"
            )
        
        context.user_data.clear()
        return

    # ════════════════════════════════════════════════
    #            ADMIN PANEL HANDLERS
    # ════════════════════════════════════════════════
    if is_admin(uid):

        # ── Settings update ──
        if state and state.startswith('edit_'):
            key = state.replace('edit_', '')
            set_setting(key, text.strip())
            await msg.reply_text(f"✅ <b>{key}</b> আপডেট হয়েছে → <code>{text.strip()}</code>", reply_markup=admin_menu(), parse_mode="HTML")
            context.user_data.clear()
            return

        # ── Broadcast ──
        elif state == 'waiting_broadcast':
            all_users = get_all_user_ids()
            sent = 0
            for u_id in all_users:
                try:
                    await context.bot.send_message(
                        chat_id=u_id,
                        text=f"📢 <b>{get_setting('shop_name')} — ঘোষণা</b>\n\n{text}",
                        parse_mode="HTML"
                    )
                    sent += 1
                except:
                    pass
            conn = get_conn()
            conn.execute("INSERT INTO announcements (message, reach) VALUES (?,?)", (text, sent))
            conn.commit(); conn.close()
            await msg.reply_text(f"✅ ব্রডকাস্ট শেষ! মোট {sent} জনের কাছে পাঠানো হয়েছে।", reply_markup=admin_menu())
            context.user_data.clear()
            return

        # ── Add product name ──
        elif state == 'add_p_name':
            context.user_data['n'] = text.strip()
            context.user_data['state'] = 'add_p_desc'
            await msg.reply_text(f"📝 <b>{text}</b> এর বিবরণ লিখুন (বা skip):")
            return

        # ── Add product description ──
        elif state == 'add_p_desc':
            context.user_data['desc'] = "" if text.lower() == "skip" else text.strip()
            context.user_data['state'] = 'add_p_price'
            await msg.reply_text(f"💰 প্রতি পিসের দাম কত? (শুধু সংখ্যা):")
            return

        # ── Add product price ──
        elif state == 'add_p_price':
            try:
                price = float(text)
                name  = context.user_data['n']
                desc  = context.user_data.get('desc', '')
                conn  = get_conn()
                conn.execute("INSERT OR REPLACE INTO products (name, price, description) VALUES (?,?,?)", (name, price, desc))
                conn.commit(); conn.close()
                await msg.reply_text(f"✅ <b>{name}</b> যোগ হয়েছে! দাম: {CURRENCY}{price}", reply_markup=admin_menu(), parse_mode="HTML")
                context.user_data.clear()
            except ValueError:
                await msg.reply_text("❌ দাম সংখ্যায় দিন!")
            return

        # ── Price edit ──
        elif state == 'en_price':
            try:
                conn = get_conn()
                conn.execute("UPDATE products SET price=? WHERE name=?", (float(text), context.user_data['en']))
                conn.commit(); conn.close()
                await msg.reply_text(f"✅ দাম আপডেট হয়েছে → {CURRENCY}{text}", reply_markup=product_mgmt_menu())
                context.user_data.clear()
            except:
                await msg.reply_text("❌ সংখ্যা দিন।")
            return

        # ── Description edit ──
        elif state == 'en_desc':
            conn = get_conn()
            conn.execute("UPDATE products SET description=? WHERE name=?", (text.strip(), context.user_data['en']))
            conn.commit(); conn.close()
            await msg.reply_text("✅ বিবরণ আপডেট হয়েছে!", reply_markup=product_mgmt_menu())
            context.user_data.clear()
            return

        # ── Manual balance ──
        elif state == 'man_bal':
            try:
                parts  = text.split(":")
                t_uid  = int(parts[0].strip())
                amount = float(parts[1].strip())
                conn   = get_conn()
                conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (t_uid,))
                conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, t_uid))
                conn.execute(
                    "INSERT INTO transactions (user_id, type, amount, status, method) VALUES (?,?,?,?,?)",
                    (t_uid, 'manual_credit', amount, 'approved', 'admin')
                )
                conn.commit(); conn.close()
                await msg.reply_text(f"✅ ইউজার <code>{t_uid}</code> এ {CURRENCY}{amount} যোগ হয়েছে।", reply_markup=admin_menu(), parse_mode="HTML")
                try:
                    await context.bot.send_message(
                        chat_id=t_uid,
                        text=(
                            f"🎁 <b>অ্যাডমিন ব্যালেন্স যোগ করেছেন!</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"✅ {CURRENCY}{amount} আপনার ওয়ালেটে যোগ হয়েছে।\n"
                            f"📅 সময়: {datetime.now().strftime('%d %b %Y, %I:%M %p')}\n\n"
                            f"🛒 এখন পণ্য কিনতে পারবেন!"
                        ),
                        parse_mode="HTML"
                    )
                except:
                    pass
                context.user_data.clear()
            except:
                await msg.reply_text("❌ ফরম্যাট: `User_ID:Amount`")
            return

        # ── Balance reset ──
        elif state == 'reset_bal':
            try:
                t_uid = int(text.strip())
                conn  = get_conn()
                conn.execute("UPDATE users SET balance=0 WHERE user_id=?", (t_uid,))
                conn.commit(); conn.close()
                await msg.reply_text(f"✅ ইউজার <code>{t_uid}</code> এর ব্যালেন্স রিসেট হয়েছে।", reply_markup=user_mgmt_menu(), parse_mode="HTML")
                context.user_data.clear()
            except:
                await msg.reply_text("❌ সঠিক ইউজার ID দিন।")
            return

        # ── User search ──
        elif state == 'search_user':
            try:
                t_uid = int(text.strip())
                u     = get_user(t_uid)
                if u:
                    conn = get_conn()
                    orders = conn.execute(
                        "SELECT COUNT(*) as cnt, SUM(total) as tot FROM orders WHERE user_id=?", (t_uid,)
                    ).fetchone()
                    conn.close()
                    ban_label = "🚫 ব্যান" if u['is_banned'] else "✅ সক্রিয়"
                    ban_action = "unban" if u['is_banned'] else "ban"
                    ban_emoji  = "✅ আনব্যান" if u['is_banned'] else "🚫 ব্যান"
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton(ban_emoji, callback_data=f"{ban_action}_{t_uid}")
                    ]])
                    await msg.reply_text(
                        f"👤 <b>ইউজার প্রোফাইল</b>\n"
                        f"━━━━━━━━━━━━━\n"
                        f"🆔 আইডি: <code>{u['user_id']}</code>\n"
                        f"👤 নাম: {u['full_name'] or 'N/A'}\n"
                        f"📱 ইউজার: @{u['username'] or 'N/A'}\n"
                        f"💰 ব্যালেন্স: {CURRENCY}{u['balance']:.2f}\n"
                        f"🛒 মোট অর্ডার: {orders['cnt']}\n"
                        f"💸 মোট ব্যয়: {CURRENCY}{orders['tot'] or 0:.2f}\n"
                        f"📅 যোগদান: {format_date(u['joined_at'])}\n"
                        f"🔒 স্ট্যাটাস: {ban_label}",
                        reply_markup=kb,
                        parse_mode="HTML"
                    )
                else:
                    await msg.reply_text("❌ ইউজার পাওয়া যায়নি।")
                context.user_data.clear()
            except:
                await msg.reply_text("❌ সঠিক ইউজার ID দিন।")
            return

        # ════ ADMIN MENU BUTTONS ════
        if text == "⚙️ অ্যাডমিন প্যানেল":
            await msg.reply_text(
                "🛠 <b>অ্যাডমিন কন্ট্রোল সেন্টার</b>\n\nসকল ফিচার এখান থেকে পরিচালনা করুন।",
                reply_markup=admin_menu(),
                parse_mode="HTML"
            )
            return

        elif text == "➕ স্টক আপডেট":
            prods = get_products()
            if not prods:
                await msg.reply_text("❌ আগে পন্য যোগ করুন।")
                return
            kb = [[f"UPLOAD:{p['name']}"] for p in prods]
            kb.append(["🔙 অ্যাডমিন প্যানেল"])
            await msg.reply_text("📦 কোন পন্যে স্টক যোগ করবেন?", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
            return

        elif text == "📦 পন্য ম্যানেজ":
            prods = get_products()
            await msg.reply_text("📦 <b>পন্য ম্যানেজমেন্ট:</b>", reply_markup=product_mgmt_menu(), parse_mode="HTML")
            if prods:
                for p in prods:
                    stk = stock_count(p['name'])
                    kb  = InlineKeyboardMarkup([[
                        InlineKeyboardButton("✏️ নাম", callback_data=f"p_rename_{p['name']}")
                    ]])
                    await msg.reply_text(
                        f"🔸 <b>{p['name']}</b>\n"
                        f"💰 দাম: {CURRENCY}{p['price']}\n"
                        f"📝 বিবরণ: {p['description'] or 'নেই'}\n"
                        f"📦 স্টক: {stk} টি",
                        reply_markup=kb,
                        parse_mode="HTML"
                    )
            return

        elif text == "🆕 নতুন পন্য":
            context.user_data['state'] = 'add_p_name'
            await msg.reply_text("📝 নতুন পন্যের নাম লিখুন:")
            return

        elif text == "❌ পন্য ডিলিট":
            prods = get_products()
            if not prods:
                await msg.reply_text("❌ কোনো পন্য নেই।")
                return
            kb = [[f"DEL:{p['name']}"] for p in prods]
            kb.append(["🔙 পন্য ম্যানেজ"])
            await msg.reply_text("🗑 কোনটি ডিলিট করবেন?", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
            return

        elif text == "🏷 দাম পরিবর্তন":
            prods = get_products()
            if not prods:
                await msg.reply_text("❌ কোনো পন্য নেই।")
                return
            kb = [[f"PRICE:{p['name']}"] for p in prods]
            kb.append(["🔙 পন্য ম্যানেজ"])
            await msg.reply_text("🏷 কার দাম পরিবর্তন করবেন?", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
            return

        elif text == "📝 বিবরণ আপডেট":
            prods = get_products()
            if not prods:
                await msg.reply_text("❌ কোনো পন্য নেই।")
                return
            kb = [[f"DESC:{p['name']}"] for p in prods]
            kb.append(["🔙 পন্য ম্যানেজ"])
            await msg.reply_text("📝 কার বিবরণ আপডেট করবেন?", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
            return

        elif text == "🔧 বট সেটিংস":
            maintenance = "🔴 চালু" if get_setting('maintenance') == '1' else "🟢 বন্ধ"
            await msg.reply_text(
                f"⚙️ <b>বট কনফিগারেশন</b>\n"
                f"━━━━━━━━━━━━━\n"
                f"🏪 শপ: {get_setting('shop_name')}\n"
                f"📱 bKash: {get_setting('bkash')}\n"
                f"📱 Nagad: {get_setting('nagad')}\n"
                f"💳 Binance: {get_setting('binance')}\n"
                f"🔧 Maintenance: {maintenance}\n\n"
                f"যা পরিবর্তন করতে চান বেছে নিন:",
                reply_markup=settings_keyboard(),
                parse_mode="HTML"
            )
            return

        elif text == "💵 ব্যালেন্স ম্যানেজ":
            context.user_data['state'] = 'man_bal'
            await msg.reply_text(
                "💰 <b>ব্যালেন্স যোগ করুন:</b>\nফরম্যাট: <code>User_ID:Amount</code>",
                parse_mode="HTML"
            )
            return

        elif text == "📢 ব্রডকাস্ট":
            count = len(get_all_user_ids())
            context.user_data['state'] = 'waiting_broadcast'
            await msg.reply_text(f"📢 মোট {count} জন ইউজারের কাছে পাঠানোর জন্য মেসেজ লিখুন:")
            return

        elif text == "👥 ইউজার ম্যানেজ":
            await msg.reply_text("👥 <b>ইউজার ম্যানেজমেন্ট:</b>", reply_markup=user_mgmt_menu(), parse_mode="HTML")
            return

        elif text == "🔍 ইউজার খুঁজুন":
            context.user_data['state'] = 'search_user'
            await msg.reply_text("🔍 ইউজার ID লিখুন:")
            return

        elif text == "💰 ব্যালেন্স সেট":
            context.user_data['state'] = 'man_bal'
            await msg.reply_text("💰 ফরম্যাট: <code>User_ID:Amount</code>", parse_mode="HTML")
            return

        elif text == "📤 ব্যালেন্স রিসেট":
            context.user_data['state'] = 'reset_bal'
            await msg.reply_text("📤 কোন ইউজারের ব্যালেন্স রিসেট করবেন? ID লিখুন:")
            return

        elif text == "🚫 ব্যান ম্যানেজ":
            context.user_data['state'] = 'search_user'
            await msg.reply_text("🔍 ব্যান/আনব্যান করতে ইউজার ID লিখুন:")
            return

        elif text == "👥 সকল ইউজার":
            conn   = get_conn()
            users  = conn.execute("SELECT user_id, full_name, balance, total_spent, is_banned FROM users ORDER BY balance DESC LIMIT 30").fetchall()
            conn.close()
            total_u = len(get_all_user_ids())
            msg_text = f"👥 <b>সকল ইউজার (টপ ৩০)</b> | মোট: {total_u}\n\n"
            for u in users:
                ban = "🚫" if u['is_banned'] else "✅"
                msg_text += f"{ban} <code>{u['user_id']}</code> | {CURRENCY}{u['balance']:.1f}\n"
            await msg.reply_text(msg_text, parse_mode="HTML")
            return

        elif text == "📊 এনালিটিক্স":
            conn   = get_conn()
            today  = datetime.now().strftime("%Y-%m-%d")
            week   = (datetime.now() - timedelta(days=7)).isoformat()

            total_users     = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()['c']
            active_today    = conn.execute("SELECT COUNT(DISTINCT user_id) as c FROM orders WHERE date LIKE ?", (f"{today}%",)).fetchone()['c']
            total_orders    = conn.execute("SELECT COUNT(*) as c FROM orders").fetchone()['c']
            total_revenue   = conn.execute("SELECT SUM(total) as s FROM orders").fetchone()['s'] or 0
            week_revenue    = conn.execute("SELECT SUM(total) as s FROM orders WHERE date >= ?", (week,)).fetchone()['s'] or 0
            pending_trx     = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE status='pending'").fetchone()['c']
            total_stock     = conn.execute("SELECT COUNT(*) as c FROM emails WHERE status='available'").fetchone()['c']
            sold_items      = conn.execute("SELECT COUNT(*) as c FROM emails WHERE status='sold'").fetchone()['c']
            conn.close()

            await msg.reply_text(
                f"📊 <b>শপ এনালিটিক্স</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"👥 মোট ইউজার: {total_users}\n"
                f"🟢 আজকের অর্ডার: {active_today} জন\n"
                f"🛒 মোট অর্ডার: {total_orders}\n"
                f"💰 মোট আয়: {CURRENCY}{total_revenue:.2f}\n"
                f"📈 এই সপ্তাহ: {CURRENCY}{week_revenue:.2f}\n"
                f"⏳ পেন্ডিং পেমেন্ট: {pending_trx}\n"
                f"📦 স্টকে আছে: {total_stock} টি\n"
                f"✅ বিক্রি হয়েছে: {sold_items} টি",
                parse_mode="HTML"
            )
            return

        elif text == "📋 অর্ডার হিস্টরি":
            conn   = get_conn()
            orders = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 20").fetchall()
            conn.close()
            if not orders:
                await msg.reply_text("📋 কোনো অর্ডার নেই।")
                return
            msg_text = "📋 <b>সর্বশেষ ২০টি অর্ডার:</b>\n\n"
            for o in orders:
                msg_text += (
                    f"🆔 #{o['id']} | 👤 <code>{o['user_id']}</code>\n"
                    f"📦 {o['product']} × {o['qty']} | {CURRENCY}{o['total']}\n"
                    f"📅 {o['date']}\n\n"
                )
            await msg.reply_text(msg_text, parse_mode="HTML")
            return

        elif text == "💹 ট্রানজেকশন লগ":
            conn = get_conn()
            trxs = conn.execute("SELECT * FROM transactions ORDER BY id DESC LIMIT 20").fetchall()
            conn.close()
            if not trxs:
                await msg.reply_text("💹 কোনো ট্রানজেকশন নেই।")
                return
            msg_text = "💹 <b>সর্বশেষ ২০টি ট্রানজেকশন:</b>\n\n"
            for t in trxs:
                status_emoji = {"approved": "✅", "pending": "⏳", "rejected": "❌"}.get(t['status'], "❓")
                msg_text += (
                    f"{status_emoji} <code>{t['user_id']}</code> | {CURRENCY}{t['amount']} | {t['method'] or 'N/A'}\n"
                    f"🔑 {t['trx_id'] or 'N/A'} | {format_date(t['created_at'])}\n\n"
                )
            await msg.reply_text(msg_text, parse_mode="HTML")
            return

        elif text == "📥 ইউজার অর্ডার ডাউনলোড":
            context.user_data['state'] = 'download_order_uid'
            await msg.reply_text(
                f"📥 <b>ইউজার অর্ডার ডাউনলোড</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"যে ইউজারের অর্ডার ডাউনলোড করতে চান\n"
                f"তার <b>User ID</b> লিখুন:\n\n"
                f"💡 User ID পেতে ইউজার ম্যানেজ → ইউজার খুঁজুন",
                parse_mode="HTML"
            )
            return

        # UPLOAD / DEL / PRICE / DESC prefix actions
        if text and text.startswith("UPLOAD:"):
            context.user_data['selected_p_type'] = text.split(":", 1)[1]
            context.user_data['state'] = 'waiting_file'
            await msg.reply_text(
                f"📂 <b>{context.user_data['selected_p_type']}</b> এর জন্য .txt ফাইল পাঠান।\n"
                f"প্রতি লাইনে একটি করে ডেটা।\n\n"
                f"⚠️ <b>গুরুত্বপূর্ণ:</b> ফাইলের প্রথম লাইন (হেডার) বাদ দেওয়া হবে।",
                parse_mode="HTML"
            )
            return

        elif text and text.startswith("DEL:"):
            name = text.split(":", 1)[1]
            conn = get_conn()
            conn.execute("DELETE FROM products WHERE name=?", (name,))
            conn.commit(); conn.close()
            await msg.reply_text(f"🗑 <b>{name}</b> ডিলিট হয়েছে।", reply_markup=product_mgmt_menu(), parse_mode="HTML")
            return

        elif text and text.startswith("PRICE:"):
            context.user_data['en']    = text.split(":", 1)[1]
            context.user_data['state'] = 'en_price'
            conn = get_conn()
            prod = conn.execute("SELECT price FROM products WHERE name=?", (context.user_data['en'],)).fetchone()
            conn.close()
            current = prod['price'] if prod else 'N/A'
            await msg.reply_text(f"💰 <b>{context.user_data['en']}</b>\nবর্তমান দাম: {CURRENCY}{current}\nনতুন দাম লিখুন:", parse_mode="HTML")
            return

        elif text and text.startswith("DESC:"):
            context.user_data['en']    = text.split(":", 1)[1]
            context.user_data['state'] = 'en_desc'
            await msg.reply_text(f"📝 <b>{context.user_data['en']}</b> এর নতুন বিবরণ লিখুন:", parse_mode="HTML")
            return

    # ════════════════════════════════════════════════
    #            USER ACTION HANDLERS
    # ════════════════════════════════════════════════

    # ── Buy qty ──
    if state == 'buy_qty' and text.isdigit():
        qty    = int(text)
        p_name = context.user_data.get('bn')
        conn   = get_conn()
        prod   = conn.execute("SELECT * FROM products WHERE name=?", (p_name,)).fetchone()
        if not prod:
            conn.close()
            await msg.reply_text("❌ পন্য পাওয়া যায়নি।")
            return

        min_ord = prod['min_order'] or 1
        if qty < min_ord:
            conn.close()
            await msg.reply_text(f"⚠️ ন্যূনতম অর্ডার {min_ord} পিস।")
            return

        total = qty * prod['price']

        user_data = conn.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
        bal  = user_data['balance'] if user_data else 0.0
        stk  = conn.execute(
            "SELECT COUNT(*) as c FROM emails WHERE status='available' AND product_type=?", (p_name,)
        ).fetchone()['c']

        if stk < qty:
            conn.close()
            await msg.reply_text(f"⚠️ পর্যাপ্ত স্টক নেই! স্টকে আছে: {stk} টি")
            return
        if bal < total:
            conn.close()
            await msg.reply_text(
                f"⚠️ <b>ব্যালেন্স কম!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🛒 পণ্য: {p_name} × {qty} পিস\n"
                f"💸 প্রয়োজন: {CURRENCY}{total:.2f}\n"
                f"💰 আপনার ব্যালেন্স: {CURRENCY}{bal:.2f}\n"
                f"📉 ঘাটতি: {CURRENCY}{total - bal:.2f}\n\n"
                f"💡 <b>💰 ব্যালেন্স রিচার্জ</b> বাটনে চাপুন এবং\n"
                f"বাকি {CURRENCY}{total - bal:.2f} রিচার্জ করুন।",
                reply_markup=main_menu(uid),
                parse_mode="HTML"
            )
            return

        # Process purchase
        items = conn.execute(
            "SELECT id, data FROM emails WHERE status='available' AND product_type=? LIMIT ?", (p_name, qty)
        ).fetchall()
        ids  = [i['id'] for i in items]
        now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        oid  = generate_order_id()

        conn.execute("UPDATE users SET balance=balance-?, total_spent=total_spent+? WHERE user_id=?", (total, total, uid))
        conn.execute(
            f"UPDATE emails SET status='sold', sold_to=?, sold_at=? WHERE id IN ({','.join(['?']*len(ids))})",
            [uid, now] + ids
        )
        conn.execute(
            "INSERT INTO orders (user_id, product, qty, total, date) VALUES (?,?,?,?,?)",
            (uid, p_name, qty, total, now)
        )
        conn.commit()

        # Build CSV
        out    = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['Data'])
        for i in items:
            writer.writerow([i['data']])
        out.seek(0)

        invoice = (
            f"🧾 <b>অর্ডার ইনভয়েস</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 অর্ডার ID: <code>#{oid}</code>\n"
            f"📅 তারিখ: {now}\n"
            f"👤 ক্রেতা ID: <code>{uid}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 পণ্য: <b>{p_name}</b>\n"
            f"🔢 পরিমাণ: {qty} পিস\n"
            f"💰 একক মূল্য: {CURRENCY}{prod['price']}\n"
            f"💸 মোট মূল্য: {CURRENCY}{total:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ স্ট্যাটাস: সফলভাবে ডেলিভারড\n"
            f"📁 ডেটা সংযুক্ত CSV ফাইলে দেওয়া হয়েছে।\n\n"
            f"🙏 কেনাকাটার জন্য ধন্যবাদ!"
        )
        await context.bot.send_document(
            chat_id=uid,
            document=io.BytesIO(out.getvalue().encode()),
            filename=f"Order_{oid}_{p_name}.csv",
            caption=invoice,
            parse_mode="HTML"
        )

        conn.close()
        await msg.reply_text(
            f"🎉 <b>অর্ডার সফল!</b>\n\n"
            f"✅ আপনার <b>{qty}টি {p_name}</b> ডেলিভার হয়েছে।\n"
            f"💰 বাকি ব্যালেন্স: {CURRENCY}{bal - total:.2f}\n\n"
            f"📁 উপরের CSV ফাইলটি সেভ করুন — এতে আপনার ডেটা আছে।\n"
            f"❓ কোনো সমস্যা হলে 📞 কাস্টমার সাপোর্টে যোগাযোগ করুন।",
            reply_markup=main_menu(uid),
            parse_mode="HTML"
        )
        context.user_data.clear()
        return

    # ════ USER MENU BUTTONS ════

    if text == "💰 ব্যালেন্স রিচার্জ":
        min_r = get_setting('min_recharge') or '10'
        context.user_data['state'] = 'd_amt'
        await msg.reply_text(
            f"💵 <b>ব্যালেন্স রিচার্জ</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 গ্রহণযোগ্য মাধ্যম: bKash | Nagad | Binance\n"
            f"💰 ন্যূনতম রিচার্জ: {CURRENCY}{min_r}\n\n"
            f"👇 কত টাকা রিচার্জ করতে চান লিখুন:",
            parse_mode="HTML"
        )
        return

    elif state == 'd_amt' and text.isdigit():
        amt   = int(text)
        min_r = int(get_setting('min_recharge') or '10')
        if amt < min_r:
            await msg.reply_text(f"❌ ন্যূনতম রিচার্জ {CURRENCY}{min_r}")
            return
        context.user_data['a']     = str(amt)
        context.user_data['state'] = None
        await msg.reply_text(
            f"💳 <b>পেমেন্ট মাধ্যম বেছে নিন ({CURRENCY}{amt})</b>",
            reply_markup=payment_method_keyboard(),
            parse_mode="HTML"
        )
        return

    elif text == "🛒 পন্য কিনুন":
        prods = get_products()
        if not prods:
            await msg.reply_text("❌ বর্তমানে কোনো পন্য নেই।")
            return
        kb = []
        for p in prods:
            stk = stock_count(p['name'])
            avail = f"({stk} টি)" if stk > 0 else "(শেষ!)"
            kb.append([f"BUY:{p['name']} — {CURRENCY}{p['price']} {avail}"])
        kb.append(["🔙 মেইন মেনু"])
        await msg.reply_text(
            f"🛍 <b>পণ্য তালিকা</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"নিচের পণ্যগুলো থেকে আপনার পছন্দেরটি বেছে নিন।\n"
            f"✅ = স্টকে আছে | ❌ = শেষ\n\n"
            f"👇 একটি পণ্যে ট্যাপ করুন:",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
            parse_mode="HTML"
        )
        return

    elif text and text.startswith("BUY:"):
        raw    = text.split("BUY:", 1)[1]
        p_name = raw.split(" — ")[0].strip()
        conn   = get_conn()
        prod   = conn.execute("SELECT * FROM products WHERE name=?", (p_name,)).fetchone()
        conn.close()
        if not prod:
            await msg.reply_text("❌ পন্য পাওয়া যায়নি।")
            return
        stk = stock_count(p_name)
        if stk == 0:
            await msg.reply_text("❌ এই পন্যটি স্টকে নেই।")
            return
        context.user_data['bn']    = p_name
        context.user_data['state'] = 'buy_qty'
        await msg.reply_text(
            f"📦 <b>{p_name}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 মূল্য: {CURRENCY}{prod['price']} / পিস\n"
            f"📝 বিবরণ: {prod['description'] or 'N/A'}\n"
            f"📊 স্টক: {stk} টি পাওয়া যাচ্ছে\n\n"
            f"🛒 <b>কত পিস কিনতে চান?</b>\n"
            f"শুধু সংখ্যা লিখে পাঠান (যেমন: 1, 5, 10)",
            parse_mode="HTML"
        )
        return

    elif text == "👤 আমার প্রোফাইল":
        conn   = get_conn()
        u      = conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        bal    = u['balance'] if u else 0.0
        spent  = u['total_spent'] if u else 0.0
        orders = conn.execute(
            "SELECT product, qty, total, date FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,)
        ).fetchall()
        trx_count = conn.execute(
            "SELECT COUNT(*) as c FROM transactions WHERE user_id=? AND status='approved'", (uid,)
        ).fetchone()['c']
        conn.close()

        history = ""
        for o in orders:
            history += f"  🔸 {o['date'][:10]} | {o['product']} × {o['qty']} | {CURRENCY}{o['total']:.2f}\n"

        await msg.reply_text(
            f"👤 <b>আমার প্রোফাইল</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🆔 আইডি: <code>{uid}</code>\n"
            f"👤 নাম: {user.full_name or 'N/A'}\n"
            f"📱 ইউজার: @{user.username or 'N/A'}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 ব্যালেন্স: {CURRENCY}{bal:.2f}\n"
            f"💸 মোট ব্যয়: {CURRENCY}{spent:.2f}\n"
            f"✅ পেমেন্ট: {trx_count}x\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📜 <b>সর্বশেষ অর্ডার:</b>\n"
            f"{history if history else '  কোনো অর্ডার নেই।'}",
            parse_mode="HTML"
        )
        return

    elif text == "📊 স্টক চেক":
        conn  = get_conn()
        items = conn.execute(
            "SELECT product_type, COUNT(*) as cnt FROM emails WHERE status='available' GROUP BY product_type"
        ).fetchall()
        conn.close()
        if not items:
            await msg.reply_text("📦 স্টক বর্তমানে খালি।")
            return
        msg_text = "📊 <b>স্টক স্ট্যাটাস</b>\n\n"
        for r in items:
            emoji = "✅" if r['cnt'] > 10 else "⚠️" if r['cnt'] > 0 else "❌"
            msg_text += f"{emoji} <b>{r['product_type']}</b>: {r['cnt']} টি\n"
        await msg.reply_text(msg_text, parse_mode="HTML")
        return

    elif text == "📞 কাস্টমার সাপোর্ট":
        await msg.reply_text(
            f"🎧 <b>কাস্টমার সাপোর্ট</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"যেকোনো সমস্যায় আমরা সাহায্য করতে প্রস্তুত!\n\n"
            f"👤 সাপোর্ট: {get_setting('support_user')}\n"
            f"🏪 শপ: {get_setting('shop_name')}\n\n"
            f"📋 <b>সাধারণ সমস্যা:</b>\n"
            f"  • পেমেন্ট অনুমোদন না হলে — TrxID পাঠান\n"
            f"  • পণ্য না পেলে — অর্ডার ID দিন\n"
            f"  • ব্যালেন্স সমস্যা — স্ক্রিনশট পাঠান\n\n"
            f"⏰ সাধারণত ৫-৩০ মিনিটের মধ্যে সাড়া দেওয়া হয়।",
            parse_mode="HTML"
        )
        return

    elif text in ["🔙 মেইন মেনু", "🔙 অ্যাডমিন প্যানেল"]:
        if text == "🔙 অ্যাডমিন প্যানেল" and is_admin(uid):
            await msg.reply_text("⚙️ অ্যাডমিন প্যানেলে ফিরলেন।", reply_markup=admin_menu())
        else:
            await msg.reply_text("🏠 মেইন মেনুতে ফিরলেন।", reply_markup=main_menu(uid))
        context.user_data.clear()
        return

    elif text in ["🔙 পন্য ম্যানেজ", "🔙 ইউজার ম্যানেজ"]:
        if text == "🔙 পন্য ম্যানেজ":
            await msg.reply_text("📦 পন্য ম্যানেজমেন্ট:", reply_markup=product_mgmt_menu())
        else:
            await msg.reply_text("👥 ইউজার ম্যানেজমেন্ট:", reply_markup=user_mgmt_menu())
        return

# ════════════════════════════════════════════════
#               /help COMMAND
# ════════════════════════════════════════════════
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_admin(uid):
        await update.message.reply_text(
            "🛠 <b>অ্যাডমিন কমান্ড গাইড</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "➕ <b>স্টক আপডেট</b> — .txt ফাইল আপলোড করে স্টক যোগ করুন\n"
            "📦 <b>পন্য ম্যানেজ</b> — নতুন পণ্য যোগ, দাম ও বিবরণ এডিট করুন\n"
            "💵 <b>ব্যালেন্স ম্যানেজ</b> — ইউজারের ব্যালেন্স যোগ করুন\n"
            "📊 <b>এনালিটিক্স</b> — বিক্রয় ও ইউজার রিপোর্ট দেখুন\n"
            "📢 <b>ব্রডকাস্ট</b> — সকল ইউজারকে মেসেজ পাঠান\n"
            "👥 <b>ইউজার ম্যানেজ</b> — ব্যান/আনব্যান ও প্রোফাইল দেখুন\n"
            "🔧 <b>বট সেটিংস</b> — পেমেন্ট নম্বর ও বট কনফিগার করুন\n"
            "📋 <b>অর্ডার হিস্টরি</b> — সর্বশেষ অর্ডারগুলো দেখুন\n"
            "💹 <b>ট্রানজেকশন লগ</b> — পেমেন্ট লগ চেক করুন\n"
            "📥 <b>ইউজার অর্ডার ডাউনলোড</b> — ইউজার ID দিয়ে তার ক্রয় করা ফাইল ডাউনলোড করুন",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "ℹ️ <b>ব্যবহার গাইড</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🛒 <b>পণ্য কিনুন</b> — ডিজিটাল পণ্যের তালিকা দেখুন ও কিনুন\n"
            "💰 <b>ব্যালেন্স রিচার্জ</b> — bKash/Nagad/Binance দিয়ে টাকা যোগ করুন\n"
            "👤 <b>আমার প্রোফাইল</b> — ব্যালেন্স ও অর্ডার ইতিহাস দেখুন\n"
            "📊 <b>স্টক চেক</b> — কোন পণ্য কতটা আছে দেখুন\n"
            "📞 <b>কাস্টমার সাপোর্ট</b> — সমস্যায় সাহায্য নিন\n\n"
            "💡 <b>টিপস:</b> পণ্য কেনার আগে ব্যালেন্স রিচার্জ করে নিন।",
            parse_mode="HTML"
        )

# ════════════════════════════════════════════════
#                  /balance COMMAND
# ════════════════════════════════════════════════
async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u   = get_user(uid)
    bal = u['balance'] if u else 0.0
    conn = get_conn()
    orders_count = conn.execute("SELECT COUNT(*) as c FROM orders WHERE user_id=?", (uid,)).fetchone()['c']
    conn.close()
    await update.message.reply_text(
        f"💰 <b>আমার ওয়ালেট</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 ব্যালেন্স: <b>{CURRENCY}{bal:.2f}</b>\n"
        f"🛒 মোট অর্ডার: {orders_count}টি\n\n"
        f"💡 রিচার্জ করতে মেনু থেকে <b>💰 ব্যালেন্স রিচার্জ</b> বাটনে চাপুন।",
        parse_mode="HTML"
    )

# ════════════════════════════════════════════════
#              /addadmin COMMAND (owner only)
# ════════════════════════════════════════════════
async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return
    if context.args:
        new_id = int(context.args[0])
        ADMIN_IDS.add(new_id)
        await update.message.reply_text(f"✅ <code>{new_id}</code> কে অ্যাডমিন করা হয়েছে।", parse_mode="HTML")

# ════════════════════════════════════════════════
#                   ERROR HANDLER
# ════════════════════════════════════════════════
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}", exc_info=True)

# ════════════════════════════════════════════════
#                     MAIN
# ════════════════════════════════════════════════
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("balance",  cmd_balance))
    app.add_handler(CommandHandler("addadmin", cmd_addadmin))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()