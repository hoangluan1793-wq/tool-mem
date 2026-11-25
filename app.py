import os
import sys

# Đảm bảo không bị lỗi khi chạy ở chế độ GUI không có console (--noconsole)
if sys.stdout is None:
    class _NullWriter:
        def write(self, s): pass
        def flush(self): pass
    sys.stdout = _NullWriter()
if sys.stderr is None:
    sys.stderr = _NullWriter()

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import json
import asyncio
import csv
import subprocess
import threading
import time
import tempfile
import zipfile
import shutil
import zlib
import base64
import hashlib
import aiohttp
from datetime import datetime
from aiohttp import web

from core.embedded_web import EMBEDDED_HTML_B64

from core.database import (
    get_connection, get_dashboard_metrics, get_recent_logs, log_action,
    get_scraped_history_count, clear_scraped_history
)
from core.telegram_client import (
    get_telegram_client, check_auth, get_admin_channels, get_my_groups, scrape_group_members, post_to_channel,
    get_accounts_list, switch_account, start_login_request, complete_login_request, delete_saved_account,
    request_scrape_stop, export_all_sessions, restore_all_sessions
)
from modules.ads_manager.scheduler import (
    start_scheduler, stop_scheduler, set_campaign_status, delete_campaign, execute_campaign
)
IS_CLOUD = bool(os.environ.get("RENDER") or os.environ.get("PORT"))

if not IS_CLOUD:
    from core.licensing import get_hwid, verify_license, apply_license, verify_update_signature, parse_version
else:
    def get_hwid(): return "RENDER-CLOUD"
    def verify_license(): return True, "Cloud mode", "2099-12-31 23:59:59"
    def apply_license(key): return True, "Cloud mode"
    def verify_update_signature(v, s, sig): return False
    def parse_version(v): return (0, 0, 0)

# Xác định đường dẫn tương thích cả khi chạy code thường (.py) và khi đóng gói (.exe)
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, '_MEIPASS', BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR

_web_candidate = os.path.join(BUNDLE_DIR, "web")
if not os.path.exists(_web_candidate):
    _web_candidate = os.path.join(BASE_DIR, "web")

