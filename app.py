# -*- coding: utf-8 -*-
import os
import sys
import json
import asyncio
import csv
import socket
import subprocess
import threading
import time
import re
from datetime import datetime, timezone
from aiohttp import web

from telethon import TelegramClient, errors, types
from telethon.tl.types import (
    Channel, Chat, User,
    UserStatusOnline, UserStatusOffline,
    UserStatusRecently, UserStatusLastWeek, UserStatusLastMonth,
)
from telethon.tl.functions.messages import ImportChatInviteRequest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
DEFAULT_SESSION = os.path.join(BASE_DIR, "tele_session")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
INDEX_HTML = os.path.join(BASE_DIR, "web", "index.html")

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

PORT = int(os.environ.get("PORT", 5001))

DEFAULT_API_ID = 35113375
DEFAULT_API_HASH = "a952d49f01385827b34e454a412f4eb3"

_client = None
_current_session_path = DEFAULT_SESSION
_pending_login_client = None
_pending_phone = None
_pending_phone_code_hash = None

_scrape_state = {
    "is_running": False,
    "group": "",
    "scanned": 0,
    "matched": 0,
    "members": [],
    "error": None,
    "done": False
}

_last_heartbeat = time.time()
_public_url = None

def get_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def format_phone(phone_input):
    digits = re.sub(r'\D', '', str(phone_input))
    if digits.startswith('0'):
        return '+84' + digits[1:]
    elif digits.startswith('84'):
        return '+' + digits
    return '+' + digits

def get_active_session_path():
    config = get_config()
    session_name = config.get("active_session")
    if session_name:
        if os.path.exists(os.path.join(SESSIONS_DIR, f"{session_name}.session")):
            return os.path.join(SESSIONS_DIR, session_name)
        elif os.path.exists(os.path.join(BASE_DIR, f"{session_name}.session")):
            return os.path.join(BASE_DIR, session_name)
    return DEFAULT_SESSION

def get_telegram_client():
    global _client, _current_session_path
    session_path = get_active_session_path()

    if _client is not None and _current_session_path == session_path:
        return _client

    if _client is not None:
        try:
            if _client.is_connected():
                asyncio.create_task(_client.disconnect())
        except Exception:
            pass
        _client = None

    config = get_config()
    api_id = config.get("api_id") or DEFAULT_API_ID
    api_hash = config.get("api_hash") or DEFAULT_API_HASH

    _current_session_path = session_path
    _client = TelegramClient(session_path, api_id, api_hash)
    return _client

async def check_auth():
    client = get_telegram_client()
    if not client:
        return {"connected": False, "reason": "Chưa có API trong config"}

    if not client.is_connected():
        await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()

        config = get_config()
        accounts = config.get("accounts", [])
        current_phone = format_phone(me.phone) if me.phone else config.get("phone", "")
        current_session = config.get("active_session", "tele_session")

        found = False
        for acc in accounts:
            if acc.get("session_name") == current_session or acc.get("phone") == current_phone:
                acc["name"] = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Người dùng"
                acc["username"] = me.username or ""
                acc["phone"] = current_phone
                acc["session_name"] = current_session
                found = True
                break
        if not found:
            accounts.append({
                "name": f"{me.first_name or ''} {me.last_name or ''}".strip() or "Người dùng",
                "username": me.username or "",
                "phone": current_phone,
                "session_name": current_session
            })
        config["accounts"] = accounts
        config["active_session"] = current_session
        save_config(config)

        return {
            "connected": True,
            "id": me.id,
            "first_name": me.first_name,
            "username": me.username,
            "phone": current_phone
        }
    return {"connected": False, "reason": "Tài khoản chưa đăng nhập"}

def get_accounts_list():
    config = get_config()
    accounts = config.get("accounts", [])
    active_session = config.get("active_session", "tele_session")
    return {"accounts": accounts, "active_session": active_session}

async def switch_account(session_name):
    global _client
    config = get_config()
    config["active_session"] = session_name
    save_config(config)

    if _client and _client.is_connected():
        await _client.disconnect()
    _client = None

    client = get_telegram_client()
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        return {"success": True, "name": me.first_name, "phone": me.phone}
    return {"success": False, "reason": "Tài khoản này chưa đăng nhập hợp lệ"}

async def start_login_request(phone_number):
    global _pending_login_client, _pending_phone, _pending_phone_code_hash
    config = get_config()
    api_id = config.get("api_id") or DEFAULT_API_ID
    api_hash = config.get("api_hash") or DEFAULT_API_HASH

    clean_phone = format_phone(phone_number)
    digits = re.sub(r'\D', '', clean_phone)
    new_session_path = os.path.join(SESSIONS_DIR, f"session_{digits}")

    if _pending_login_client:
        try:
            if _pending_login_client.is_connected():
                await _pending_login_client.disconnect()
        except Exception:
            pass
        _pending_login_client = None

    _pending_phone = clean_phone
    _pending_login_client = TelegramClient(new_session_path, api_id, api_hash)
    await _pending_login_client.connect()

    sent = await _pending_login_client.send_code_request(clean_phone)
    _pending_phone_code_hash = sent.phone_code_hash

    return {"status": "code_sent", "phone": clean_phone}

async def complete_login_request(code, password=None):
    global _pending_login_client, _pending_phone, _pending_phone_code_hash, _client
    if not _pending_login_client:
        raise Exception("Không tìm thấy phiên đăng nhập đang chờ. Vui lòng bấm gửi mã lại!")

    clean_phone = _pending_phone
    try:
        await _pending_login_client.sign_in(clean_phone, code=code, phone_code_hash=_pending_phone_code_hash)
    except errors.SessionPasswordNeededError:
        if not password:
            return {"status": "password_needed", "message": "Tài khoản có bảo mật 2 bước (2FA). Vui lòng nhập mật khẩu 2FA!"}
        await _pending_login_client.sign_in(password=password)

    me = await _pending_login_client.get_me()
    digits = re.sub(r'\D', '', clean_phone)
    session_name = f"session_{digits}"

    config = get_config()
    accounts = config.get("accounts", [])

    exists = False
    for a in accounts:
        if a.get("phone") == clean_phone or a.get("session_name") == session_name:
            a["name"] = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Người dùng"
            a["username"] = me.username or ""
            a["phone"] = clean_phone
            a["session_name"] = session_name
            exists = True
            break
    if not exists:
        accounts.append({
            "name": f"{me.first_name or ''} {me.last_name or ''}".strip() or "Người dùng",
            "username": me.username or "",
            "phone": clean_phone,
            "session_name": session_name
        })

    config["accounts"] = accounts
    config["active_session"] = session_name
    save_config(config)

    if _client and _client.is_connected():
        await _client.disconnect()
    _client = _pending_login_client
    _pending_login_client = None

    return {
        "status": "success",
        "name": me.first_name,
        "phone": clean_phone,
        "session_name": session_name
    }

async def delete_saved_account(session_name):
    config = get_config()
    accounts = config.get("accounts", [])
    accounts = [a for a in accounts if a.get("session_name") != session_name]
    config["accounts"] = accounts

    if config.get("active_session") == session_name:
        config["active_session"] = accounts[0]["session_name"] if accounts else "tele_session"

    save_config(config)

    sess_file = os.path.join(SESSIONS_DIR, f"{session_name}.session")
    if os.path.exists(sess_file):
        try:
            os.remove(sess_file)
        except Exception:
            pass

    return {"status": "deleted"}

async def get_my_groups():
    client = get_telegram_client()
    if not client or not await client.is_user_authorized():
        return []

    groups = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if dialog.is_group or (isinstance(entity, Channel) and (entity.megagroup or not entity.broadcast)):
            count = getattr(entity, 'participants_count', 0)
            groups.append({
                "id": str(dialog.id),
                "title": dialog.title,
                "username": getattr(entity, 'username', '') or '',
                "participants_count": count
            })
    return groups

