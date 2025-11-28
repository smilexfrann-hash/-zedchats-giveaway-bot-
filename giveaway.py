#!/usr/bin/env python3
# giveaway_final_v2.py
# Features: Smart Approve (@username/ID), Unapprove, Admin List, Secure
# Termux Compatible (python-telegram-bot v13.15)

import json
import logging
import os
import random
import threading
import time
import html
from datetime import datetime, timedelta

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ParseMode,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    Filters,
    CallbackContext,
)

# ---------------- CONFIG ----------------
BOT_TOKEN = "8136920419:AAFkTfZ74v6uMc2p0rk0Z-TIzB_nb_Cg92I"
OWNER_ID = 8167780741
DATA_FILE = "giveaway_data.json"
DEFAULT_BANNER = "https://i.ibb.co/7Wc3JXF/default-giveaway.jpg"
# ----------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

data_lock = threading.Lock()

# In-memory state
giveaways = {}
APPROVED_USERS = set()
AUTO_CHOOSE = True
BANNER_URL = None
known_groups = {}
wizards = {}
user_host_prefs = {}

# ------------- Helpers -------------

def format_time_remaining(ends_at):
    delta = ends_at - datetime.utcnow()
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0: return "Ended"
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return ", ".join(parts)

def is_allowed(uid):
    return uid == OWNER_ID or uid in APPROVED_USERS

def send_access_denied(update):
    try:
        update.message.reply_text("⛔ <b>ACCESS DENIED</b>\n\nThis bot is restricted to the <b>Owner & Admins</b> only.", parse_mode=ParseMode.HTML)
    except: pass

def resolve_target_id(bot, arg):
    """Helper to resolve ID from @username or numeric string"""
    arg = str(arg).strip()
    # If it's digits, return int
    if arg.lstrip("-").isdigit():
        return int(arg)
    # Try resolving username
    try:
        if not arg.startswith("@"): arg = "@" + arg
        chat = bot.get_chat(arg)
        return chat.id
    except Exception as e:
        logger.warning(f"Could not resolve user {arg}: {e}")
        return None

# ------------- Persistence -------------
def load_data():
    global giveaways, APPROVED_USERS, AUTO_CHOOSE, BANNER_URL, known_groups, user_host_prefs
    if os.path.exists(DATA_FILE):
        with data_lock:
            try:
                with open(DATA_FILE, "r") as f:
                    data = json.load(f)
                AUTO_CHOOSE = data.get("auto_choose", True)
                BANNER_URL = data.get("banner")
                APPROVED_USERS = set(data.get("approved_users", []))
                known_groups = {int(k): v for k, v in data.get("known_groups", {}).items()}
                user_host_prefs = {int(k): v for k, v in data.get("user_host_prefs", {}).items()}
                
                raw = data.get("giveaways", {})
                giveaways.clear()
                for gid, g in raw.items():
                    giveaways[gid] = {
                        "chat_id": g["chat_id"],
                        "message_id": g["message_id"],
                        "title": g["title"],
                        "prize": g["prize"],
                        "conditions": g.get("conditions", "None"),
                        "creator_id": g["creator_id"],
                        "winners_count": g["winners_count"],
                        "min_entries": g["min_entries"],
                        "ends_at": datetime.fromisoformat(g["ends_at"]),
                        "participants": set(g.get("participants", [])),
                        "ended": g.get("ended", False),
                        "waiting_manual": g.get("waiting_manual", False),
                        "host": g.get("host"),
                    }
            except Exception as e:
                logger.warning("Data load error: %s", e)