STATIC_DIR = os.path.join(_web_candidate, "static")
INDEX_HTML = os.path.join(_web_candidate, "index.html")
UPLOADS_DIR = os.path.join(BASE_DIR, "web", "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# === CẤU HÌNH UPDATE ===
# Gist chứa thông tin bản mới (version.json do Tao_Ban_Cap_Nhat.py tạo ra):
REMOTE_VERSION_URL = "https://gist.githubusercontent.com/hoangluan1793-wq/005a02a26faae26e02b2fdbe030e74d1/raw/"

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Trạng thái cào mem đang chạy
_scrape_state = {
    "is_running": False,
    "group": "",
    "scanned": 0,
    "matched": 0,
    "members": [],
    "error": None,
    "done": False
}

# --- BẢO MẬT GIAO DIỆN & TỰ ĐỘNG GIẢI NÉN TRONG BỘ NHỚ ---
def get_secure_index_html():
    """Phục vụ HTML trực tiếp từ bộ nhớ khi đóng gói EXE, chống sửa file để bẻ khóa."""
    if not getattr(sys, 'frozen', False) and os.path.exists(INDEX_HTML):
        try:
            with open(INDEX_HTML, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    try:
        return zlib.decompress(base64.b64decode(EMBEDDED_HTML_B64.encode("ascii"))).decode("utf-8")
    except Exception as e:
        return f"<h3>Lỗi khởi động giao diện: {str(e)}</h3>"

# --- CHỐT CHẶN BẢO MẬT BACKEND (RSA LICENSE GUARD) ---
@web.middleware
async def license_guard_middleware(request, handler):
    """Bắt buộc kiểm tra chữ ký số RSA cho toàn bộ API chức năng. Chống can thiệp HTML để dùng chùa."""
    if IS_CLOUD:
        return await handler(request)

    exempt_paths = {
        "/",
        "/api/heartbeat",
        "/api/exit",
        "/api/license/status",
        "/api/license/activate",
        "/api/update/check"
    }
    if request.path in exempt_paths or request.path.startswith("/static"):
        return await handler(request)

    is_valid, reason, _ = verify_license()
    if not is_valid:
        return web.json_response({
            "error": "Phần mềm chưa được kích hoạt bản quyền hợp lệ!",
            "license_required": True,
            "reason": reason
        }, status=403)

    return await handler(request)

# --- HANDLERS ---
async def handle_index(request):
    html = get_secure_index_html()
    if IS_CLOUD:
        html = html.replace("</head>", "<script>window.CLOUD_SCRAPER_ONLY = true;</script></head>", 1)
    return web.Response(text=html, content_type="text/html")

async def handle_status(request):
    auth_info = await check_auth()
    return web.json_response(auth_info)

# --- QUẢN LÝ BẢN QUYỀN MÃ THIẾT BỊ ---
async def handle_license_status(request):
    if IS_CLOUD:
        return web.json_response({
            "is_valid": True,
            "reason": "Cloud mode",
            "expiry": "2099-12-31 23:59:59",
            "hwid": "RENDER-CLOUD"
        })
    is_valid, reason, expiry = verify_license()
    hwid = get_hwid()
    return web.json_response({
        "is_valid": is_valid,
        "reason": reason,
        "expiry": expiry,
        "hwid": hwid
    })

async def handle_license_activate(request):
    body = await request.json()
    key = body.get("key", "").strip()
    if not key:
        return web.json_response({"success": False, "message": "Vui lòng nhập mã kích hoạt!"}, status=400)
    
    success, message = apply_license(key)
    return web.json_response({"success": success, "message": message})

# --- HỆ THỐNG CẬP NHẬT ---
UPDATE_DIR = os.path.join(BASE_DIR, "_update")
UPDATE_EXE_NAME = "Telegram_Toolkit_Pro.exe"
UPDATE_MAX_BYTES = 800 * 1024 * 1024
_update_pending = None
_update_busy = False

def get_current_version():
    try:
        with open(os.path.join(BASE_DIR, "version.json"), "r", encoding="utf-8") as f:
            return str(json.load(f).get("version", "1.0.0")).strip()
    except Exception:
        return "1.0.0"

CURRENT_VERSION = get_current_version()

async def fetch_remote_update():
    """Đọc thông tin bản mới trên mạng. Chỉ trả về khi chữ ký đúng của admin, ngược lại trả None."""
    url = f"{REMOTE_VERSION_URL}?t={int(time.time())}"
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers={"Cache-Control": "no-cache"}) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            remote = await resp.json(content_type=None)

    version = str(remote.get("version", "")).strip()
    sha256 = str(remote.get("sha256", "")).strip().lower()
    sig = str(remote.get("sig", "")).strip()
    download_url = str(remote.get("download_url", "")).strip()
    if not (version and sha256 and sig and download_url.lower().startswith("https://")):
        return None
    if not verify_update_signature(version, sha256, sig):
        return None
    return {
        "version": version,
        "sha256": sha256,
        "download_url": download_url,
        "changelog": str(remote.get("changelog", "")).strip(),
    }

async def handle_update_check(request):
    """Kiểm tra có bản mới hợp lệ không (đúng chữ ký, version cao hơn bản đang chạy)."""
    global _update_pending
    out = {"has_update": False, "current": CURRENT_VERSION, "latest": CURRENT_VERSION, "changelog": ""}
    try:
        info = await fetch_remote_update()
        if info and parse_version(info["version"]) > parse_version(CURRENT_VERSION):
            _update_pending = info
            out.update(has_update=True, latest=info["version"], changelog=info["changelog"])
        else:
            _update_pending = None
    except Exception as e:
        out["error"] = str(e)
    return web.json_response(out)

def _extract_update(zip_path, expected_version):
    out_dir = os.path.join(UPDATE_DIR, "new")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.realpath(out_dir)
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not os.path.realpath(os.path.join(out_dir, name)).startswith(base + os.sep):
                raise Exception("file nén không hợp lệ")
        zf.extractall(out_dir)

    new_root = None
    for root, _dirs, files in os.walk(out_dir):
        if UPDATE_EXE_NAME in files:
            new_root = root
            break
    if not new_root or not os.path.isdir(os.path.join(new_root, "_internal")):
        raise Exception("gói cập nhật thiếu file chương trình")

    try:
        with open(os.path.join(new_root, "version.json"), "r", encoding="utf-8") as f:
            pkg_version = str(json.load(f).get("version", "")).strip()
    except Exception:
        pkg_version = ""
    if pkg_version != expected_version:
        raise Exception("gói cập nhật sai phiên bản")
    return new_root

async def _download_update(info):
    shutil.rmtree(UPDATE_DIR, ignore_errors=True)
    os.makedirs(UPDATE_DIR, exist_ok=True)
    zip_path = os.path.join(UPDATE_DIR, "update.zip")
    digest = hashlib.sha256()
    size = 0
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(info["download_url"]) as resp:
            if resp.status != 200:
                raise Exception(f"không tải được file (HTTP {resp.status})")
            with open(zip_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(262144):
                    size += len(chunk)
                    if size > UPDATE_MAX_BYTES:
                        raise Exception("file tải về quá lớn")
                    digest.update(chunk)
                    f.write(chunk)
    if digest.hexdigest() != info["sha256"]:
        raise Exception("file tải về không khớp chữ ký, đã hủy cập nhật")
    return await asyncio.to_thread(_extract_update, zip_path, info["version"])

def _launch_update_script(new_root):
    """Tạo file .bat chờ tool tắt hẳn, chép đè file mới (giữ dữ liệu khách), rồi mở lại tool."""
    exe = os.path.join(BASE_DIR, UPDATE_EXE_NAME)
    bat = os.path.join(UPDATE_DIR, "apply_update.bat")
    log = os.path.join(BASE_DIR, "update.log")
    keep_dirs = " ".join(f'"{os.path.join(new_root, d)}"' for d in ("sessions", "chrome_profile", "output", "web\\uploads"))
    script = "\r\n".join([
        "@echo off",
        "chcp 65001 >nul",
        f'powershell -NoProfile -WindowStyle Hidden -Command "Wait-Process -Id {os.getpid()} -Timeout 40 -ErrorAction SilentlyContinue"',
        "ping -n 3 127.0.0.1 >nul",
        f'robocopy "{new_root}" "{BASE_DIR}" /E /R:30 /W:1 /NFL /NDL /NJH /NJS /NP '
        f'/XF config.json license.key toolkit.db .sys_time.dat .app.lock *.session *.session-journal '
        f'/XD {keep_dirs} > "{log}" 2>&1',
        f'if errorlevel 8 echo [LOI] Cap nhat khong hoan tat, vui long gui file nay cho @hoang1793 >> "{log}"',
        f'cd /d "{BASE_DIR}"',
        f'start "" "{exe}" --no-window',
        "",
    ])
    with open(bat, "w", encoding="utf-8", newline="") as f:
        f.write(script)

    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    no_window = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(["cmd.exe", "/c", bat], creationflags=no_window | 0x01000000, env=env, close_fds=True, cwd=BASE_DIR)
    except OSError:
        subprocess.Popen(["cmd.exe", "/c", bat], creationflags=no_window, env=env, close_fds=True, cwd=BASE_DIR)

async def handle_update_apply(request):
    """Tải, kiểm tra chữ ký rồi cài bản cập nhật. Tool tự tắt và mở lại sau khi cài xong."""
    global _update_busy
    def fail(msg):
        return web.json_response({"success": False, "message": msg})

    if not getattr(sys, "frozen", False):
        return fail("Chỉ cập nhật tự động được trên bản EXE đã đóng gói.")
    if _update_busy:
        return fail("Đang cập nhật, vui lòng chờ trong giây lát!")
    if _scrape_state.get("is_running"):
        return fail("Đang quét thành viên. Hãy bấm Dừng quét hoặc đợi quét xong rồi cập nhật.")
    info = _update_pending
    if not info:
        return fail("Chưa có bản cập nhật hợp lệ. Hãy mở lại phần mềm để kiểm tra.")

    _update_busy = True
    try:
        new_root = await _download_update(info)
        _launch_update_script(new_root)
    except Exception as e:
        _update_busy = False
        shutil.rmtree(UPDATE_DIR, ignore_errors=True)
        return fail(f"Lỗi cập nhật: {e}")

    async def shutdown():
        await asyncio.sleep(1.5)
        os._exit(0)
    asyncio.create_task(shutdown())
    return web.json_response({"success": True, "message": "Đã tải xong, đang cài đặt và khởi động lại..."})

# --- QUẢN LÝ ĐA TÀI KHOẢN ---
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

async def handle_accounts_export_sessions(request):
    return web.json_response(export_all_sessions())

async def handle_accounts_restore_sessions(request):
    body = await request.json()
    res = await restore_all_sessions(body)
    return web.json_response(res)

async def handle_channels(request):
    channels = await get_admin_channels()
    return web.json_response({"channels": channels})

async def handle_my_groups(request):
    groups = await get_my_groups()
    return web.json_response({"groups": groups})

# --- MEMBER SCRAPER HANDLERS ---
async def handle_scrape_start(request):
    global _scrape_state
    if _scrape_state["is_running"]:
        return web.json_response({"error": "Tiến trình quét đang hoạt động, vui lòng chờ!"}, status=400)

    data = await request.json()
    source_group = data.get("source_group", "").strip()
    exclude_group = data.get("exclude_group", "").strip()
    dedup_mode = data.get("dedup_mode", "all")
    filter_days = data.get("filter_days")
    only_online = data.get("only_online", False)
    name_filter = data.get("name_filter", None)
    scrape_mode = data.get("scrape_mode", "members")
    raw_limit = data.get("msg_limit", 1000)
    if raw_limit == "all" or raw_limit is None or str(raw_limit).lower() == "none":
        msg_limit = None
    else:
        try:
            msg_limit = int(raw_limit)
        except Exception:
            msg_limit = 1000

    if not source_group:
        return web.json_response({"error": "Vui lòng nhập hoặc chọn nhóm nguồn!"}, status=400)
    if exclude_group and exclude_group.lower() == source_group.lower():
        return web.json_response({"error": "Nhóm loại trừ không được trùng với nhóm cần cào!"}, status=400)

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
                progress_callback=on_progress,
                exclude_group=exclude_group or None,
                dedup_mode=dedup_mode
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

async def handle_scrape_stop(request):
    request_scrape_stop()
    return web.json_response({"status": "stopping"})

async def handle_scrape_status(request):
    resp = dict(_scrape_state)
    resp["total_saved"] = get_scraped_history_count()
    return web.json_response(resp)

async def handle_scrape_history_stats(request):
    return web.json_response({"total_saved": get_scraped_history_count()})

async def handle_scrape_history_clear(request):
    clear_scraped_history()
    return web.json_response({"status": "cleared", "total_saved": 0})

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
        return web.json_response({"error": "Danh sách thành viên đang trống!"}, status=400)

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

# --- ADS MANAGER HANDLERS ---
async def handle_ads_dashboard(request):
    metrics = get_dashboard_metrics()
    logs = get_recent_logs(15)
    return web.json_response({"metrics": metrics, "logs": logs})

async def handle_ads_campaigns_list(request):
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT c.*, s.start_time, s.end_time, s.is_posted, s.schedule_mode, s.interval_minutes,
                   m.file_name as media_name, m.media_type
            FROM campaigns c
            LEFT JOIN campaign_schedule s ON c.id = s.campaign_id
            LEFT JOIN campaign_media m ON c.id = m.campaign_id
            ORDER BY c.id DESC
        """).fetchall()
        return web.json_response({"campaigns": [dict(r) for r in rows]})
    finally:
        conn.close()

async def _parse_campaign_multipart(request):
    """Đọc dữ liệu multipart form cho tạo mới hoặc cập nhật lịch đăng bài."""
    reader = await request.multipart()
    fields = {
        "name": "",
        "description": "",
        "content": "",
        "destination_link": "",
        "target_channels": "[]",
        "start_date": "",
        "start_time": "",
        "schedule_mode": "once",
        "interval_minutes": 0,
        "status": "DRAFT",
        "budget": 0.0,
        "media_file_path": None,
        "media_file_name": None,
        "media_type": None,
    }
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name in ("name", "description", "content", "destination_link", "start_date", "start_time", "status"):
            fields[part.name] = (await part.text()).strip()
        elif part.name == "target_channels":
            fields["target_channels"] = await part.text()
        elif part.name == "schedule_mode":
            fields["schedule_mode"] = (await part.text()).strip() or "once"
        elif part.name == "interval_minutes":
            try:
                fields["interval_minutes"] = max(0, int(await part.text()))
            except ValueError:
                fields["interval_minutes"] = 0
        elif part.name == "budget":
            try:
                fields["budget"] = float(await part.text())
            except ValueError:
                fields["budget"] = 0.0
        elif part.name == "media" and part.filename:
            filename = part.filename
            ext = filename.split(".")[-1].lower()
            fields["media_file_name"] = filename
            fields["media_type"] = "video" if ext in ["mp4", "mov", "avi", "mkv"] else "image"
            save_path = os.path.join(UPLOADS_DIR, f"{int(datetime.now().timestamp())}_{filename}")
            with open(save_path, "wb") as f:
                while True:
                    chunk = await part.read_chunk()
                    if not chunk:
                        break
                    f.write(chunk)
            fields["media_file_path"] = save_path
    return fields


async def handle_ads_campaign_create(request):
    f = await _parse_campaign_multipart(request)
    if not f["name"] or not f["content"]:
        return web.json_response({"error": "Vui lòng nhập tên lịch đăng và nội dung tin nhắn!"}, status=400)

    scheduled_datetime = None
    if f["start_date"] and f["start_time"]:
        scheduled_datetime = f"{f['start_date']} {f['start_time']}:00"
    elif f["status"] == "SCHEDULED":
        scheduled_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO campaigns (name, description, content, destination_link, target_channels, status, budget)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (f["name"], f["description"], f["content"], f["destination_link"], f["target_channels"], f["status"], f["budget"]))
        campaign_id = cursor.lastrowid

        if f["media_file_path"]:
            cursor.execute("""
                INSERT INTO campaign_media (campaign_id, media_type, file_path, file_name)
                VALUES (?, ?, ?, ?)
            """, (campaign_id, f["media_type"], f["media_file_path"], f["media_file_name"]))

        if scheduled_datetime:
            cursor.execute("""
                INSERT INTO campaign_schedule (campaign_id, start_time, schedule_mode, interval_minutes)
                VALUES (?, ?, ?, ?)
            """, (campaign_id, scheduled_datetime, f["schedule_mode"], f["interval_minutes"]))

        conn.commit()
        log_action(campaign_id, "CREATE", f"Tạo lịch đăng '{f['name']}' trạng thái {f['status']}", "SUCCESS")
        return web.json_response({"status": "created", "campaign_id": campaign_id})
    finally:
        conn.close()


async def handle_ads_campaign_update(request):
    campaign_id = int(request.match_info["id"])
    f = await _parse_campaign_multipart(request)
    if not f["name"] or not f["content"]:
        return web.json_response({"error": "Vui lòng nhập tên lịch đăng và nội dung tin nhắn!"}, status=400)

    scheduled_datetime = None
    if f["start_date"] and f["start_time"]:
        scheduled_datetime = f"{f['start_date']} {f['start_time']}:00"
    elif f["status"] == "SCHEDULED":
        scheduled_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        existing = conn.execute("SELECT id FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if not existing:
            return web.json_response({"error": "Không tìm thấy lịch đăng bài!"}, status=404)

        conn.execute("""
            UPDATE campaigns
            SET name = ?, description = ?, content = ?, destination_link = ?, target_channels = ?, status = ?, updated_at = datetime('now', 'localtime')
            WHERE id = ?
        """, (f["name"], f["description"], f["content"], f["destination_link"], f["target_channels"], f["status"], campaign_id))

        if f["media_file_path"]:
            conn.execute("DELETE FROM campaign_media WHERE campaign_id = ?", (campaign_id,))
            conn.execute("""
                INSERT INTO campaign_media (campaign_id, media_type, file_path, file_name)
                VALUES (?, ?, ?, ?)
            """, (campaign_id, f["media_type"], f["media_file_path"], f["media_file_name"]))

        if scheduled_datetime:
            sched_row = conn.execute("SELECT id FROM campaign_schedule WHERE campaign_id = ?", (campaign_id,)).fetchone()
            if sched_row:
                conn.execute("""
                    UPDATE campaign_schedule
                    SET start_time = ?, schedule_mode = ?, interval_minutes = ?, is_posted = 0
                    WHERE campaign_id = ?
                """, (scheduled_datetime, f["schedule_mode"], f["interval_minutes"], campaign_id))
            else:
                conn.execute("""
                    INSERT INTO campaign_schedule (campaign_id, start_time, schedule_mode, interval_minutes)
                    VALUES (?, ?, ?, ?)
                """, (campaign_id, scheduled_datetime, f["schedule_mode"], f["interval_minutes"]))

        conn.commit()
        log_action(campaign_id, "UPDATE", f"Cập nhật lịch đăng '{f['name']}' ({f['status']})", "INFO")
        return web.json_response({"status": "updated", "campaign_id": campaign_id})
    finally:
        conn.close()

async def handle_ads_campaign_action(request):
    campaign_id = int(request.match_info["id"])
    action = request.match_info["action"].upper()

    if action == "DELETE":
        delete_campaign(campaign_id)
        return web.json_response({"status": "deleted"})
    elif action == "START":
        # Đăng ngay lập tức
        asyncio.create_task(execute_campaign(campaign_id))
        return web.json_response({"status": "started"})
    elif action in ["PAUSE", "RESUME", "STOP"]:
        status_map = {"PAUSE": "PAUSED", "RESUME": "SCHEDULED", "STOP": "STOPPED"}
        set_campaign_status(campaign_id, status_map[action])
        return web.json_response({"status": status_map[action].lower()})

    return web.json_response({"error": "Hành động không hợp lệ"}, status=400)

async def handle_ads_history(request):
    conn = get_connection()
    try:
        search = request.query.get("search", "").strip()
        status_filter = request.query.get("status", "").strip()

        query = """
            SELECT p.*, c.name as campaign_name, s.start_time as scheduled_date
            FROM campaign_posts p
            JOIN campaigns c ON p.campaign_id = c.id
            LEFT JOIN campaign_schedule s ON c.id = s.campaign_id
            WHERE 1=1
        """
        params = []
        if search:
            query += " AND (c.name LIKE ? OR p.channel_title LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        if status_filter:
            query += " AND p.status = ?"
            params.append(status_filter)

        query += " ORDER BY p.id DESC LIMIT 100"
        rows = conn.execute(query, params).fetchall()
        return web.json_response({"history": [dict(r) for r in rows]})
    finally:
        conn.close()

async def handle_ads_calendar(request):
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT c.id, c.name, c.status, s.start_time
            FROM campaigns c
            JOIN campaign_schedule s ON c.id = s.campaign_id
            WHERE s.start_time IS NOT NULL
        """).fetchall()
        events = []
        for r in rows:
            events.append({
                "id": r["id"],
                "title": r["name"],
                "start": r["start_time"],
                "status": r["status"]
            })
        return web.json_response({"events": events})
    finally:
        conn.close()

_last_heartbeat = time.time()
_heartbeat_grace = time.time() + 90

async def handle_heartbeat(request):
    global _last_heartbeat
    _last_heartbeat = time.time()
    return web.json_response({"status": "ok", "version": CURRENT_VERSION})

async def heartbeat_watcher():
    """Tự động tắt hoàn toàn tiến trình chạy ngầm khi cửa sổ phần mềm bị tắt."""
    await asyncio.sleep(20)
    while True:
        await asyncio.sleep(5)
        # Giữ tiến trình tối đa 60 giây không có heartbeat (đủ thời gian khi người dùng mở Telegram trên điện thoại lấy mã OTP)
        if time.time() > _heartbeat_grace and (time.time() - _last_heartbeat) > 60:
            os._exit(0)

async def handle_exit(request):
    async def shutdown():
        await asyncio.sleep(0.5)
        os._exit(0)
    asyncio.create_task(shutdown())
    return web.json_response({"status": "exiting"})

def launch_desktop_window(wait_before=0.5):
    if wait_before > 0:
        time.sleep(wait_before)

    # Ưu tiên mở cửa sổ ứng dụng (App mode) không có thanh địa chỉ qua Chrome hoặc Edge
    browser_candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for b_path in browser_candidates:
        if os.path.exists(b_path):
            try:
                subprocess.Popen(
                    f'"{b_path}" --app=http://127.0.0.1:5000 --window-size=1360,880',
                    shell=True,
                    creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )
                return
            except Exception:
                pass

    try:
        os.system('start "" "http://127.0.0.1:5000"')
    except Exception:
        import webbrowser
        webbrowser.open("http://127.0.0.1:5000")

def ensure_desktop_shortcut():
    if getattr(sys, 'frozen', False):
        try:
            exe_path = sys.executable
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            shortcut_path = os.path.join(desktop, "Telegram Toolkit Pro.lnk")
            if not os.path.exists(shortcut_path):
                cmd = f'$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut("{shortcut_path}"); $s.TargetPath = "{exe_path}"; $s.WorkingDirectory = "{os.path.dirname(exe_path)}"; $s.Description = "Telegram Toolkit Pro"; $s.Save()'
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
                    shell=True,
                    creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )
        except Exception:
            pass

