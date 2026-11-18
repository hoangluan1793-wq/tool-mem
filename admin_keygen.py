import os
import sys

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import subprocess
import threading
import time
import base64
import json
from datetime import datetime
from aiohttp import web
from core.licensing import generate_license_key, get_hwid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

HTML_CONTENT = """<!DOCTYPE html>
<html lang="vi" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trình Quản Lý Bản Quyền - Admin Key Generator</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        body {
            background-color: #0a0b14;
            background-image: 
                radial-gradient(at 0% 0%, rgba(168, 85, 247, 0.2) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(59, 130, 246, 0.2) 0px, transparent 50%);
            min-height: 100vh;
            color: #e2e8f0;
            font-family: system-ui, -apple-system, sans-serif;
            user-select: none;
        }
        .glass-card {
            background: rgba(18, 20, 36, 0.7);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.4);
        }
        .neon-glow-purple {
            box-shadow: 0 0 20px rgba(168, 85, 247, 0.4);
        }
        .neon-glow-blue {
            box-shadow: 0 0 20px rgba(59, 130, 246, 0.4);
        }
    </style>
</head>
<body class="flex flex-col items-center justify-center min-h-screen p-6 relative">

    <div id="toast" class="fixed top-5 right-5 z-50 hidden p-3 rounded-xl bg-emerald-950/90 border border-emerald-500/50 text-emerald-200 text-xs shadow-2xl backdrop-blur-md transition-all"></div>

    <div class="glass-card max-w-xl w-full rounded-2xl p-7 border border-borderglass space-y-6 relative overflow-hidden">
        
        <!-- Header -->
        <div class="flex items-center justify-between border-b border-white/5 pb-4">
            <div class="flex items-center gap-3">
                <div class="w-11 h-11 rounded-xl bg-gradient-to-tr from-purple-600 to-blue-600 flex items-center justify-center neon-glow-purple">
                    <i data-lucide="key" class="w-5 h-5 text-white"></i>
                </div>
                <div>
                    <h1 class="text-lg font-bold text-white">TẠO MÃ KÍCH HOẠT BẢN QUYỀN</h1>
                    <p class="text-xs text-purple-300">Công cụ dành riêng cho Admin quản lý cho thuê</p>
                </div>
            </div>
            <button onclick="exitApp()" title="Đóng phần mềm" class="p-2 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 text-xs transition-all">
                <i data-lucide="power" class="w-4 h-4"></i>
            </button>
        </div>

        <!-- Form tạo mã -->
        <div class="space-y-4">
            <div>
                <label class="block text-xs font-semibold text-gray-300 mb-1.5 flex items-center gap-1.5">
                    <i data-lucide="laptop" class="w-3.5 h-3.5 text-neonblue"></i> Dán Mã máy tính (HWID) của khách hàng:
                </label>
                <input type="text" id="input-hwid" placeholder="ví dụ: HL-C0F1-CC85-1033-A9F1" class="w-full bg-black/50 border border-white/10 rounded-xl px-3.5 py-2.5 text-sm text-emerald-400 font-mono font-bold focus:outline-none focus:border-neonpurple">
            </div>

            <div>
                <label class="block text-xs font-semibold text-gray-300 mb-1.5 flex items-center gap-1.5">
                    <i data-lucide="clock" class="w-3.5 h-3.5 text-neonpurple"></i> Thời hạn sử dụng:
                </label>
                <div class="grid grid-cols-4 gap-2 mb-2">
                    <button type="button" onclick="setDays(7)" class="py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-xs font-medium text-gray-300 border border-white/5">7 Ngày</button>
                    <button type="button" onclick="setDays(30)" class="py-1.5 rounded-lg bg-neonpurple/20 hover:bg-neonpurple/30 text-xs font-semibold text-purple-200 border border-neonpurple/30">30 Ngày</button>
                    <button type="button" onclick="setDays(90)" class="py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-xs font-medium text-gray-300 border border-white/5">90 Ngày</button>
                    <button type="button" onclick="setDays(365)" class="py-1.5 rounded-lg bg-neonblue/20 hover:bg-neonblue/30 text-xs font-semibold text-blue-200 border border-neonblue/30">365 Ngày</button>
                </div>
                <div class="flex items-center gap-2">
                    <input type="number" id="input-days" value="30" min="1" class="w-32 bg-black/50 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-neonpurple">
                    <span class="text-xs text-gray-400">ngày</span>
                </div>
            </div>

            <button onclick="createKey()" class="w-full py-3 rounded-xl bg-gradient-to-r from-purple-600 to-blue-600 text-white font-bold text-xs uppercase tracking-wider neon-glow-purple hover:opacity-95 transition-all flex items-center justify-center gap-2">
                <i data-lucide="sparkles" class="w-4 h-4"></i> Tạo Mã Kích Hoạt Ngay
            </button>
        </div>

        <!-- Kết quả tạo mã -->
        <div id="result-box" class="hidden p-4 rounded-xl bg-black/70 border border-neonpurple/40 space-y-3">
            <div class="flex items-center justify-between">
                <span class="text-xs font-bold text-emerald-400 flex items-center gap-1.5">
                    <i data-lucide="check-circle" class="w-4 h-4 text-emerald-400"></i> Mã kích hoạt đã tạo thành công!
                </span>
                <span id="result-expiry" class="text-[11px] text-gray-400 font-mono"></span>
            </div>

            <div class="relative">
                <textarea id="output-key" rows="2" readonly class="w-full bg-black/80 border border-white/10 rounded-lg p-2.5 text-xs text-purple-200 font-mono break-all focus:outline-none"></textarea>
            </div>

            <button onclick="copyGeneratedKey()" id="btn-copy" class="w-full py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold transition-all flex items-center justify-center gap-2 shadow-lg">
                <i data-lucide="copy" class="w-3.5 h-3.5"></i> Sao chép mã gửi cho khách
            </button>
        </div>

    </div>

    <script>
        lucide.createIcons();

        function setDays(days) {
            document.getElementById('input-days').value = days;
        }

        function showToastMsg(msg) {
            const t = document.getElementById('toast');
            t.innerText = msg;
            t.classList.remove('hidden');
            setTimeout(() => t.classList.add('hidden'), 2500);
        }

        async function createKey() {
            const hwid = document.getElementById('input-hwid').value.trim();
            const days = document.getElementById('input-days').value.trim();

            if (!hwid) return alert('Vui lòng dán Mã máy tính (HWID) của khách!');
            if (!days || parseInt(days) <= 0) return alert('Vui lòng nhập số ngày hợp lệ!');

            try {
                const res = await fetch('/api/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ hwid, days: parseInt(days) })
                });
                const data = await res.json();
                if (data.key) {
                    document.getElementById('output-key').value = data.key;
                    document.getElementById('result-expiry').innerText = 'Hạn dùng: ' + data.expiry;
                    document.getElementById('result-box').classList.remove('hidden');
                    lucide.createIcons();
                } else {
                    alert(data.error || 'Lỗi khi tạo mã');
                }
            } catch (e) {
                alert(e.message);
            }
        }

        function copyGeneratedKey() {
            const text = document.getElementById('output-key').value;
            if (!text) return;
            navigator.clipboard.writeText(text);
            const btn = document.getElementById('btn-copy');
            btn.innerHTML = '<i data-lucide="check" class="w-3.5 h-3.5"></i> Đã sao chép vào bộ nhớ tạm!';
            lucide.createIcons();
            showToastMsg('Đã sao chép mã kích hoạt!');
            setTimeout(() => {
                btn.innerHTML = '<i data-lucide="copy" class="w-3.5 h-3.5"></i> Sao chép mã gửi cho khách';
                lucide.createIcons();
            }, 2000);
        }

        async function exitApp() {
            try {
                await fetch('/api/exit', { method: 'POST' });
            } catch (e) {}
            window.close();
        }
    </script>
</body>
</html>
"""

