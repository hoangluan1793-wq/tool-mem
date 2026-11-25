import os
import sys
import json
import asyncio
import html
import re
from datetime import datetime, timezone
from telethon import TelegramClient, errors, types
from telethon.sessions import StringSession, SQLiteSession
from telethon.tl.types import (
    Channel, Chat, User,
    UserStatusOnline, UserStatusOffline,
    UserStatusRecently, UserStatusLastWeek, UserStatusLastMonth,
    ChatAdminRights
)
from telethon.tl.functions.messages import ImportChatInviteRequest

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
DEFAULT_SESSION = os.path.join(BASE_DIR, "tele_session")

os.makedirs(SESSIONS_DIR, exist_ok=True)

_client = None
_current_session_path = DEFAULT_SESSION
_pending_login_client = None
_pending_phone = None
_pending_phone_code_hash = None
_scrape_cancel_requested = False

def request_scrape_stop():
    global _scrape_cancel_requested
    _scrape_cancel_requested = True

def is_scrape_cancelled():
    global _scrape_cancel_requested
    return _scrape_cancel_requested

DEFAULT_API_ID = 2040
DEFAULT_API_HASH = "b18441a1ff607e10a989891a5462e627"

CLIENT_PARAMS = {
    "device_model": "Desktop",
    "system_version": "Windows 10",
    "app_version": "5.6.3 x64",
    "lang_code": "en",
    "system_lang_code": "en"
}

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
        # Kiểm tra file session ở thư mục gốc hoặc thư mục sessions
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

    if not api_id or not api_hash:
        return None

    _current_session_path = session_path
    _client = TelegramClient(session_path, api_id, api_hash, **CLIENT_PARAMS)
    return _client

async def check_auth():
    client = get_telegram_client()
    if not client:
        return {"connected": False, "reason": "Chưa có API ID/Hash trong config.json"}

    if not client.is_connected():
        await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        
        # Đồng bộ tài khoản hiện tại vào config accounts
        config = get_config()
        accounts = config.get("accounts", [])
        current_phone = format_phone(me.phone) if me.phone else config.get("phone", "")
        current_session = config.get("active_session", "tele_session")

        # Cập nhật hoặc thêm vào danh sách accounts
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

# --- QUẢN LÝ ĐA TÀI KHOẢN ---
def get_accounts_list():
    config = get_config()
    accounts = config.get("accounts", [])
    active_session = config.get("active_session", "tele_session")
    return {
        "accounts": accounts,
        "active_session": active_session
    }


def export_all_sessions():
    """Xuất toàn bộ tài khoản + StringSession để lưu trữ an toàn trên trình duyệt / Cloud."""
    config = get_config()
    accounts = config.get("accounts", [])
    active_session = config.get("active_session", "tele_session")
    sessions_map = {}

    for acc in accounts:
        sname = acc.get("session_name")
        if not sname:
            continue
        if _client is not None and active_session == sname and _client.session and _client.session.auth_key:
            ss = StringSession()
            ss.set_dc(_client.session.dc_id, _client.session.server_address, _client.session.port)
            ss.auth_key = _client.session.auth_key
            sessions_map[sname] = ss.save()
            continue

        spath = os.path.join(SESSIONS_DIR, f"{sname}.session")
        if not os.path.exists(spath):
            spath = os.path.join(BASE_DIR, f"{sname}.session")
        if os.path.exists(spath):
            try:
                sq = SQLiteSession(spath[:-8])
                if sq.auth_key:
                    ss = StringSession()
                    ss.set_dc(sq.dc_id, sq.server_address, sq.port)
                    ss.auth_key = sq.auth_key
                    sessions_map[sname] = ss.save()
                sq.close()
            except Exception:
                pass

    return {
        "accounts": accounts,
        "active_session": active_session,
        "sessions": sessions_map,
    }