# --- KHỞI TẠO APP VÀ SCHEDULER ---
async def on_startup(app):
    start_scheduler()
    if IS_CLOUD:
        port = int(os.environ.get("PORT", 5000))
        print(f"[*] Telegram Toolkit Pro Cloud ready on 0.0.0.0:{port}")
        return
    ensure_desktop_shortcut()
    asyncio.create_task(heartbeat_watcher())
    print("[*] Telegram Toolkit Pro đã sẵn sàng tại http://127.0.0.1:5000")
    if "--no-window" not in sys.argv:
        threading.Thread(target=lambda: launch_desktop_window(0.8), daemon=True).start()

async def on_cleanup(app):
    stop_scheduler()

def create_app():
    app = web.Application(middlewares=[license_guard_middleware])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    # Routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/heartbeat", handle_heartbeat)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/license/status", handle_license_status)
    app.router.add_post("/api/license/activate", handle_license_activate)
    app.router.add_get("/api/update/check", handle_update_check)
    app.router.add_post("/api/update/apply", handle_update_apply)
    app.router.add_get("/api/channels", handle_channels)
    app.router.add_get("/api/my_groups", handle_my_groups)
    app.router.add_post("/api/exit", handle_exit)

    # Multi Accounts
    app.router.add_get("/api/accounts", handle_accounts_list)
    app.router.add_post("/api/accounts/switch", handle_accounts_switch)
    app.router.add_post("/api/accounts/send_code", handle_accounts_send_code)
    app.router.add_post("/api/accounts/verify_code", handle_accounts_verify_code)
    app.router.add_post("/api/accounts/delete", handle_accounts_delete)
    app.router.add_get("/api/accounts/export_sessions", handle_accounts_export_sessions)
    app.router.add_post("/api/accounts/restore_sessions", handle_accounts_restore_sessions)

    # Member Filter
    app.router.add_post("/api/scrape/start", handle_scrape_start)
    app.router.add_post("/api/scrape/stop", handle_scrape_stop)
    app.router.add_get("/api/scrape/status", handle_scrape_status)
    app.router.add_get("/api/scrape/history/stats", handle_scrape_history_stats)
    app.router.add_post("/api/scrape/history/clear", handle_scrape_history_clear)
    app.router.add_get("/api/scrape/export/{format}", handle_scrape_export)
    app.router.add_post("/api/scrape/send_tg", handle_scrape_send_tg)

    # Ads Manager
    app.router.add_get("/api/ads/dashboard", handle_ads_dashboard)
    app.router.add_get("/api/ads/campaigns", handle_ads_campaigns_list)
    app.router.add_post("/api/ads/campaigns", handle_ads_campaign_create)
    app.router.add_post("/api/ads/campaigns/{id}/update", handle_ads_campaign_update)
    app.router.add_post("/api/ads/campaigns/{id}/{action}", handle_ads_campaign_action)
    app.router.add_get("/api/ads/history", handle_ads_history)
    app.router.add_get("/api/ads/calendar", handle_ads_calendar)

    # Static files
    app.router.add_static("/static", STATIC_DIR)
    app.router.add_static("/uploads", UPLOADS_DIR)

    return app

