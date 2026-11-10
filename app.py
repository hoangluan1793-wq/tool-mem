import os
import sys
import json
import asyncio
import csv
import socket
import subprocess
import threading
import time
from datetime import datetime
from aiohttp import web

from core.telegram_client import (
    get_telegram_client, check_auth, get_my_groups, scrape_group_members,
    get_accounts_list, switch_account, start_login_request, complete_login_request, delete_saved_account
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(BASE_DIR, "web", "index.html")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

PORT = int(os.environ.get("PORT", 5001))

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


async def handle_index(request):
    if os.path.exists(INDEX_HTML):
        with open(INDEX_HTML, "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    return web.Response(text="Index HTML not found", status=404)


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