def check_member_status(user, filter_days=None, only_online=False):
    status = user.status
    if not status:
        return (False if (only_online or filter_days is not None) else True), "Không xác định"

    if isinstance(status, UserStatusOnline):
        return True, "Đang Online"

    if only_online:
        return False, "Offline"

    if filter_days is None:
        if isinstance(status, UserStatusOffline):
            return True, f"Offline ({status.was_online.strftime('%d/%m/%Y %H:%M')})"
        elif isinstance(status, UserStatusRecently):
            return True, "Gần đây (1-3 ngày)"
        elif isinstance(status, UserStatusLastWeek):
            return True, "Tuần trước"
        elif isinstance(status, UserStatusLastMonth):
            return True, "Tháng trước"
        return True, "Offline lâu"

    now = datetime.now(timezone.utc)
    if isinstance(status, UserStatusOffline):
        if status.was_online:
            diff_days = (now - status.was_online).total_seconds() / 86400.0
            if diff_days <= filter_days:
                return True, f"Offline ({status.was_online.strftime('%d/%m/%Y %H:%M')})"
        return False, "Quá số ngày"
    elif isinstance(status, UserStatusRecently):
        return (filter_days >= 1), "Gần đây (1-3 ngày)"
    elif isinstance(status, UserStatusLastWeek):
        return (filter_days >= 7), "Tuần trước"
    elif isinstance(status, UserStatusLastMonth):
        return (filter_days >= 30), "Tháng trước"

    return False, "Ẩn/Offline lâu"

VIETNAMESE_CHARS = set('àáâãèéêìíòóôõùúýăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝĂĐƠƯẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỴỶỸ')

def _has_vietnamese(name):
    return any(c in VIETNAMESE_CHARS for c in name)

async def scrape_group_members(source_group, filter_days=None, only_online=False, name_filter=None, scrape_mode="members", msg_limit=1000, progress_callback=None):
    client = get_telegram_client()
    if not client or not await client.is_user_authorized():
        raise Exception("Telegram chưa đăng nhập")

    source_group = source_group.strip()
    entity = None

    if "t.me/+" in source_group or "t.me/joinchat/" in source_group:
        invite_hash = source_group.split("+")[-1].split("joinchat/")[-1].strip()
        try:
            await client(ImportChatInviteRequest(invite_hash))
        except errors.UserAlreadyParticipantError:
            pass
        except Exception:
            pass

    try:
        if source_group.isdigit() or (source_group.startswith("-") and source_group[1:].isdigit()):
            entity = await client.get_entity(int(source_group))
        else:
            entity = await client.get_entity(source_group)
    except Exception:
        pass

    if not entity:
        search_kw = source_group.strip().lower()
        async for dialog in client.iter_dialogs():
            if dialog.is_group or (isinstance(dialog.entity, Channel) and (dialog.entity.megagroup or not dialog.entity.broadcast)):
                d_title = dialog.title.strip().lower()
                if d_title == search_kw or search_kw in d_title or str(dialog.id) == source_group:
                    entity = dialog.entity
                    break

    if not entity:
        raise Exception(f"Không tìm thấy nhóm '{source_group}'. Hãy đảm bảo tài khoản đã tham gia nhóm này!")

    members = []
    seen_ids = set()
    count_scanned = 0

    if scrape_mode == "messages":
        user_msg_counts = {}
        users_map = {}

        try:
            async for msg in client.iter_messages(entity, limit=msg_limit or 1000):
                count_scanned += 1
                sender = msg.sender
                if not sender and msg.sender_id:
                    try:
                        sender = await client.get_entity(msg.sender_id)
                    except Exception:
                        continue

                if not sender or not isinstance(sender, User):
                    continue
                if sender.bot or sender.deleted:
                    continue

                uid = sender.id
                user_msg_counts[uid] = user_msg_counts.get(uid, 0) + 1
                if uid not in users_map:
                    users_map[uid] = sender

                if progress_callback and count_scanned % 25 == 0:
                    await progress_callback(len(users_map), count_scanned)
        except Exception as e:
            raise Exception(f"Lỗi khi quét tin nhắn trong nhóm: {str(e)}")

        seen_uids = set()
        for uid, user in users_map.items():
            if uid in seen_uids:
                continue
            seen_uids.add(uid)

            is_valid, status_str = check_member_status(user, filter_days=filter_days, only_online=only_online)
            if not is_valid:
                continue

            first = user.first_name or ""
            last = user.last_name or ""
            full_name = f"{first} {last}".strip() or "Người dùng ẩn danh"
            cnt = user_msg_counts.get(uid, 1)

            members.append({
                "id": user.id,
                "full_name": full_name,
                "username": user.username or "",
                "phone": user.phone or "",
                "status": f"{status_str} (Đã chat {cnt} tin)",
                "profile_link": f"tg://user?id={user.id}"
            })

        members.sort(key=lambda x: user_msg_counts.get(x["id"], 0), reverse=True)
    else:
        try:
            async for user in client.iter_participants(entity, aggressive=True):
                count_scanned += 1
                if user.bot or user.deleted:
                    continue
                if user.id in seen_ids:
                    continue
                seen_ids.add(user.id)

                is_valid, status_str = check_member_status(user, filter_days=filter_days, only_online=only_online)
                if not is_valid:
                    continue

                first = user.first_name or ""
                last = user.last_name or ""
                full_name = f"{first} {last}".strip() or "Người dùng ẩn danh"

                members.append({
                    "id": user.id,
                    "full_name": full_name,
                    "username": user.username or "",
                    "phone": user.phone or "",
                    "status": status_str,
                    "profile_link": f"tg://user?id={user.id}"
                })

                if progress_callback and len(members) % 25 == 0:
                    await progress_callback(len(members), count_scanned)
        except errors.ChatAdminRequiredError:
            raise Exception("Nhóm này đã BẬT CHẾ ĐỘ ẨN THÀNH VIÊN! Vui lòng chọn 'Lọc qua tin nhắn' để quét!")
        except Exception as e:
            err_msg = str(e).lower()
            if "admin" in err_msg or "participant" in err_msg or "privilege" in err_msg:
                raise Exception(f"Nhóm này có thể đã ẩn danh sách thành viên. Hãy chọn 'Lọc qua tin nhắn'! (Chi tiết: {e})")
            raise e

    if progress_callback:
        await progress_callback(len(members), count_scanned)

    if name_filter == 'vietnamese':
        members = [m for m in members if _has_vietnamese(m.get('full_name', ''))]
    elif name_filter == 'english':
        members = [m for m in members if re.search(r'[a-zA-Z]', m.get('full_name', '')) and not _has_vietnamese(m.get('full_name', ''))]

    return members