def save_data():
    with data_lock:
        serial = {
            "auto_choose": AUTO_CHOOSE,
            "banner": BANNER_URL,
            "approved_users": list(APPROVED_USERS),
            "known_groups": {str(k): v for k, v in known_groups.items()},
            "user_host_prefs": {str(k): v for k, v in user_host_prefs.items()},
            "giveaways": {}
        }
        for gid, g in giveaways.items():
            serial["giveaways"][gid] = {
                "chat_id": g["chat_id"],
                "message_id": g["message_id"],
                "title": g["title"],
                "prize": g["prize"],
                "conditions": g.get("conditions", "None"),
                "creator_id": g["creator_id"],
                "winners_count": g["winners_count"],
                "min_entries": g["min_entries"],
                "ends_at": g["ends_at"].isoformat(),
                "participants": list(g["participants"]),
                "ended": g.get("ended", False),
                "waiting_manual": g.get("waiting_manual", False),
                "host": g.get("host"),
            }
        try:
            with open(DATA_FILE, "w") as f:
                json.dump(serial, f, indent=2)
        except Exception as e:
            logger.warning("Save error: %s", e)

# ------------- Display Logic -------------

def build_participate_keyboard(gid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎟 Participate!", callback_data=f"join|{gid}")]
    ])

def update_give_message(bot, gid):
    with data_lock:
        g = giveaways.get(gid)
        if not g: return
        prize = html.escape(g['prize'])
        host = g.get('host', 'Unknown') 
        conditions = g.get('conditions', 'None')
        count = len(g['participants'])
        winners = g['winners_count']
        ends_at = g['ends_at']
        chat_id = g['chat_id']
        msg_id = g['message_id']

    time_str = format_time_remaining(ends_at)
    
    text = (
        f"<b>🎉 NEW GIVEAWAY!</b>\n\n"
        f"💰 <b>Prize:</b> {prize}\n"
        f"👑 <b>Hosted By:</b> {host}\n\n"
        f"📜 <b>Conditions:</b>\n{conditions}\n\n"
        f"👥 <b>Entries:</b> {count}\n"
        f"🥇 <b>Winners:</b> {winners}\n"
        f"⏰ <b>Ends In:</b> {time_str}\n\n"
        f"To participate press below."
    )
    
    try:
        bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=text, reply_markup=build_participate_keyboard(gid), parse_mode=ParseMode.HTML)
    except:
        try: bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=build_participate_keyboard(gid), parse_mode=ParseMode.HTML)
        except: pass

def announce_winners(bot, gid, winners_list, is_reroll=False):
    with data_lock:
        g = giveaways.get(gid)
    if not g: return

    winners_text = ""
    for uid in winners_list:
        try:
            user = bot.get_chat(uid)
            safe_name = html.escape(user.first_name)
            winners_text += f"<a href='tg://user?id={uid}'>{safe_name}</a> "
        except:
            winners_text += f"user_{uid} "
            
    title_text = "🔁 <b>REROLL RESULT!</b>" if is_reroll else "🎉 <b>GIVEAWAY ENDED!</b>"
    
    caption = (
        f"{title_text}\n\n"
        f"💰 <b>Prize:</b> {html.escape(g['prize'])}\n\n"
        f"👑 <b>Hosted By:</b> {g.get('host')}\n\n"
        f"🏆 <b>Winner(s):</b> {winners_text}"
    )
    
    try:
        photo_to_send = BANNER_URL or DEFAULT_BANNER
        msg = bot.send_photo(g["chat_id"], photo=photo_to_send, caption=caption, parse_mode=ParseMode.HTML)
        if not is_reroll:
            try: bot.pin_chat_message(g["chat_id"], msg.message_id)
            except: pass
    except Exception as e:
        logger.warning("Announce failed: %s", e)

def perform_end_logic(bot, gid):
    with data_lock:
        g = giveaways.get(gid)
        if not g: return
        g["ended"] = True
        g["waiting_manual"] = False
    
    save_data()
    
    participants = list(g["participants"])
    if len(participants) < g["min_entries"]:
        try:
            bot.send_message(g["chat_id"], f"❌ Giveaway cancelled. Not enough entries.")
        except: pass
        return

    winners_count = min(g["winners_count"], len(participants))
    winners_list = random.sample(participants, winners_count)
    announce_winners(bot, gid, winners_list)