async def restore_all_sessions(payload):
    """Khôi phục các tài khoản + StringSession từ trình duyệt vào server."""
    global _client, _current_session_path
    if not isinstance(payload, dict):
        return {"restored": 0}

    accounts = payload.get("accounts") or []
    active_session = payload.get("active_session") or ""
    sessions_map = payload.get("sessions") or {}

    if not sessions_map:
        return {"restored": 0}

    if _client and _client.is_connected():
        try:
            await _client.disconnect()
        except Exception:
            pass
    _client = None
    _current_session_path = DEFAULT_SESSION

    restored = 0
    valid_accounts = []
    for acc in accounts:
        sname = str(acc.get("session_name", "")).strip()
        if not re.match(r'^[a-zA-Z0-9_+\-]+$', sname):
            continue
        s_str = sessions_map.get(sname)
        if not s_str:
            continue
        try:
            ss = StringSession(s_str)
            if not ss.auth_key:
                continue
            spath = os.path.join(SESSIONS_DIR, sname)
            sq = SQLiteSession(spath)
            sq.set_dc(ss.dc_id, ss.server_address, ss.port)
            sq.auth_key = ss.auth_key
            sq.save()
            sq.close()
            valid_accounts.append(acc)
            restored += 1
        except Exception:
            pass

    if valid_accounts:
        config = get_config()
        config["accounts"] = valid_accounts
        if active_session and any(a.get("session_name") == active_session for a in valid_accounts):
            config["active_session"] = active_session
        else:
            config["active_session"] = valid_accounts[0]["session_name"]
        save_config(config)

    auth_res = await check_auth()
    return {"restored": restored, **auth_res}

async def switch_account(session_name):
    global _client
    config = get_config()
    config["active_session"] = session_name
    save_config(config)

    if _client and _client.is_connected():
        await _client.disconnect()
    _client = None

    # Khởi động lại với session mới
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

    if not api_id or not api_hash:
        raise Exception("Chưa cấu hình API ID hoặc API Hash!")

    clean_phone = format_phone(phone_number)
    digits = re.sub(r'\D', '', clean_phone)
    new_session_path = os.path.join(SESSIONS_DIR, f"session_{digits}")

    # Đóng phiên chờ trước đó nếu có để giải phóng file SQLite
    if _pending_login_client:
        try:
            if _pending_login_client.is_connected():
                await _pending_login_client.disconnect()
        except Exception:
            pass
        _pending_login_client = None

    _pending_phone = clean_phone
    _pending_login_client = TelegramClient(new_session_path, api_id, api_hash, **CLIENT_PARAMS)
    await _pending_login_client.connect()

    try:
        sent = await _pending_login_client.send_code_request(clean_phone)
        _pending_phone_code_hash = sent.phone_code_hash
    except errors.FloodWaitError as e:
        raise Exception(f"Telegram yêu cầu giãn cách. Vui lòng thử lại sau {e.seconds} giây.")

    return {
        "status": "code_sent",
        "phone": clean_phone
    }

async def complete_login_request(code, password=None):
    global _pending_login_client, _pending_phone, _pending_phone_code_hash, _client
    if not _pending_login_client:
        raise Exception("Không tìm thấy phiên đăng nhập đang chờ. Vui lòng bấm gửi mã lại!")

    clean_phone = _pending_phone
    try:
        await _pending_login_client.sign_in(clean_phone, code=code, phone_code_hash=_pending_phone_code_hash)
    except errors.FloodWaitError as e:
        raise Exception(f"Telegram yêu cầu giãn cách đăng nhập. Vui lòng thử lại sau {e.seconds} giây.")
    except errors.SessionPasswordNeededError:
        if not password:
            return {"status": "password_needed", "message": "Tài khoản có bảo mật 2 bước (2FA). Vui lòng nhập mật khẩu 2FA!"}
        try:
            await _pending_login_client.sign_in(password=password)
        except errors.FloodWaitError as e:
            raise Exception(f"Telegram yêu cầu giãn cách đăng nhập. Vui lòng thử lại sau {e.seconds} giây.")

    me = await _pending_login_client.get_me()
    digits = re.sub(r'\D', '', clean_phone)
    session_name = f"session_{digits}"

    # Lưu vào danh sách accounts
    config = get_config()
    accounts = config.get("accounts", [])
    
    # Kiểm tra tồn tại
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

    # Đóng client cũ nếu có
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

    # Nếu xóa đúng tài khoản đang chọn, chuyển về tài khoản khác
    if config.get("active_session") == session_name:
        config["active_session"] = accounts[0]["session_name"] if accounts else "tele_session"

    save_config(config)

    # Xóa file session
    sess_file = os.path.join(SESSIONS_DIR, f"{session_name}.session")
    if os.path.exists(sess_file):
        try:
            os.remove(sess_file)
        except Exception:
            pass

    return {"status": "deleted"}