EMBEDDED_HTML = '<!DOCTYPE html>\n<html lang="vi" class="dark">\n<head>\n    <meta charset="UTF-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">\n    <title>TeleScraper Mobile - Cào & Lọc Thành Viên</title>\n    <!-- Tailwind CSS CDN -->\n    <script src="https://cdn.tailwindcss.com"></script>\n    <script>\n        tailwind.config = {\n            darkMode: \'class\',\n            theme: {\n                extend: {\n                    colors: {\n                        darkbg: \'#0a0b14\',\n                        darkcard: \'#121424\',\n                        neonpurple: \'#a855f7\',\n                        neonblue: \'#06b6d4\',\n                        borderglass: \'rgba(255, 255, 255, 0.08)\'\n                    }\n                }\n            }\n        }\n    </script>\n    <!-- Lucide Icons -->\n    <script src="https://unpkg.com/lucide@latest"></script>\n    <style>\n        body {\n            background: #0a0b14;\n            color: #e2e8f0;\n            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;\n            -webkit-tap-highlight-color: transparent;\n        }\n        .glass-card {\n            background: rgba(18, 20, 36, 0.85);\n            backdrop-filter: blur(12px);\n            -webkit-backdrop-filter: blur(12px);\n        }\n        .neon-glow-purple {\n            box-shadow: 0 0 20px rgba(168, 85, 247, 0.35);\n        }\n        .neon-glow-blue {\n            box-shadow: 0 0 20px rgba(6, 182, 212, 0.35);\n        }\n        /* Custom scrollbar */\n        ::-webkit-scrollbar {\n            width: 5px;\n            height: 5px;\n        }\n        ::-webkit-scrollbar-thumb {\n            background: rgba(168, 85, 247, 0.3);\n            border-radius: 4px;\n        }\n        ::-webkit-scrollbar-track {\n            background: transparent;\n        }\n    </style>\n</head>\n<body class="min-h-screen flex flex-col pb-10">\n\n    <!-- Toast Container -->\n    <div id="toast-container" class="fixed top-4 right-4 left-4 sm:left-auto sm:w-80 z-50 flex flex-col gap-2 pointer-events-none"></div>\n\n    <!-- Header / Navbar -->\n    <header class="sticky top-0 z-30 glass-card border-b border-borderglass px-4 py-3">\n        <div class="max-w-4xl mx-auto flex items-center justify-between">\n            <div class="flex items-center gap-2.5">\n                <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-neonpurple to-neonblue flex items-center justify-center neon-glow-purple text-white">\n                    <i data-lucide="users" class="w-5 h-5"></i>\n                </div>\n                <div>\n                    <h1 class="font-bold text-sm sm:text-base text-transparent bg-clip-text bg-gradient-to-r from-neonpurple to-neonblue">\n                        TELE SCRAPER\n                    </h1>\n                    <div class="flex items-center gap-1.5 text-[11px] text-gray-400">\n                        <span id="tele-status-dot" class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>\n                        <span id="tele-header-account" class="truncate max-w-[150px] sm:max-w-xs font-medium">Đang kiểm tra...</span>\n                    </div>\n                </div>\n            </div>\n\n            <!-- Quick Account Switch / Manage Button -->\n            <button onclick="openAccountsModal()" class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-neonpurple/20 hover:bg-neonpurple/30 text-purple-300 border border-neonpurple/30 text-xs font-semibold transition-all">\n                <i data-lucide="user-cog" class="w-4 h-4"></i>\n                <span class="hidden sm:inline">Tài khoản</span>\n            </button>\n        </div>\n    </header>\n\n    <!-- Main Container -->\n    <main class="max-w-4xl w-full mx-auto p-4 sm:p-6 space-y-5 flex-1">\n\n        <!-- Mobile Link Card / Banner -->\n        <div id="tunnel-banner" class="hidden p-3.5 bg-gradient-to-r from-neonpurple/20 to-neonblue/20 border border-neonpurple/40 rounded-2xl flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-lg">\n            <div class="flex items-center gap-2.5">\n                <div class="w-8 h-8 rounded-xl bg-neonpurple/30 flex items-center justify-center text-purple-300">\n                    <i data-lucide="smartphone" class="w-4 h-4"></i>\n                </div>\n                <div>\n                    <div class="text-[11px] text-gray-300 font-medium">Link mở trực tiếp trên điện thoại (không cần cài gì):</div>\n                    <a id="tunnel-link-href" href="#" target="_blank" class="text-xs font-mono font-bold text-emerald-400 hover:underline break-all">Đang nạp link...</a>\n                </div>\n            </div>\n            <button onclick="copyMobileLink()" id="btn-copy-mobile-link" class="px-3 py-1.5 rounded-xl bg-neonpurple/30 hover:bg-neonpurple/50 text-white text-xs font-semibold flex items-center justify-center gap-1.5 whitespace-nowrap">\n                <i data-lucide="copy" class="w-3.5 h-3.5"></i> Sao chép link\n            </button>\n        </div>\n\n        <!-- Form thiết lập cào thành viên -->\n        <div class="glass-card p-4 sm:p-5 rounded-2xl border border-borderglass space-y-4 shadow-xl">\n            <div class="flex items-center justify-between pb-2 border-b border-borderglass">\n                <div class="flex items-center gap-2">\n                    <i data-lucide="filter" class="w-4 h-4 text-neonpurple"></i>\n                    <h2 class="text-sm sm:text-base font-bold text-white">Cào & Lọc Thành Viên</h2>\n                </div>\n                <button onclick="loadMyGroups()" title="Tải lại danh sách nhóm" class="p-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-gray-400 text-xs flex items-center gap-1">\n                    <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>\n                </button>\n            </div>\n\n            <!-- Chọn nhanh nhóm đã tham gia -->\n            <div class="p-3 bg-neonpurple/10 border border-neonpurple/30 rounded-xl space-y-1.5">\n                <label class="block text-xs font-semibold text-purple-200 flex items-center gap-1.5">\n                    <i data-lucide="sparkles" class="w-3.5 h-3.5 text-neonpurple"></i>\n                    Chọn nhanh nhóm đã tham gia:\n                </label>\n                <select id="select-my-groups" onchange="onSelectMyGroup(this)" class="w-full bg-black/60 border border-neonpurple/40 rounded-lg px-3 py-2 text-xs sm:text-sm text-purple-100 focus:outline-none focus:border-neonpurple">\n                    <option value="">-- Đang nạp danh sách nhóm của bạn... --</option>\n                </select>\n            </div>\n\n            <!-- Ô nhập nhóm nguồn -->\n            <div>\n                <label class="block text-xs font-semibold text-gray-300 mb-1">Hoặc nhập nhóm nguồn (Tên nhóm, ID hoặc link):</label>\n                <input type="text" id="scrape-source-group" placeholder="ví dụ: @tennhom, link t.me/..., hoặc ID" class="w-full bg-black/40 border border-borderglass rounded-xl px-3.5 py-2.5 text-xs sm:text-sm text-white focus:outline-none focus:border-neonpurple">\n            </div>\n\n            <!-- Cấu hình lọc: Grid 2 cột trên điện thoại -->\n            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">\n                <div>\n                    <label class="block text-xs font-semibold text-gray-300 mb-1">Phương thức cào:</label>\n                    <select id="scrape-mode" onchange="onScrapeModeChange(this.value)" class="w-full bg-black/40 border border-borderglass rounded-xl px-3 py-2.5 text-xs sm:text-sm text-white focus:outline-none focus:border-neonpurple">\n                        <option value="members">Lọc qua danh sách thành viên</option>\n                        <option value="messages">Lọc qua tin nhắn</option>\n                    </select>\n                </div>\n\n                <div id="scrape-msg-limit-box" class="hidden">\n                    <label class="block text-xs font-semibold text-amber-300 mb-1">Số tin nhắn quét:</label>\n                    <select id="scrape-msg-limit" class="w-full bg-black/40 border border-amber-500/40 rounded-xl px-3 py-2.5 text-xs sm:text-sm text-amber-200 focus:outline-none focus:border-amber-400 font-medium">\n                        <option value="500">500 tin nhắn</option>\n                        <option value="1000" selected>1.000 tin nhắn</option>\n                        <option value="2000">2.000 tin nhắn</option>\n                        <option value="5000">5.000 tin nhắn</option>\n                        <option value="10000">10.000 tin nhắn</option>\n                    </select>\n                </div>\n\n                <div>\n                    <label class="block text-xs font-semibold text-gray-300 mb-1">Thời gian hoạt động (Online):</label>\n                    <select id="scrape-filter-days" class="w-full bg-black/40 border border-borderglass rounded-xl px-3 py-2.5 text-xs sm:text-sm text-white focus:outline-none focus:border-neonpurple">\n                        <option value="">Tất cả thành viên</option>\n                        <option value="online">Chỉ người ĐANG ONLINE ngay bây giờ</option>\n                        <option value="1">Online trong 24 giờ qua</option>\n                        <option value="3">Online trong 3 ngày qua</option>\n                        <option value="7">Online trong 7 ngày qua</option>\n                        <option value="30">Online trong 30 ngày qua</option>\n                    </select>\n                </div>\n\n                <div>\n                    <label class="block text-xs font-semibold text-gray-300 mb-1">Lọc theo ngôn ngữ tên:</label>\n                    <select id="scrape-name-filter" class="w-full bg-black/40 border border-borderglass rounded-xl px-3 py-2.5 text-xs sm:text-sm text-white focus:outline-none focus:border-neonpurple">\n                        <option value="">Tất cả tên</option>\n                        <option value="vietnamese">Chỉ tên Tiếng Việt</option>\n                        <option value="english">Chỉ tên Tiếng Anh</option>\n                    </select>\n                </div>\n            </div>\n\n            <!-- Nút Bắt đầu cào -->\n            <div class="pt-2">\n                <button onclick="startScraping()" id="btn-start-scrape" class="w-full py-3 px-4 rounded-xl bg-gradient-to-r from-neonpurple to-neonblue text-white font-bold text-sm neon-glow-purple hover:opacity-95 transition-all flex items-center justify-center gap-2">\n                    <i data-lucide="play" class="w-4 h-4"></i>\n                    <span>Bắt đầu cào thành viên</span>\n                </button>\n                <div id="scrape-spinner" class="hidden mt-3 flex items-center justify-center gap-2 text-xs sm:text-sm text-neonpurple font-medium">\n                    <i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i>\n                    <span>Đang quét... (<strong id="scrape-counter" class="text-white">0</strong> thành viên)</span>\n                </div>\n            </div>\n        </div>\n\n        <!-- Bảng & Thẻ danh sách kết quả -->\n        <div class="glass-card p-4 sm:p-5 rounded-2xl border border-borderglass space-y-4 shadow-xl">\n            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">\n                <h3 class="text-sm sm:text-base font-bold text-white flex items-center gap-2">\n                    <i data-lucide="list" class="w-4 h-4 text-neonblue"></i>\n                    Kết quả (<span id="members-count" class="text-neonpurple">0</span> thành viên)\n                </h3>\n                <div class="flex flex-wrap items-center gap-2">\n                    <button onclick="exportScraped(\'csv\')" class="flex-1 sm:flex-none px-3 py-2 rounded-xl bg-emerald-600/25 hover:bg-emerald-600/40 text-emerald-300 border border-emerald-500/30 text-xs font-semibold flex items-center justify-center gap-1.5 transition-all">\n                        <i data-lucide="file-spreadsheet" class="w-3.5 h-3.5"></i> Xuất CSV\n                    </button>\n                    <button onclick="exportScraped(\'txt\')" class="flex-1 sm:flex-none px-3 py-2 rounded-xl bg-blue-600/25 hover:bg-blue-600/40 text-blue-300 border border-blue-500/30 text-xs font-semibold flex items-center justify-center gap-1.5 transition-all">\n                        <i data-lucide="file-text" class="w-3.5 h-3.5"></i> Xuất TXT\n                    </button>\n                    <button onclick="openSendTgModal()" class="w-full sm:w-auto px-3.5 py-2 rounded-xl bg-purple-600/30 hover:bg-purple-600/45 text-purple-200 border border-purple-500/35 text-xs font-semibold flex items-center justify-center gap-1.5 transition-all">\n                        <i data-lucide="send" class="w-3.5 h-3.5"></i> Gửi vào Telegram\n                    </button>\n                </div>\n            </div>\n\n            <!-- Danh sách thành viên (Mobile: Dạng thẻ; Desktop: Dạng bảng) -->\n            <div id="members-list-mobile" class="sm:hidden space-y-2 max-h-[500px] overflow-y-auto pr-1">\n                <div class="text-center py-10 text-gray-500 italic text-xs">Chưa có dữ liệu thành viên. Chọn nhóm và bấm Bắt đầu cào.</div>\n            </div>\n\n            <div class="hidden sm:block overflow-x-auto max-h-[500px] overflow-y-auto">\n                <table class="w-full text-left text-xs text-gray-300">\n                    <thead class="sticky top-0 bg-[#0d0f1a] text-gray-400 uppercase text-[10px] border-b border-borderglass">\n                        <tr>\n                            <th class="py-2.5 px-3">Tên hiển thị (Link Profile)</th>\n                            <th class="py-2.5 px-3">Username</th>\n                            <th class="py-2.5 px-3">User ID</th>\n                            <th class="py-2.5 px-3">Trạng thái</th>\n                        </tr>\n                    </thead>\n                    <tbody id="members-table-body" class="divide-y divide-borderglass">\n                        <tr>\n                            <td colspan="4" class="py-8 text-center text-gray-500 italic">Chưa có dữ liệu thành viên.</td>\n                        </tr>\n                    </tbody>\n                </table>\n            </div>\n        </div>\n\n    </main>\n\n    <!-- MODAL 1: GỬI THÀNH VIÊN VÀO TELEGRAM -->\n    <div id="modal-send-tg" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">\n        <div class="glass-card p-5 sm:p-6 rounded-2xl border border-borderglass max-w-md w-full space-y-4">\n            <div class="flex items-center justify-between border-b border-borderglass pb-2">\n                <h3 class="text-sm sm:text-base font-bold text-white flex items-center gap-2">\n                    <i data-lucide="send" class="w-4 h-4 text-neonblue"></i>\n                    Gửi thành viên vào Telegram\n                </h3>\n                <button onclick="closeSendTgModal()" class="text-gray-400 hover:text-white">\n                    <i data-lucide="x" class="w-4 h-4"></i>\n                </button>\n            </div>\n            <p class="text-xs text-gray-300 leading-relaxed">\n                Danh sách sẽ được gửi chia thành từng mẻ 50 người / tin nhắn (gắn link mở thẳng trang cá nhân).\n            </p>\n            <div>\n                <label class="block text-xs font-semibold text-gray-300 mb-1">Nơi nhận tin nhắn:</label>\n                <input type="text" id="send-tg-target" placeholder="Để trống hoặc gõ \'me\' để gửi tin nhắn đã lưu" class="w-full bg-black/40 border border-borderglass rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-neonpurple">\n            </div>\n            <div class="flex items-center justify-end gap-2 pt-2">\n                <button onclick="closeSendTgModal()" class="px-4 py-2 rounded-xl bg-white/10 text-xs text-gray-300 hover:bg-white/15">Hủy</button>\n                <button onclick="confirmSendTg()" id="btn-confirm-send" class="px-4 py-2 rounded-xl bg-gradient-to-r from-neonpurple to-neonblue text-white text-xs font-bold neon-glow-purple">Bắt đầu gửi</button>\n            </div>\n        </div>\n    </div>\n\n    <!-- MODAL 2: QUẢN LÝ TÀI KHOẢN TELEGRAM -->\n    <div id="modal-accounts" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">\n        <div class="glass-card p-5 sm:p-6 rounded-2xl border border-borderglass max-w-md w-full space-y-4">\n            <div class="flex items-center justify-between border-b border-borderglass pb-3">\n                <div class="flex items-center gap-2">\n                    <i data-lucide="users" class="w-5 h-5 text-neonpurple"></i>\n                    <h3 class="text-sm sm:text-base font-bold text-white">Quản lý Tài Khoản</h3>\n                </div>\n                <button onclick="closeAccountsModal()" class="text-gray-400 hover:text-white">\n                    <i data-lucide="x" class="w-5 h-5"></i>\n                </button>\n            </div>\n\n            <!-- Tab chọn chế độ: Danh sách đã lưu / Đăng nhập tài khoản mới -->\n            <div class="flex border-b border-borderglass text-xs">\n                <button onclick="switchAccountTab(\'list\')" id="btn-acc-tab-list" class="flex-1 py-2 font-semibold text-neonpurple border-b-2 border-neonpurple">\n                    Đã lưu (<span id="acc-saved-count">0</span>)\n                </button>\n                <button onclick="switchAccountTab(\'new\')" id="btn-acc-tab-new" class="flex-1 py-2 font-semibold text-gray-400 hover:text-white">\n                    + Thêm tài khoản mới\n                </button>\n            </div>\n\n            <!-- Danh sách tài khoản đã lưu -->\n            <div id="acc-tab-content-list" class="space-y-2.5 max-h-72 overflow-y-auto pr-1">\n                <div id="accounts-list-container" class="space-y-2">\n                    <div class="text-center py-6 text-gray-500 italic text-xs">Đang tải danh sách tài khoản...</div>\n                </div>\n            </div>\n\n            <!-- Đăng nhập tài khoản mới -->\n            <div id="acc-tab-content-new" class="hidden space-y-3.5">\n                <div>\n                    <label class="block text-xs font-semibold text-gray-300 mb-1">Số điện thoại Telegram:</label>\n                    <div class="flex gap-2">\n                        <input type="text" id="new-acc-phone" placeholder="ví dụ: 0339111172" class="flex-1 bg-black/40 border border-borderglass rounded-xl px-3 py-2 text-xs sm:text-sm text-white focus:outline-none focus:border-neonpurple">\n                        <button onclick="sendLoginCode()" id="btn-send-code" class="px-3 py-2 rounded-xl bg-neonpurple/30 hover:bg-neonpurple/50 text-white text-xs font-semibold whitespace-nowrap">\n                            Gửi OTP\n                        </button>\n                    </div>\n                </div>\n\n                <div id="otp-input-group" class="hidden space-y-3 border-t border-borderglass pt-3">\n                    <div>\n                        <label class="block text-xs font-semibold text-gray-300 mb-1">Mã xác thực Telegram (OTP):</label>\n                        <input type="text" id="new-acc-otp" placeholder="ví dụ: 12345" class="w-full bg-black/40 border border-borderglass rounded-xl px-3 py-2 text-xs sm:text-sm text-white focus:outline-none focus:border-neonpurple">\n                    </div>\n\n                    <div>\n                        <label class="block text-xs font-semibold text-gray-300 mb-1">Mật khẩu 2 bước (2FA nếu có):</label>\n                        <input type="password" id="new-acc-pass" placeholder="Để trống nếu không có 2FA" class="w-full bg-black/40 border border-borderglass rounded-xl px-3 py-2 text-xs sm:text-sm text-white focus:outline-none focus:border-neonpurple">\n                    </div>\n\n                    <button onclick="verifyLoginCode()" id="btn-verify-code" class="w-full py-2.5 rounded-xl bg-gradient-to-r from-neonpurple to-neonblue text-white text-xs font-bold neon-glow-purple">\n                        Xác nhận đăng nhập & Lưu\n                    </button>\n                </div>\n            </div>\n\n            <div class="flex justify-end pt-2 border-t border-borderglass">\n                <button onclick="closeAccountsModal()" class="px-4 py-2 rounded-xl bg-white/10 text-xs text-gray-300 hover:bg-white/15">Đóng</button>\n            </div>\n        </div>\n    </div>\n\n    <!-- JAVASCRIPT LOGIC -->\n    <script>\n        lucide.createIcons();\n\n        function escapeHtml(text) {\n            if (!text) return \'\';\n            const map = { \'&\': \'&amp;\', \'<\': \'&lt;\', \'>\': \'&gt;\', \'"\': \'&quot;\', "\'": \'&#039;\' };\n            return String(text).replace(/[&<>"\']/g, m => map[m]);\n        }\n\n        function showToast(message, type = \'info\') {\n            const container = document.getElementById(\'toast-container\');\n            const toast = document.createElement(\'div\');\n            const colors = {\n                success: \'bg-emerald-950/95 border-emerald-500/50 text-emerald-200\',\n                error: \'bg-red-950/95 border-red-500/50 text-red-200\',\n                info: \'bg-purple-950/95 border-neonpurple/50 text-purple-200\'\n            };\n            toast.className = `p-3 rounded-xl border ${colors[type] || colors.info} text-xs shadow-xl backdrop-blur-md transition-all duration-300 pointer-events-auto flex items-center justify-between gap-2`;\n            toast.innerHTML = `<span>${message}</span>`;\n            container.appendChild(toast);\n            setTimeout(() => {\n                toast.classList.add(\'opacity-0\', \'translate-y-[-10px]\');\n                setTimeout(() => toast.remove(), 300);\n            }, 3500);\n        }\n\n        async function checkConnection() {\n            try {\n                const res = await fetch(\'/api/status\');\n                const data = await res.json();\n                const headerText = document.getElementById(\'tele-header-account\');\n                const dot = document.getElementById(\'tele-status-dot\');\n\n                if (data.connected) {\n                    headerText.innerText = `${data.first_name || \'Người dùng\'} (${data.phone || \'\'})`;\n                    dot.className = \'w-2 h-2 rounded-full bg-emerald-500 animate-pulse\';\n                } else {\n                    headerText.innerText = \'Chưa đăng nhập\';\n                    dot.className = \'w-2 h-2 rounded-full bg-red-500\';\n                    if (!window._hasPromptedLogin) {\n                        window._hasPromptedLogin = true;\n                        setTimeout(() => {\n                            openAccountsModal();\n                            switchAccountTab(\'new\');\n                        }, 500);\n                    }\n                }\n            } catch (err) {\n                console.error(err);\n            }\n        }\n\n        async function loadMyGroups() {\n            const select = document.getElementById(\'select-my-groups\');\n            select.innerHTML = \'<option value="">-- Đang tải danh sách nhóm... --</option>\';\n            try {\n                const res = await fetch(\'/api/my_groups\');\n                const data = await res.json();\n                if (!data.groups || data.groups.length === 0) {\n                    select.innerHTML = \'<option value="">(Không tìm thấy nhóm nào)</option>\';\n                    return;\n                }\n                select.innerHTML = \'<option value="">-- Bấm vào đây để chọn nhóm đã tham gia --</option>\' + \n                    data.groups.map(g => `\n                        <option value="${g.id}">${escapeHtml(g.title)} (${g.participants_count ? g.participants_count + \' thành viên\' : \'Nhóm\'})</option>\n                    `).join(\'\');\n            } catch (err) {\n                select.innerHTML = \'<option value="">(Lỗi tải danh sách nhóm)</option>\';\n                console.error(err);\n            }\n        }\n\n        function onSelectMyGroup(el) {\n            if (el.value) {\n                document.getElementById(\'scrape-source-group\').value = el.value;\n                const text = el.options[el.selectedIndex].text;\n                showToast(`Đã chọn: ${text}`, \'info\');\n            }\n        }\n\n        function onScrapeModeChange(mode) {\n            const box = document.getElementById(\'scrape-msg-limit-box\');\n            if (box) {\n                if (mode === \'messages\') {\n                    box.classList.remove(\'hidden\');\n                } else {\n                    box.classList.add(\'hidden\');\n                }\n            }\n        }\n\n        let scrapeInterval = null;\n\n        async function startScraping() {\n            const group = document.getElementById(\'scrape-source-group\').value.trim();\n            const filterDays = document.getElementById(\'scrape-filter-days\').value;\n            if (!group) return showToast(\'Vui lòng nhập nhóm cần cào!\', \'error\');\n\n            const payload = { source_group: group };\n            if (filterDays === \'online\') {\n                payload.only_online = true;\n            } else if (filterDays) {\n                payload.filter_days = parseFloat(filterDays);\n            }\n\n            const nameFilt = document.getElementById(\'scrape-name-filter\').value;\n            if (nameFilt) payload.name_filter = nameFilt;\n\n            const scrapeMode = document.getElementById(\'scrape-mode\') ? document.getElementById(\'scrape-mode\').value : \'members\';\n            payload.scrape_mode = scrapeMode;\n            if (scrapeMode === \'messages\') {\n                const limit = document.getElementById(\'scrape-msg-limit\') ? document.getElementById(\'scrape-msg-limit\').value : \'1000\';\n                payload.msg_limit = parseInt(limit, 10) || 1000;\n            }\n\n            try {\n                const res = await fetch(\'/api/scrape/start\', {\n                    method: \'POST\',\n                    headers: { \'Content-Type\': \'application/json\' },\n                    body: JSON.stringify(payload)\n                });\n                const data = await res.json();\n                if (data.error) return showToast(data.error, \'error\');\n\n                showToast(\'Đang tiến hành cào thành viên...\', \'info\');\n                document.getElementById(\'scrape-spinner\').classList.remove(\'hidden\');\n                document.getElementById(\'btn-start-scrape\').disabled = true;\n\n                scrapeInterval = setInterval(pollScrapeStatus, 1500);\n            } catch (e) {\n                showToast(e.message, \'error\');\n            }\n        }\n\n        async function pollScrapeStatus() {\n            try {\n                const res = await fetch(\'/api/scrape/status\');\n                const state = await res.json();\n                document.getElementById(\'scrape-counter\').innerText = state.matched;\n                document.getElementById(\'members-count\').innerText = state.matched;\n\n                if (state.members && state.members.length > 0) {\n                    renderMembers(state.members);\n                }\n\n                if (state.done || !state.is_running) {\n                    clearInterval(scrapeInterval);\n                    document.getElementById(\'scrape-spinner\').classList.add(\'hidden\');\n                    document.getElementById(\'btn-start-scrape\').disabled = false;\n                    if (state.error) showToast(\'Lỗi: \' + state.error, \'error\');\n                    else showToast(`Đã cào hoàn tất ${state.matched} thành viên!`, \'success\');\n                }\n            } catch (err) {\n                console.error(err);\n            }\n        }\n\n        function renderMembers(members) {\n            // Render mobile cards\n            const mobileContainer = document.getElementById(\'members-list-mobile\');\n            mobileContainer.innerHTML = members.slice(0, 150).map(m => `\n                <div class="p-3 rounded-xl border border-borderglass bg-black/30 space-y-1">\n                    <div class="flex items-center justify-between">\n                        <a href="${m.profile_link}" target="_blank" class="font-bold text-xs text-neonblue hover:underline flex items-center gap-1 truncate max-w-[200px]">\n                            ${escapeHtml(m.full_name)}\n                        </a>\n                        <span class="px-2 py-0.5 rounded text-[10px] ${m.status.includes(\'Online\') ? \'bg-emerald-500/20 text-emerald-400\' : \'bg-white/5 text-gray-400\'}">\n                            ${m.status}\n                        </span>\n                    </div>\n                    <div class="flex items-center justify-between text-[11px] text-gray-400 font-mono">\n                        <span>${m.username ? \'@\' + m.username : \'---\'}</span>\n                        <span class="text-gray-500">ID: ${m.id}</span>\n                    </div>\n                </div>\n            `).join(\'\');\n\n            // Render desktop table\n            const tbody = document.getElementById(\'members-table-body\');\n            tbody.innerHTML = members.slice(0, 150).map(m => `\n                <tr class="hover:bg-white/5 transition-colors">\n                    <td class="py-2 px-3 font-medium text-neonblue">\n                        <a href="${m.profile_link}" target="_blank" class="hover:underline flex items-center gap-1">\n                            ${escapeHtml(m.full_name)}\n                        </a>\n                    </td>\n                    <td class="py-2 px-3 text-gray-400 font-mono">${m.username ? \'@\' + m.username : \'---\'}</td>\n                    <td class="py-2 px-3 text-gray-500 font-mono">${m.id}</td>\n                    <td class="py-2 px-3">\n                        <span class="px-2 py-0.5 rounded text-[10px] ${m.status.includes(\'Online\') ? \'bg-emerald-500/20 text-emerald-400\' : \'bg-white/5 text-gray-400\'}">\n                            ${m.status}\n                        </span>\n                    </td>\n                </tr>\n            `).join(\'\');\n        }\n\n        function exportScraped(format) {\n            window.location.href = `/api/scrape/export/${format}`;\n        }\n\n        function openSendTgModal() {\n            document.getElementById(\'modal-send-tg\').classList.remove(\'hidden\');\n        }\n        function closeSendTgModal() {\n            document.getElementById(\'modal-send-tg\').classList.add(\'hidden\');\n        }\n\n        async function confirmSendTg() {\n            const target = document.getElementById(\'send-tg-target\').value.trim();\n            const btn = document.getElementById(\'btn-confirm-send\');\n            btn.disabled = true;\n            btn.innerText = \'Đang gửi...\';\n\n            try {\n                const res = await fetch(\'/api/scrape/send_tg\', {\n                    method: \'POST\',\n                    headers: { \'Content-Type\': \'application/json\' },\n                    body: JSON.stringify({ target_chat: target || \'me\' })\n                });\n                const data = await res.json();\n                if (data.error) showToast(data.error, \'error\');\n                else {\n                    showToast(`Đã gửi thành công ${data.total_sent} thành viên vào Telegram!`, \'success\');\n                    closeSendTgModal();\n                }\n            } catch (e) {\n                showToast(e.message, \'error\');\n            } finally {\n                btn.disabled = false;\n                btn.innerText = \'Bắt đầu gửi\';\n            }\n        }\n\n        // --- QUẢN LÝ ĐA TÀI KHOẢN ---\n        function openAccountsModal() {\n            document.getElementById(\'modal-accounts\').classList.remove(\'hidden\');\n            loadAccountsList();\n        }\n\n        function closeAccountsModal() {\n            document.getElementById(\'modal-accounts\').classList.add(\'hidden\');\n        }\n\n        function switchAccountTab(tab) {\n            const listBtn = document.getElementById(\'btn-acc-tab-list\');\n            const newBtn = document.getElementById(\'btn-acc-tab-new\');\n            const listContent = document.getElementById(\'acc-tab-content-list\');\n            const newContent = document.getElementById(\'acc-tab-content-new\');\n\n            if (tab === \'list\') {\n                listBtn.className = \'flex-1 py-2 font-semibold text-neonpurple border-b-2 border-neonpurple\';\n                newBtn.className = \'flex-1 py-2 font-semibold text-gray-400 hover:text-white\';\n                listContent.classList.remove(\'hidden\');\n                newContent.classList.add(\'hidden\');\n                loadAccountsList();\n            } else {\n                newBtn.className = \'flex-1 py-2 font-semibold text-neonpurple border-b-2 border-neonpurple\';\n                listBtn.className = \'flex-1 py-2 font-semibold text-gray-400 hover:text-white\';\n                newContent.classList.remove(\'hidden\');\n                listContent.classList.add(\'hidden\');\n            }\n        }\n\n        async function loadAccountsList() {\n            try {\n                const res = await fetch(\'/api/accounts\');\n                const data = await res.json();\n                const container = document.getElementById(\'accounts-list-container\');\n                const countBadge = document.getElementById(\'acc-saved-count\');\n                countBadge.innerText = (data.accounts || []).length;\n\n                if (!data.accounts || data.accounts.length === 0) {\n                    container.innerHTML = \'<div class="text-center py-6 text-gray-500 italic text-xs">Chưa có tài khoản nào. Bấm sang tab Thêm mới.</div>\';\n                    return;\n                }\n\n                container.innerHTML = data.accounts.map(acc => {\n                    const isActive = acc.session_name === data.active_session;\n                    return `\n                        <div class="p-3 rounded-xl border ${isActive ? \'border-neonpurple/50 bg-neonpurple/10\' : \'border-borderglass bg-black/30\'} flex items-center justify-between">\n                            <div class="flex items-center gap-2.5">\n                                <div class="w-8 h-8 rounded-full ${isActive ? \'bg-neonpurple\' : \'bg-white/10\'} flex items-center justify-center font-bold text-xs text-white">\n                                    ${(acc.name || \'TG\').charAt(0).toUpperCase()}\n                                </div>\n                                <div>\n                                    <div class="font-semibold text-xs text-white flex items-center gap-1.5">\n                                        <span>${escapeHtml(acc.name || \'Người dùng\')}</span>\n                                        ${isActive ? \'<span class="text-[9px] bg-emerald-500/20 text-emerald-400 px-1 py-0.2 rounded font-mono">ĐANG DÙNG</span>\' : \'\'}\n                                    </div>\n                                    <div class="text-[11px] text-gray-400 font-mono">${acc.phone}</div>\n                                </div>\n                            </div>\n                            <div class="flex items-center gap-1.5">\n                                ${!isActive ? `\n                                    <button onclick="switchActiveAccount(\'${acc.session_name}\')" class="px-2.5 py-1 rounded-lg bg-neonpurple/20 hover:bg-neonpurple/40 text-neonpurple text-xs font-semibold">\n                                        Chọn dùng\n                                    </button>\n                                    <button onclick="deleteSavedAccount(\'${acc.session_name}\')" title="Xóa" class="p-1 rounded text-gray-500 hover:text-red-400">\n                                        <i data-lucide="trash-2" class="w-4 h-4"></i>\n                                    </button>\n                                ` : `\n                                    <button onclick="logoutCurrentAccount(\'${acc.session_name}\')" class="px-2 py-1 rounded-lg bg-red-500/20 hover:bg-red-500/40 text-red-400 text-xs font-medium flex items-center gap-1">\n                                        <i data-lucide="log-out" class="w-3 h-3"></i> Đăng xuất\n                                    </button>\n                                `}\n                            </div>\n                        </div>\n                    `;\n                }).join(\'\');\n                lucide.createIcons();\n            } catch (err) {\n                console.error(err);\n            }\n        }\n\n        function resetAndReloadAllAccountData() {\n            document.getElementById(\'scrape-source-group\').value = \'\';\n            document.getElementById(\'members-table-body\').innerHTML = \'<tr><td colspan="4" class="py-6 text-center text-gray-500 italic">Đã đổi tài khoản. Chọn nhóm và bấm Bắt đầu cào.</td></tr>\';\n            document.getElementById(\'members-list-mobile\').innerHTML = \'<div class="text-center py-10 text-gray-500 italic text-xs">Đã đổi tài khoản. Chọn nhóm và bấm Bắt đầu cào.</div>\';\n            document.getElementById(\'members-count\').innerText = \'0\';\n            document.getElementById(\'scrape-counter\').innerText = \'0\';\n\n            checkConnection();\n            loadAccountsList();\n            loadMyGroups();\n        }\n\n        async function switchActiveAccount(sessionName) {\n            showToast(\'Đang chuyển tài khoản...\', \'info\');\n            try {\n                const res = await fetch(\'/api/accounts/switch\', {\n                    method: \'POST\',\n                    headers: { \'Content-Type\': \'application/json\' },\n                    body: JSON.stringify({ session_name: sessionName })\n                });\n                const data = await res.json();\n                if (data.success) {\n                    showToast(`Đã chuyển sang: ${data.name || data.phone}`, \'success\');\n                    resetAndReloadAllAccountData();\n                } else {\n                    showToast(data.reason || \'Lỗi chuyển tài khoản\', \'error\');\n                }\n            } catch (e) {\n                showToast(e.message, \'error\');\n            }\n        }\n\n        async function sendLoginCode() {\n            const phone = document.getElementById(\'new-acc-phone\').value.trim();\n            if (!phone) return showToast(\'Vui lòng nhập số điện thoại!\', \'error\');\n\n            const btn = document.getElementById(\'btn-send-code\');\n            btn.disabled = true;\n            btn.innerText = \'Đang gửi...\';\n\n            try {\n                const res = await fetch(\'/api/accounts/send_code\', {\n                    method: \'POST\',\n                    headers: { \'Content-Type\': \'application/json\' },\n                    body: JSON.stringify({ phone })\n                });\n                const data = await res.json();\n                if (data.error) {\n                    showToast(data.error, \'error\');\n                } else {\n                    showToast(`Đã gửi OTP đến Telegram của số: ${data.phone}`, \'success\');\n                    document.getElementById(\'otp-input-group\').classList.remove(\'hidden\');\n                }\n            } catch (e) {\n                showToast(\'Lỗi gửi OTP: \' + e.message, \'error\');\n            } finally {\n                btn.disabled = false;\n                btn.innerText = \'Gửi lại OTP\';\n            }\n        }\n\n        async function verifyLoginCode() {\n            const code = document.getElementById(\'new-acc-otp\').value.trim();\n            const password = document.getElementById(\'new-acc-pass\').value.trim();\n            if (!code) return showToast(\'Vui lòng nhập mã OTP!\', \'error\');\n\n            const btn = document.getElementById(\'btn-verify-code\');\n            btn.disabled = true;\n            btn.innerText = \'Đang xác thực...\';\n\n            try {\n                const res = await fetch(\'/api/accounts/verify_code\', {\n                    method: \'POST\',\n                    headers: { \'Content-Type\': \'application/json\' },\n                    body: JSON.stringify({ code, password })\n                });\n                const data = await res.json();\n                if (data.error) {\n                    showToast(data.error, \'error\');\n                } else if (data.status === \'password_needed\') {\n                    showToast(data.message, \'error\');\n                } else if (data.status === \'success\') {\n                    showToast(`Đăng nhập thành công: ${data.name}!`, \'success\');\n                    switchAccountTab(\'list\');\n                    resetAndReloadAllAccountData();\n                }\n            } catch (e) {\n                showToast(e.message, \'error\');\n            } finally {\n                btn.disabled = false;\n                btn.innerText = \'Xác nhận đăng nhập & Lưu\';\n            }\n        }\n\n        async function deleteSavedAccount(sessionName) {\n            if (!confirm(\'Bạn có chắc muốn xóa tài khoản này?\')) return;\n            try {\n                await fetch(\'/api/accounts/delete\', {\n                    method: \'POST\',\n                    headers: { \'Content-Type\': \'application/json\' },\n                    body: JSON.stringify({ session_name: sessionName })\n                });\n                showToast(\'Đã xóa tài khoản!\', \'info\');\n                resetAndReloadAllAccountData();\n            } catch (e) {\n                showToast(e.message, \'error\');\n            }\n        }\n\n        async function logoutCurrentAccount(sessionName) {\n            if (!confirm(\'Bạn muốn đăng xuất tài khoản này?\')) return;\n            try {\n                await fetch(\'/api/accounts/delete\', {\n                    method: \'POST\',\n                    headers: { \'Content-Type\': \'application/json\' },\n                    body: JSON.stringify({ session_name: sessionName })\n                });\n                showToast(\'Đã đăng xuất!\', \'success\');\n                resetAndReloadAllAccountData();\n            } catch (e) {\n                showToast(e.message, \'error\');\n            }\n        }\n\n        async function loadTunnelInfo() {\n            try {\n                const res = await fetch(\'/api/tunnel_info\');\n                const data = await res.json();\n                const link = data.public_url || data.local_url;\n                if (link) {\n                    const banner = document.getElementById(\'tunnel-banner\');\n                    const hrefEl = document.getElementById(\'tunnel-link-href\');\n                    banner.classList.remove(\'hidden\');\n                    hrefEl.href = link;\n                    hrefEl.innerText = link;\n                    window._mobileLink = link;\n                }\n                if (data.public_url && window._tunnelPoller) {\n                    clearInterval(window._tunnelPoller);\n                }\n            } catch (e) {}\n        }\n\n        function copyMobileLink() {\n            if (window._mobileLink) {\n                navigator.clipboard.writeText(window._mobileLink);\n                showToast(\'Đã sao chép link điện thoại!\', \'success\');\n            }\n        }\n\n        // Heartbeat giữ phiên kết nối\n        setInterval(() => {\n            fetch(\'/api/heartbeat\').catch(() => {});\n        }, 15000);\n\n        // Khởi động\n        checkConnection();\n        loadMyGroups();\n        loadTunnelInfo();\n        window._tunnelPoller = setInterval(loadTunnelInfo, 3000);\n    </script>\n</body>\n</html>\n'