# ------------- Background Worker -------------
def auto_end_worker(bot):
    last_update_check = datetime.utcnow()
    while True:
        now = datetime.utcnow()
        to_process = []
        to_update = []
        with data_lock:
            for gid, g in giveaways.items():
                if not g.get("ended", False):
                    if g["ends_at"] <= now: to_process.append(gid)
                    else: to_update.append(gid)
        
        for gid in to_process:
            if AUTO_CHOOSE:
                try: perform_end_logic(bot, gid)
                except Exception as e: logger.error(e)
            else:
                with data_lock:
                    g = giveaways.get(gid)
                    if g and not g.get("waiting_manual", False):
                        g["ended"] = True
                        g["waiting_manual"] = True
                        chat_id = g["chat_id"]
                        save_data()
                        try: bot.send_message(chat_id, "⏰ <b>TIME IS UP!</b>\n\nWaiting for host to pick winner.\nUse <code>/roll</code>.", parse_mode=ParseMode.HTML)
                        except: pass
        
        if (now - last_update_check).total_seconds() >= 60:
            for gid in to_update:
                try:
                    update_give_message(bot, gid)
                    time.sleep(0.5) 
                except: pass
            last_update_check = now
        time.sleep(10)

# ------------- Commands -------------

def cmd_help(update, context: CallbackContext):
    if not is_allowed(update.effective_user.id):
        return send_access_denied(update)

    help_text = (
        "🤖 <b>GIVEAWAY BOT COMMANDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🚀 <b>Start & Host</b>\n"
        "• <code>/host</code> : Start Giveaway Wizard (in DM)\n\n"
        "🎮 <b>Manage (In Group)</b>\n"
        "• <code>/cancel</code> : Cancel active event (Secure)\n"
        "• <code>/sethost Name</code> : Set Host Name\n"
        "• <code>/reroll</code> : Pick new winner\n"
        "• <code>/roll</code> : Pick winner manually\n"
        "• <code>/autochoose [on/off]</code> : Toggle auto-ending\n\n"
        "👮‍♂️ <b>Owner & Admins</b>\n"
        "• <code>/approve @user</code> : Add Admin\n"
        "• <code>/unapprove @user</code> : Remove Admin\n"
        "• <code>/adminlist</code> : See Admins\n"
        "• <code>/my_groups</code> : Check connected groups\n\n"
        "🎨 <b>Customization</b>\n"
        "• <code>/setbanner</code> : Reply to photo to set banner\n\n"
    )
    update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

def cmd_sethost(update, context: CallbackContext):
    uid = update.effective_user.id
    if not is_allowed(uid): return send_access_denied(update)
    
    chat_id = update.effective_chat.id
    if not context.args: return update.message.reply_text("⚠️ Usage: <code>/sethost @MyChannel</code>", parse_mode=ParseMode.HTML)
    
    new_host = " ".join(context.args)
    active_gid = None
    
    with data_lock:
        for gid, g in giveaways.items():
            if g["chat_id"] == chat_id and not g.get("ended", False):
                active_gid = gid; break
    
    if active_gid:
        with data_lock: giveaways[active_gid]["host"] = new_host
        save_data()
        update_give_message(context.bot, active_gid)
        update.message.reply_text(f"✅ <b>Updated Active Giveaway!</b>\nNew Host: {html.escape(new_host)}", parse_mode=ParseMode.HTML)
    else:
        with data_lock: user_host_prefs[uid] = new_host
        save_data()
        update.message.reply_text(f"✅ <b>Default Host Name Saved!</b>\nHost: {html.escape(new_host)}", parse_mode=ParseMode.HTML)

def cmd_autochoose(update, context: CallbackContext):
    if not is_allowed(update.effective_user.id): return send_access_denied(update)
    global AUTO_CHOOSE
    if not context.args: return update.message.reply_text(f"ℹ️ Autochoose: <b>{'ON' if AUTO_CHOOSE else 'OFF'}</b>", parse_mode=ParseMode.HTML)
    mode = context.args[0].lower()
    if mode == "on": AUTO_CHOOSE = True; save_data(); update.message.reply_text("✅ Autochoose ON.")
    elif mode == "off": AUTO_CHOOSE = False; save_data(); update.message.reply_text("✅ Autochoose OFF.")

