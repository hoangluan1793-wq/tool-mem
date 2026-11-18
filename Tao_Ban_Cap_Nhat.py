import os, sys, json, hashlib, zipfile

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from core.licensing import sign_update

OUTPUT_DIR = os.path.join(BASE_DIR, "Xuat_Ban_Cho_Khach")
ZIP_FILE = os.path.join(OUTPUT_DIR, "Telegram_Toolkit_Pro_Client.zip")
OUT_FILE = os.path.join(OUTPUT_DIR, "version_remote.json")


def main():
    if not os.path.exists(ZIP_FILE):
        print("Chua co file zip. Hay chay Dong_Goi_Ban_Cho_Khach.bat truoc!")
        return 1

    url = sys.argv[1].strip() if len(sys.argv) > 1 else input("Link tai file zip (https://...): ").strip()
    if not url.lower().startswith("https://"):
        print("Link tai phai bat dau bang https://")
        return 1

    with zipfile.ZipFile(ZIP_FILE) as zf:
        names = sorted((n for n in zf.namelist() if n.endswith("version.json")), key=lambda n: n.count("/"))
        if not names:
            print("Trong file zip khong co version.json!")
            return 1
        info = json.loads(zf.read(names[0]).decode("utf-8-sig"))

    version = str(info.get("version", "")).strip()
    if not version:
        print("version.json trong zip thieu truong version!")
        return 1

    digest = hashlib.sha256()
    with open(ZIP_FILE, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    sha256 = digest.hexdigest()

    data = {
        "version": version,
        "changelog": str(info.get("changelog", "")).strip(),
        "download_url": url,
        "sha256": sha256,
        "sig": sign_update(version, sha256),
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    print("=" * 60)
    print(f"Phien ban : {version}")
    print(f"SHA256    : {sha256}")
    print(f"File xuat : {OUT_FILE}")
    print("=" * 60)
    print("BUOC CUOI: mo file tren, copy toan bo noi dung dan de len Gist (thay noi dung cu).")
    print("Khach mo tool se tu thay nut cap nhat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