# --- LẤY DANH SÁCH TẤT CẢ CÁC NHÓM ĐÃ THAM GIA ---
async def get_my_groups():
    client = get_telegram_client()
    if not client:
        return []
    try:
        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            return []
    except Exception:
        return []

    groups = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        # Nhóm thường (Chat) hoặc Siêu nhóm (Supergroup)
        if dialog.is_group or (isinstance(entity, Channel) and (entity.megagroup or not entity.broadcast)):
            count = getattr(entity, 'participants_count', 0)
            groups.append({
                "id": str(dialog.id),
                "title": dialog.title,
                "username": getattr(entity, 'username', '') or '',
                "participants_count": count
            })
    return groups

# --- LẤY DANH SÁCH KÊNH QUẢN LÝ ---
async def get_admin_channels():
    client = get_telegram_client()
    if not client:
        return []
    try:
        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            return []
    except Exception:
        return []

    channels = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if isinstance(entity, Channel):
            if entity.creator or (entity.admin_rights and (entity.admin_rights.post_messages or entity.admin_rights.change_info)):
                channels.append({
                    "id": str(entity.id),
                    "title": entity.title,
                    "username": entity.username or "",
                    "is_broadcast": entity.broadcast,
                    "is_megagroup": entity.megagroup,
                    "participants_count": getattr(entity, 'participants_count', 0)
                })
    return channels

# --- ĐĂNG BÀI LÊN KÊNH ---
async def post_to_channel(channel_identifier, text, media_path=None):
    client = get_telegram_client()
    if not client:
        raise Exception("Telegram client chưa được xác thực")
    if not client.is_connected():
        await client.connect()
    if not await client.is_user_authorized():
        raise Exception("Telegram client chưa được xác thực")

    try:
        try:
            target_id = int(channel_identifier)
        except ValueError:
            target_id = channel_identifier

        entity = await client.get_entity(target_id)
    except Exception as e:
        raise Exception(f"Không thể tìm thấy kênh '{channel_identifier}': {str(e)}")

    if media_path and os.path.exists(media_path):
        msg = await client.send_file(
            entity,
            media_path,
            caption=text,
            parse_mode='html',
            link_preview=False
        )
    else:
        msg = await client.send_message(
            entity,
            text,
            parse_mode='html',
            link_preview=False
        )
    return msg.id

# --- LOGIC MEMBER FILTER & SCRAPER ---
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

def _matches_name_filter(full_name, name_filter):
    if not name_filter:
        return True
    if name_filter == "vietnamese":
        return _has_vietnamese(full_name)
    if name_filter == "english":
        return bool(re.search(r'[a-zA-Z]', full_name)) and not _has_vietnamese(full_name)
    return True