async def handle_index(request):
    return web.Response(text=HTML_CONTENT, content_type="text/html")

async def handle_generate(request):
    body = await request.json()
    hwid = body.get("hwid", "").strip()
    days = body.get("days", 30)
    if not hwid:
        return web.json_response({"error": "Chưa có mã HWID"}, status=400)
    key, expiry_str = generate_license_key(hwid, days)
    return web.json_response({"key": key, "expiry": expiry_str})

async def handle_exit(request):
    async def shutdown():
        await asyncio.sleep(0.5)
        os._exit(0)
    asyncio.create_task(shutdown())
    return web.json_response({"status": "exiting"})

def launch_admin_window(wait_before=0.5):
    if wait_before > 0:
        time.sleep(wait_before)
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if os.path.exists(chrome_path):
        try:
            subprocess.Popen(f'"{chrome_path}" --app=http://127.0.0.1:5001 --window-size=680,680', shell=True)
            return
        except Exception:
            pass
    try:
        os.system('start "" "http://127.0.0.1:5001"')
    except Exception:
        import webbrowser
        webbrowser.open("http://127.0.0.1:5001")

async def on_startup(app):
    threading.Thread(target=lambda: launch_admin_window(0.7), daemon=True).start()

import msvcrt

_lock_file = None
def acquire_single_instance_lock():
    global _lock_file
    lock_path = os.path.join(BASE_DIR, ".keygen.lock")
    try:
        _lock_file = open(lock_path, "w")
        msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except Exception:
        return False

def main():
    if not acquire_single_instance_lock():
        launch_admin_window(wait_before=0)
        sys.exit(0)

    app = web.Application()
    app.on_startup.append(on_startup)
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/generate", handle_generate)
    app.router.add_post("/api/exit", handle_exit)
    try:
        web.run_app(app, host="127.0.0.1", port=5001)
    except Exception as e:
        with open(os.path.join(BASE_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] Keygen Error: {str(e)}\n")

if __name__ == "__main__":
    main()
