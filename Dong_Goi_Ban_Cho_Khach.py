import os
import sys
import shutil
import zipfile
import subprocess

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "Xuat_Ban_Cho_Khach")
BUILD_DIR = os.path.join(OUTPUT_DIR, "Telegram_Toolkit_Pro")
ZIP_FILE = os.path.join(OUTPUT_DIR, "Telegram_Toolkit_Pro_Client.zip")
PYINSTALLER_TEMP = os.path.join(OUTPUT_DIR, "_pyinstaller_temp")

def update_embedded_html():
    print("[0/4] Dang dong goi bao mat giao dien vao bo nho Python...")
    import zlib
    import base64
    html_path = os.path.join(BASE_DIR, "web", "index.html")
    out_path = os.path.join(BASE_DIR, "core", "embedded_web.py")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            raw = f.read()
        comp = zlib.compress(raw.encode("utf-8"), level=9)
        b64 = base64.b64encode(comp).decode("ascii")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("# -*- coding: utf-8 -*-\n")
            f.write('"""Embedded secure HTML resource."""\n')
            f.write(f'EMBEDDED_HTML_B64 = "{b64}"\n')

def clean_old():
    update_embedded_html()
    print("[1/4] Dang don dep ban build cu...")
    for p in [BUILD_DIR, PYINSTALLER_TEMP]:
        if os.path.exists(p):
            shutil.rmtree(p, ignore_errors=True)
    if os.path.exists(ZIP_FILE):
        try:
            os.remove(ZIP_FILE)
        except Exception:
            pass
    # Xoa chrome_profile rac neu co trong thu muc web
    cp_path = os.path.join(BASE_DIR, "web", "chrome_profile")
    if os.path.exists(cp_path):
        shutil.rmtree(cp_path, ignore_errors=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def run_pyinstaller():
    print("[2/4] Dang bien dich thanh file .EXE chuyen nghiep (PyInstaller)...")
    print("      (Qua trinh nay mat khoang 30-60 giay, vui long doi...)")

    icon_path = os.path.join(BASE_DIR, "icons", "toolkit.ico")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconsole",
        "--onedir",
        "--name=Telegram_Toolkit_Pro",
        f"--distpath={OUTPUT_DIR}",
        f"--workpath={PYINSTALLER_TEMP}",
        f"--specpath={PYINSTALLER_TEMP}",
        "--clean",
        "--hidden-import=telethon",
        "--hidden-import=aiohttp",
        "--hidden-import=cryptography",
        "--hidden-import=sqlite3",
        "--hidden-import=msvcrt",
        "--hidden-import=qrcode",
        "--hidden-import=PIL",
        os.path.join(BASE_DIR, "app.py")
    ]
    if os.path.exists(icon_path):
        cmd.insert(4, f"--icon={icon_path}")

    res = subprocess.run(cmd, cwd=BASE_DIR)
    if res.returncode != 0:
        print("\n[LOI] PyInstaller bien dich that bai!")
        sys.exit(1)

def finalize_package():
    print("[3/4] Dang tao file huong dan va cau hinh sach cho khach...")
    
    # 1. Config sach khong chua thong tin rieng tu
    clean_config = {"accounts": []}
    with open(os.path.join(BUILD_DIR, "config.json"), "w", encoding="utf-8") as f:
        import json
        json.dump(clean_config, f, indent=4)

    # 2. Version file
    ver_src = os.path.join(BASE_DIR, "version.json")
    if os.path.exists(ver_src):
        shutil.copy2(ver_src, os.path.join(BUILD_DIR, "version.json"))

    # 3. Huong dan su dung Notepad
    huong_dan = """HUONG DAN SU DUNG TELEGRAM TOOLKIT PRO

Buoc 1: Giai nen file zip nay ra mot thu muc bat ky (vi du: O dia D hoac Desktop).
Buoc 2: Bam dup vao file "Telegram_Toolkit_Pro.exe" de mo phan mem.
        (Tool se tu dong tao loi tat Shortcut ngoai Desktop de lan sau mo cho tien).
Buoc 3: Copy ma thiet bi (HWID) hien tren man hinh roi gui qua Telegram: @hoang1793 de nhan ma kich hoat.
Buoc 4: Dan ma vao o kich hoat roi bam "Xac nhan" la xong!

* LUU Y:
- May tinh KHONG CAN cai Python, khong can go bat ky lenh nao.
- Mo phan mem truc tiep bang file Telegram_Toolkit_Pro.exe.

Ho tro ky thuat: Telegram @hoang1793
"""
    with open(os.path.join(BUILD_DIR, "HUONG_DAN.txt"), "w", encoding="utf-8-sig") as f:
        f.write(huong_dan.replace("\n", "\r\n"))

    # Xóa triệt để các thư mục rác nếu có lỡ lọt vào
    for trash in ["chrome_profile", "admin_profile", "uploads", ".app.lock", "error.log"]:
        for root, dirs, files in os.walk(BUILD_DIR):
            for d in list(dirs):
                if d == trash:
                    shutil.rmtree(os.path.join(root, d), ignore_errors=True)
            for f in list(files):
                if f == trash:
                    try:
                        os.remove(os.path.join(root, f))
                    except Exception:
                        pass

    # Don dep thu muc temp cua PyInstaller
    if os.path.exists(PYINSTALLER_TEMP):
        shutil.rmtree(PYINSTALLER_TEMP, ignore_errors=True)

def create_zip():
    print("[4/4] Dang nen file ZIP de gui cho khach...")
    with zipfile.ZipFile(ZIP_FILE, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(BUILD_DIR):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, OUTPUT_DIR)
                zipf.write(full_path, rel_path)

def main():
    print("=" * 60)
    print("   DONG GOI PHAN MEM THANH FILE .EXE CHO KHACH (STANDALONE)   ")
    print("=" * 60)
    print()
    clean_old()
    run_pyinstaller()
    finalize_package()
    create_zip()
    print("\n" + "=" * 60)
    print(">>> DONG GOI THANH CONG! <<<")
    print(f"1. Thu muc phan mem: {BUILD_DIR}")
    print(f"2. File chay chinh:  {os.path.join(BUILD_DIR, 'Telegram_Toolkit_Pro.exe')}")
    print(f"3. File ZIP gui khach: {ZIP_FILE}")
    print("=" * 60)
    print("\nTRAI NGHIEM KHACH HANG:")
    print("- KHONG can cai Python.")
    print("- KHONG hien man hinh den CMD.")
    print("- Bam dup vao 'Telegram_Toolkit_Pro.exe' la mo thang phan mem.")
    print()

if __name__ == "__main__":
    main()