async def _resolve_group_entity(client, group_input, join_invite=True):
    """Tìm entity nhóm theo link invite, ID, @username hoặc tên nhóm đã tham gia."""
    group_input = group_input.strip()
    entity = None

    if join_invite and ("t.me/+" in group_input or "t.me/joinchat/" in group_input):
        invite_hash = group_input.split("+")[-1].split("joinchat/")[-1].strip()
        try:
            await client(ImportChatInviteRequest(invite_hash))
        except errors.UserAlreadyParticipantError:
            pass
        except Exception:
            pass

    try:
        if group_input.isdigit() or (group_input.startswith("-") and group_input[1:].isdigit()):
            entity = await client.get_entity(int(group_input))
        else:
            entity = await client.get_entity(group_input)
    except Exception:
        pass

    if not entity:
        search_kw = group_input.lower()
        async for dialog in client.iter_dialogs():
            if dialog.is_group or (isinstance(dialog.entity, Channel) and (dialog.entity.megagroup or not dialog.entity.broadcast)):
                d_title = dialog.title.strip().lower()
                if d_title == search_kw or search_kw in d_title or str(dialog.id) == group_input:
                    entity = dialog.entity
                    break

    if not entity:
        raise Exception(f"Không tìm thấy nhóm '{group_input}'. Vui lòng kiểm tra lại liên kết hoặc đảm bảo tài khoản đã tham gia nhóm!")
    return entity


async def _fetch_exclude_member_ids(client, exclude_entity):
    """Lấy tập user_id từ nhóm loại trừ (ưu tiên danh sách thành viên, fallback quét tin nhắn nếu ẩn mem)."""
    ids = set()
    try:
        async for user in client.iter_participants(exclude_entity, aggressive=True):
            if _scrape_cancel_requested:
                break
            if not user.bot and not user.deleted:
                ids.add(user.id)
        return ids
    except (errors.ChatAdminRequiredError, errors.FloodWaitError):
        pass
    except Exception as e:
        err_msg = str(e).lower()
        if not any(k in err_msg for k in ("admin", "participant", "privilege")) and not ids:
            raise Exception(f"Không thể lấy thành viên nhóm loại trừ: {str(e)}")

    # Fallback: nhóm loại trừ ẩn danh sách thành viên -> quét qua tin nhắn gần nhất
    try:
        async for msg in client.iter_messages(exclude_entity, limit=2000):
            if _scrape_cancel_requested:
                break
            if msg.sender_id:
                ids.add(msg.sender_id)
    except Exception:
        pass
    return ids