def cmd_roll(update, context: CallbackContext):
    if not is_allowed(update.effective_user.id): return send_access_denied(update)
    chat_id = update.effective_chat.id
    target_gid = None
    with data_lock:
        for gid, g in giveaways.items():
            if g["chat_id"] == chat_id and g.get("waiting_manual", False): target_gid = gid; break
        if not target_gid:
            for gid, g in giveaways.items():
                if g["chat_id"] == chat_id and not g.get("ended", False): target_gid = gid; break
    if not target_gid: return update.message.reply_text("⚠️ Nothing to roll.")
    update.message.reply_text("🎲 Rolling...")
    perform_end_logic(context.bot, target_gid)

def cmd_host(update, context: CallbackContext):
    uid = update.effective_user.id
    if not is_allowed(uid): return send_access_denied(update)
    current_pref = user_host_prefs.get(uid, update.effective_user.username or "Admin")
    wizards[uid] = {"step": 1, "host": current_pref}
    update.message.reply_text(f"✏️ <b>New Giveaway</b>\nHost: <b>{html.escape(current_pref)}</b>\n\nSend the <b>Title</b>.", parse_mode=ParseMode.HTML)

def cmd_cancel(update, context: CallbackContext):
    if not is_allowed(update.effective_user.id): return send_access_denied(update)
    chat_id = update.effective_chat.id
    target_gid = None
    with data_lock:
        for gid, g in giveaways.items():
            if g["chat_id"] == chat_id and not g.get("ended", False): target_gid = gid; break
    if not target_gid: return update.message.reply_text("⚠️ No active giveaway.")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Yes, Cancel", callback_data=f"confirm_cancel|{target_gid}")], [InlineKeyboardButton("❌ No, Back", callback_data="cancel_no")]])
    update.message.reply_text("⚠️ <b>CONFIRM CANCEL?</b>", reply_markup=keyboard, parse_mode=ParseMode.HTML)

def cmd_reroll(update, context: CallbackContext):
    if not is_allowed(update.effective_user.id): return send_access_denied(update)
    chat_id = update.effective_chat.id
    target_gid = None
    with data_lock:
        for gid, g in giveaways.items():
            if g["chat_id"] == chat_id and g.get("ended", False) and not g.get("waiting_manual", False):
                if not target_gid or gid > target_gid: target_gid = gid
    if not target_gid: return update.message.reply_text("⚠️ No ended giveaway found.")
    g = giveaways[target_gid]
    participants = list(g["participants"])
    if not participants: return update.message.reply_text("❌ No participants.")
    winner = [random.choice(participants)]
    announce_winners(context.bot, target_gid, winner, is_reroll=True)
    update.message.reply_text("✅ Reroll complete!")

def cmd_my_groups(update, context: CallbackContext):
    if not is_allowed(update.effective_user.id): return send_access_denied(update)
    text = "Known groups:\n" + "\n".join([f"{v} ({k})" for k,v in known_groups.items()])
    update.message.reply_text(text)

def cmd_setbanner(update, context: CallbackContext):
    if not is_allowed(update.effective_user.id): return send_access_denied(update)
    global BANNER_URL
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        BANNER_URL = update.message.reply_to_message.photo[-1].file_id
        save_data()
        update.message.reply_text("✅ Banner updated.")
    elif context.args:
        BANNER_URL = context.args[0]
        save_data()
        update.message.reply_text("✅ Banner URL updated.")