try:
    import msvcrt
except ImportError:
    msvcrt = None

_lock_file = None
def acquire_single_instance_lock():
    """Đảm bảo chỉ có DUY NHẤT 1 cửa sổ phần mềm chạy, chống mở đúp 2 cửa sổ."""
    if IS_CLOUD or msvcrt is None:
        return True
    global _lock_file
    lock_path = os.path.join(BASE_DIR, ".app.lock")
    try:
        _lock_file = open(lock_path, "w")
        msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except Exception:
        return False

def show_error_popup(title, message):
    """Hiện popup lỗi khi chạy ở chế độ --noconsole (EXE)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception:
        pass

if __name__ == "__main__":
    if not acquire_single_instance_lock():
        # Đã có phiên chạy ngầm, chỉ cần mở lại cửa sổ giao diện lên ngay lập tức!
        launch_desktop_window(wait_before=0)
        sys.exit(0)

    shutil.rmtree(UPDATE_DIR, ignore_errors=True)

    try:
        host = "0.0.0.0" if IS_CLOUD else "127.0.0.1"
        port = int(os.environ.get("PORT", 5000)) if IS_CLOUD else 5000
        web.run_app(create_app(), host=host, port=port)
    except OSError as e:
        if "10048" in str(e):
            # Port 5000 đã có instance cũ chạy rồi → mở thẳng giao diện thay vì báo lỗi
            launch_desktop_window(wait_before=0)
            sys.exit(0)
        else:
            show_error_popup("Telegram Toolkit Pro - Lỗi", f"Lỗi mạng: {str(e)}\n\nLiên hệ hỗ trợ: @hoang1793")
            with open(os.path.join(BASE_DIR, "error.log"), "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now()}] Lỗi khởi động: {str(e)}\n")
    except Exception as e:
        err_msg = f"Lỗi khởi động phần mềm:\n{str(e)}\n\nLiên hệ hỗ trợ: @hoang1793"
        show_error_popup("Telegram Toolkit Pro - Lỗi", err_msg)
        with open(os.path.join(BASE_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] Lỗi khởi động: {str(e)}\n")