async def scrape_group_members(
    source_group,
    filter_days=None,
    only_online=False,
    name_filter=None,
    scrape_mode="members",
    msg_limit=1000,
    progress_callback=None,
    exclude_group=None,
    dedup_mode="all"
):
    global _scrape_cancel_requested
    _scrape_cancel_requested = False

    client = get_telegram_client()
    if not client:
        raise Exception("Telegram chưa đăng nhập")
    if not client.is_connected():
        await client.connect()
    if not await client.is_user_authorized():
        raise Exception("Telegram chưa đăng nhập")

    entity = await _resolve_group_entity(client, source_group, join_invite=True)
    source_group_id = str(entity.id)

    excluded_ids = set()
    if exclude_group and exclude_group.strip():
        exclude_entity = await _resolve_group_entity(client, exclude_group, join_invite=True)
        if exclude_entity.id == entity.id:
            raise Exception("Nhóm loại trừ không được trùng với nhóm cần cào! Vui lòng chọn 2 nhóm khác nhau.")
        excluded_ids.update(await _fetch_exclude_member_ids(client, exclude_entity))

    if dedup_mode in ("new_in_group", "new_global"):
        from core.database import get_scraped_user_ids
        history_group = source_group_id if dedup_mode == "new_in_group" else None
        excluded_ids.update(get_scraped_user_ids(history_group))

    members = []
    seen_ids = set()
    count_scanned = 0

    if scrape_mode == "messages":
        # CHẾ ĐỘ QUÉT LỊCH SỬ CHAT: Dành cho nhóm ẩn danh sách thành viên
        user_msg_counts = {}
        users_map = {}
        rejected_uids = set()

        scan_limit = msg_limit if (msg_limit is not None and msg_limit > 0) else None
        try:
            async for msg in client.iter_messages(entity, limit=scan_limit):
                if _scrape_cancel_requested:
                    break
                count_scanned += 1
                uid = msg.sender_id
                if uid and (uid in excluded_ids or uid in rejected_uids):
                    if progress_callback and count_scanned % 25 == 0:
                        await progress_callback(len(users_map), count_scanned)
                    continue

                if uid and uid in users_map:
                    user_msg_counts[uid] = user_msg_counts.get(uid, 0) + 1
                    if progress_callback and count_scanned % 25 == 0:
                        await progress_callback(len(users_map), count_scanned)
                    continue

                sender = msg.sender
                if not sender and uid:
                    try:
                        sender = await client.get_entity(uid)
                    except Exception:
                        rejected_uids.add(uid)
                        continue

                if not sender or not isinstance(sender, User):
                    if uid:
                        rejected_uids.add(uid)
                    continue
                if sender.bot or sender.deleted:
                    rejected_uids.add(sender.id)
                    continue

                uid = sender.id
                if uid in excluded_ids or uid in rejected_uids:
                    continue

                first = sender.first_name or ""
                last = sender.last_name or ""
                full_name = f"{first} {last}".strip() or "Người dùng ẩn danh"
                is_valid, status_str = check_member_status(sender, filter_days=filter_days, only_online=only_online)
                if not is_valid or not _matches_name_filter(full_name, name_filter):
                    rejected_uids.add(uid)
                    continue

                user_msg_counts[uid] = 1
                users_map[uid] = (sender, full_name, status_str)

                if progress_callback and count_scanned % 25 == 0:
                    await progress_callback(len(users_map), count_scanned)
        except errors.FloodWaitError as e:
            if not users_map:
                raise Exception(f"Telegram yêu cầu giãn cách tạm thời. Vui lòng thử lại sau {e.seconds} giây.")
        except Exception as e:
            if not users_map:
                raise Exception(f"Lỗi khi quét tin nhắn trong nhóm: {str(e)}")

        for uid, (user, full_name, status_str) in users_map.items():
            cnt = user_msg_counts.get(uid, 1)
            members.append({
                "id": user.id,
                "full_name": full_name,
                "username": user.username or "",
                "phone": user.phone or "",
                "status": f"{status_str} (Đã chat {cnt} tin)",
                "profile_link": f"tg://user?id={user.id}"
            })

        # Sắp xếp người nhắn nhiều tin nhất lên đầu (những người tương tác tích cực nhất)
        members.sort(key=lambda x: user_msg_counts.get(x["id"], 0), reverse=True)
    else:
        # CHẾ ĐỘ CÀO DANH SÁCH THÀNH VIÊN TRỰC TIẾP (Nhóm mở thành viên)
        try:
            async for user in client.iter_participants(entity, aggressive=True):
                if _scrape_cancel_requested:
                    break
                count_scanned += 1
                if user.bot or user.deleted:
                    continue
                if user.id in seen_ids or user.id in excluded_ids:
                    continue
                seen_ids.add(user.id)

                is_valid, status_str = check_member_status(user, filter_days=filter_days, only_online=only_online)
                if not is_valid:
                    continue

                first = user.first_name or ""
                last = user.last_name or ""
                full_name = f"{first} {last}".strip() or "Người dùng ẩn danh"
                if not _matches_name_filter(full_name, name_filter):
                    continue

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
        except errors.FloodWaitError as e:
            if not members:
                raise Exception(f"Telegram yêu cầu giãn cách tạm thời. Vui lòng thử lại sau {e.seconds} giây.")
        except errors.ChatAdminRequiredError:
            raise Exception("Nhóm này ẩn danh sách thành viên. Vui lòng chuyển sang 'Lọc qua tin nhắn' để quét!")
        except Exception as e:
            err_msg = str(e).lower()
            if "admin" in err_msg or "participant" in err_msg or "privilege" in err_msg:
                raise Exception(f"Nhóm này có thể đã ẩn danh sách thành viên. Vui lòng chọn 'Lọc qua tin nhắn'!")
            if not members:
                raise e

    if progress_callback:
        await progress_callback(len(members), count_scanned)

    if members:
        from core.database import save_scraped_user_ids
        save_scraped_user_ids(source_group_id, [m["id"] for m in members])

    return members