async def handle_index(request):
    if os.path.exists(INDEX_HTML):
        with open(INDEX_HTML, "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    return web.Response(text=EMBEDDED_HTML, content_type="text/html")


async def handle_status(request):
    auth_info = await check_auth()
    return web.json_response(auth_info)


async def handle_accounts_list(request):
    data = get_accounts_list()
    return web.json_response(data)


async def handle_accounts_switch(request):
    body = await request.json()
    session_name = body.get("session_name")
    res = await switch_account(session_name)
    return web.json_response(res)


async def handle_accounts_send_code(request):
    global _last_heartbeat
    _last_heartbeat = time.time()
    body = await request.json()
    phone = body.get("phone", "").strip()
    if not phone:
        return web.json_response({"error": "Vui lòng nhập số điện thoại!"}, status=400)
    try:
        res = await start_login_request(phone)
        _last_heartbeat = time.time()
        return web.json_response(res)
    except Exception as e:
        _last_heartbeat = time.time()
        return web.json_response({"error": str(e)}, status=400)


async def handle_accounts_verify_code(request):
    global _last_heartbeat
    _last_heartbeat = time.time()
    body = await request.json()
    code = body.get("code", "").strip()
    password = body.get("password", "").strip() or None
    if not code:
        return web.json_response({"error": "Vui lòng nhập mã OTP!"}, status=400)
    try:
        res = await complete_login_request(code, password=password)
        _last_heartbeat = time.time()
        return web.json_response(res)
    except Exception as e:
        _last_heartbeat = time.time()
        return web.json_response({"error": str(e)}, status=400)


async def handle_accounts_delete(request):
    body = await request.json()
    session_name = body.get("session_name")
    res = await delete_saved_account(session_name)
    return web.json_response(res)


async def handle_my_groups(request):
    groups = await get_my_groups()
    return web.json_response({"groups": groups})


async def handle_scrape_start(request):
    global _scrape_state
    if _scrape_state["is_running"]:
        return web.json_response({"error": "Tiến trình cào thành viên đang chạy!"}, status=400)

    data = await request.json()
    source_group = data.get("source_group", "").strip()
    filter_days = data.get("filter_days")
    only_online = data.get("only_online", False)
    name_filter = data.get("name_filter", None)
    scrape_mode = data.get("scrape_mode", "members")
    msg_limit = int(data.get("msg_limit", 1000) or 1000)

    if not source_group:
        return web.json_response({"error": "Chưa nhập nhóm cần cào!"}, status=400)

    _scrape_state = {
        "is_running": True,
        "group": source_group,
        "scanned": 0,
        "matched": 0,
        "members": [],
        "error": None,
        "done": False
    }

    async def run_scrape():
        global _scrape_state
        try:
            async def on_progress(matched, scanned):
                _scrape_state["matched"] = matched
                _scrape_state["scanned"] = scanned

            members = await scrape_group_members(
                source_group,
                filter_days=filter_days,
                only_online=only_online,
                name_filter=name_filter,
                scrape_mode=scrape_mode,
                msg_limit=msg_limit,
                progress_callback=on_progress
            )
            _scrape_state["members"] = members
            _scrape_state["matched"] = len(members)
            _scrape_state["done"] = True
        except Exception as e:
            _scrape_state["error"] = str(e)
            _scrape_state["done"] = True
        finally:
            _scrape_state["is_running"] = False

    asyncio.create_task(run_scrape())
    return web.json_response({"status": "started"})


async def handle_scrape_status(request):
    return web.json_response(_scrape_state)


async def handle_scrape_export(request):
    format_type = request.match_info.get("format", "csv")
    members = _scrape_state.get("members", [])
    if not members:
        return web.Response(text="Chưa có dữ liệu cào", status=400)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if format_type == "csv":
        filename = f"members_{timestamp}.csv"
        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["id", "full_name", "username", "phone", "status", "profile_link"])
            writer.writeheader()
            for m in members:
                writer.writerow(m)
        return web.FileResponse(filepath)
    else:
        filename = f"members_{timestamp}.txt"
        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            for m in members:
                f.write(f"[{m['full_name']}](tg://user?id={m['id']})\n")
        return web.FileResponse(filepath)