# --- IMPROVED APPROVE COMMANDS ---
def cmd_approve(update, context: CallbackContext):
    if update.effective_user.id != OWNER_ID: return send_access_denied(update)
    
    if not context.args:
        return update.message.reply_text("⚠️ Usage: <code>/approve @username</code> or <code>/approve 123456</code>", parse_mode=ParseMode.HTML)
    
    target_arg = context.args[0]
    target_id = resolve_target_id(context.bot, target_arg)
    
    if target_id:
        APPROVED_USERS.add(target_id)
        save_data()
        update.message.reply_text(f"✅ <b>Approved:</b> <code>{target_id}</code>\nThey can now host giveaways.", parse_mode=ParseMode.HTML)
    else:
        update.message.reply_text("❌ Could not find user. Make sure they have a username or use their numeric ID.")

def cmd_unapprove(update, context: CallbackContext):
    if update.effective_user.id != OWNER_ID: return send_access_denied(update)
    
    if not context.args:
        return update.message.reply_text("⚠️ Usage: <code>/unapprove @username</code> or <code>/unapprove ID</code>", parse_mode=ParseMode.HTML)
    
    target_arg = context.args[0]
    target_id = resolve_target_id(context.bot, target_arg)
    
    if target_id and target_id in APPROVED_USERS:
        APPROVED_USERS.discard(target_id)
        save_data()
        update.message.reply_text(f"🚫 <b>Removed Admin:</b> <code>{target_id}</code>", parse_mode=ParseMode.HTML)
    else:
        update.message.reply_text("❌ User not found in admin list or invalid input.")

def cmd_adminlist(update, context: CallbackContext):
    if update.effective_user.id != OWNER_ID: return send_access_denied(update)
    
    if not APPROVED_USERS:
        return update.message.reply_text("ℹ️ No approved admins yet.")
    
    text = "<b>👮‍♂️ Approved Admins:</b>\n"
    for uid in APPROVED_USERS:
        text += f"• <code>{uid}</code>\n"
    update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ------------- Callback & Message Handlers -------------
def callback_handler(update, context: CallbackContext):
    query = update.callback_query
    bot = context.bot
    data = query.data
    uid = query.from_user.id

    if data.startswith("confirm_cancel|") or data.startswith("wizard_select|") or data.split("|")[0] == "end":
        if not is_allowed(uid):
            return query.answer("⛔ Access Denied", show_alert=True)

    if data.startswith("confirm_cancel|"):
        _, gid = data.split("|")
        with data_lock:
            g = giveaways.get(gid)
            if not g: return query.answer("Gone.")
            g["ended"] = True
            msg_id, chat_id = g["message_id"], g["chat_id"]
        save_data()
        try:
            bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption="❌ <b>CANCELLED</b>", parse_mode=ParseMode.HTML)
            query.message.delete()
        except: pass
        return query.answer("Cancelled.")

    if data == "cancel_no":
        try: query.message.delete()
        except: pass
        return query.answer("Back.")

    if data.startswith("wizard_select|"):
        _, uid_s, chat_id_s = data.split("|")
        uid_sel, chat_id = int(uid_s), int(chat_id_s)
        if uid != uid_sel: return query.answer("Not yours!", show_alert=True)
        wiz = wizards.get(uid)
        if not wiz: return query.answer("Expired")
        
        gid = str(int(time.time() * 1000))
        ends_at = datetime.utcnow() + timedelta(minutes=wiz["duration"])
        text = (f"<b>🎉 NEW GIVEAWAY!</b>\n\n💰 <b>Prize:</b> {html.escape(wiz['prize'])}\n👑 <b>Hosted By:</b> {wiz['host']}\n\n"
                f"📜 <b>Conditions:</b>\n{wiz['conditions']}\n\n👥 <b>Entries:</b> 0\n🥇 <b>Winners:</b> {wiz['winners']}\n"
                f"⏰ <b>Ends In:</b> {format_time_remaining(ends_at)}\n\nTo participate press below.")
        try:
            if BANNER_URL:
                msg = bot.send_photo(chat_id, BANNER_URL, caption=text, parse_mode=ParseMode.HTML, reply_markup=build_participate_keyboard(gid))
            else:
                msg = bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=build_participate_keyboard(gid))
            try: bot.pin_chat_message(chat_id, msg.message_id)
            except: pass
        except: return query.answer("Failed. Is bot Admin?")
        
        with data_lock:
            giveaways[gid] = {
                "chat_id": chat_id, "message_id": msg.message_id, "title": wiz["title"], "prize": wiz["prize"],
                "conditions": wiz["conditions"], "creator_id": uid, "winners_count": wiz["winners"], "min_entries": wiz["min_entries"],
                "ends_at": ends_at, "participants": set(), "ended": False, "waiting_manual": False, "host": wiz["host"]
            }
        save_data()
        wizards.pop(uid, None)
        return query.answer("Live!")

    if "|" not in data: return
    action, gid = data.split("|", 1)
    with data_lock: g = giveaways.get(gid)
    if not g or g.get("ended", False): return query.answer("Ended.", show_alert=True)
    
    if action == "join":
        if uid in g["participants"]: return query.answer("Already in!", show_alert=True)
        g["participants"].add(uid)
        save_data()
        update_give_message(bot, gid)
        return query.answer("Joined!")
    elif action == "end":
        perform_end_logic(bot, gid)