async def handle_scrape_send_tg(request):
    data = await request.json()
    target_chat = data.get("target_chat", "me").strip() or "me"
    members = _scrape_state.get("members", [])
    if not members:
        return web.json_response({"error": "Không có thành viên để gửi!"}, status=400)

    client = get_telegram_client()
    total = len(members)
    chunk_size = 50

    try:
        try:
            target_entity = 'me' if target_chat == 'me' else await client.get_entity(target_chat)
        except Exception as e:
            return web.json_response({"error": f"Không tìm thấy chat đích: {str(e)}"}, status=400)

        for i in range(0, total, chunk_size):
            chunk = members[i:i + chunk_size]
            lines = [f'<a href="tg://user?id={u["id"]}">{u["full_name"]}</a>' for u in chunk]
            msg = "\n".join(lines)
            await client.send_message(target_entity, msg, parse_mode='html', link_preview=False)
            await asyncio.sleep(2.5)

        return web.json_response({"status": "success", "total_sent": total})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_tunnel_info(request):
    local_ip = get_local_ip()
    return web.json_response({
        "public_url": _public_url,
        "local_url": f"http://{local_ip}:{PORT}"
    })


async def handle_heartbeat(request):
    global _last_heartbeat
    _last_heartbeat = time.time()
    return web.json_response({"status": "ok"})


async def handle_exit(request):
    async def shutdown():
        await asyncio.sleep(0.5)
        os._exit(0)
    asyncio.create_task(shutdown())
    return web.json_response({"status": "exiting"})


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def try_launch_cloudflared():
    global _public_url
    cf_path = os.path.join(BASE_DIR, "cloudflared.exe")
    if not os.path.exists(cf_path):
        return None
    try:
        proc = subprocess.Popen(
            [cf_path, "tunnel", "--url", f"http://localhost:{PORT}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        def read_url():
            global _public_url
            import re
            for line in proc.stdout:
                match = re.search(r'https://[a-z0-9\-]+\.trycloudflare\.com', line)
                if match:
                    _public_url = match.group(0)
                    print(f"\n{'='*55}")
                    print(f"  LINK DÙNG TRÊN ĐIỆN THOẠI (BẤM VÀO LÀ DÙNG ĐƯỢC):")
                    print(f"  {_public_url}")
                    print(f"{'='*55}\n")
                    return

        threading.Thread(target=read_url, daemon=True).start()
        return proc
    except Exception as e:
        print(f"[!] Không thể khởi động cloudflared: {e}")
        return None


async def on_startup(app):
    local_ip = get_local_ip()
    print(f"\n{'='*55}")
    print(f"  TeleScraper Mobile đã khởi động thành công!")
    print(f"  Truy cập máy tính : http://127.0.0.1:{PORT}")
    print(f"  Truy cập cùng WiFi : http://{local_ip}:{PORT}")
    print(f"{'='*55}")

    cf_proc = try_launch_cloudflared()
    if not cf_proc:
        print(f"  [!] Chưa tìm thấy cloudflared.exe")
    print()


def create_app():
    app = web.Application()
    app.on_startup.append(on_startup)

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/heartbeat", handle_heartbeat)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/tunnel_info", handle_tunnel_info)
    app.router.add_get("/api/accounts", handle_accounts_list)
    app.router.add_post("/api/accounts/switch", handle_accounts_switch)
    app.router.add_post("/api/accounts/send_code", handle_accounts_send_code)
    app.router.add_post("/api/accounts/verify_code", handle_accounts_verify_code)
    app.router.add_post("/api/accounts/delete", handle_accounts_delete)
    app.router.add_get("/api/my_groups", handle_my_groups)
    app.router.add_post("/api/scrape/start", handle_scrape_start)
    app.router.add_get("/api/scrape/status", handle_scrape_status)
    app.router.add_get("/api/scrape/export/{format}", handle_scrape_export)
    app.router.add_post("/api/scrape/send_tg", handle_scrape_send_tg)
    app.router.add_post("/api/exit", handle_exit)

    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=PORT)