def message_handler(update, context: CallbackContext):
    msg = update.message
    if not msg: return
    
    if msg.chat.type in ("group", "supergroup"):
        with data_lock: known_groups[msg.chat.id] = msg.chat.title or str(msg.chat.id)
        save_data()
        return

    if msg.chat.type == "private":
        uid, text = msg.from_user.id, msg.text or ""
        
        if not is_allowed(uid):
            return send_access_denied(update)

        wiz = wizards.get(uid)
        if wiz:
            step = wiz["step"]
            if step == 1: wiz.update({"title": text, "step": 2}); msg.reply_text("Title set. Send <b>Prize</b>.", parse_mode=ParseMode.HTML)
            elif step == 2: wiz.update({"prize": text, "step": 3}); msg.reply_text("Prize set. Send <b>Conditions</b> (or 'None').", parse_mode=ParseMode.HTML)
            elif step == 3: wiz.update({"conditions": text, "step": 4}); msg.reply_text("Conditions set. Send <b>Duration</b> (min).", parse_mode=ParseMode.HTML)
            elif step == 4: 
                try: wiz.update({"duration": int(text), "step": 5}); msg.reply_text("Duration set. Send <b>Winners</b> count.", parse_mode=ParseMode.HTML)
                except: msg.reply_text("Number please.")
            elif step == 5: 
                try: wiz.update({"winners": int(text), "step": 6}); msg.reply_text("Winners set. Send <b>Min Entries</b>.", parse_mode=ParseMode.HTML)
                except: msg.reply_text("Number please.")
            elif step == 6:
                try: 
                    wiz["min_entries"] = int(text)
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=f"wizard_select|{uid}|{c}")] for c,t in known_groups.items()])
                    if not known_groups: return msg.reply_text("No groups found.")
                    msg.reply_text("Done! Select group:", reply_markup=kb)
                except: msg.reply_text("Number please.")
            return
        
        if text.startswith("/"): return 
        msg.reply_text("Use /host to start.")

def main():
    load_data()
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    for cmd, func in [("host", cmd_host), ("my_groups", cmd_my_groups), ("setbanner", cmd_setbanner),
                      ("approve", cmd_approve), ("unapprove", cmd_unapprove), ("adminlist", cmd_adminlist),
                      ("cancel", cmd_cancel), ("reroll", cmd_reroll),
                      ("autochoose", cmd_autochoose), ("roll", cmd_roll), ("help", cmd_help), 
                      ("start", cmd_help), ("sethost", cmd_sethost)]:
        dp.add_handler(CommandHandler(cmd, func))
    dp.add_handler(CallbackQueryHandler(callback_handler))
    dp.add_handler(MessageHandler(Filters.text | Filters.command, message_handler))
    threading.Thread(target=auto_end_worker, args=(updater.bot,), daemon=True).start()
    logger.info("Bot Started (Approved v2)...")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
