import os
import sys
import uuid
import hashlib
import base64
import json
import subprocess
from datetime import datetime, timedelta
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization, hashes

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LICENSE_FILE = os.path.join(BASE_DIR, "license.key")
PRIVATE_KEY_PATH = os.path.join(BASE_DIR, "admin_private.pem")
TIME_GUARD_FILE = os.path.join(BASE_DIR, ".sys_time.dat")

def _verify_and_update_clock():
    """Chống gian lận lùi ngày giờ hệ thống Windows để kéo dài thời hạn bản quyền."""
    now_ts = int(datetime.now().timestamp())
    salt = "hluan_clock_guard_2026"
    if os.path.exists(TIME_GUARD_FILE):
        try:
            with open(TIME_GUARD_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
            saved_ts_str, sig = content.split(":")
            saved_ts = int(saved_ts_str)
            expected_sig = hashlib.sha256(f"{saved_ts_str}_{salt}".encode()).hexdigest()[:16]
            if sig == expected_sig:
                if now_ts < (saved_ts - 300):
                    return False, "Phát hiện thời gian hệ thống bị chỉnh lùi. Vui lòng đồng bộ lại giờ chuẩn Windows!"
                now_ts = max(now_ts, saved_ts)
        except Exception:
            pass

    sig = hashlib.sha256(f"{now_ts}_{salt}".encode()).hexdigest()[:16]
    try:
        with open(TIME_GUARD_FILE, "w", encoding="utf-8") as f:
            f.write(f"{now_ts}:{sig}")
    except Exception:
        pass
    return True, None

# Khóa công khai RSA (được nhúng để kiểm tra hợp lệ, không thể dùng để tạo key)
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAkIcGB6zMFYExL9N2cvRX
pQQF0xVIMsceBhQD1AJWm8OvLYIOPx9JfIM1mNsjAIp6kBX4avtunlVwta6PsAMO
6fqvqLJ3K3bcCvta3ujepboyg4CsfPvOFpabQdbKc3mQl9YdJ5nPfQTtp/z63URd
F+RKqLcR3OY1EjVYjp/YyeY5eSomoD7pW0n/+wVJLy1u99ktzB9G5e6gPGgceZN4
QiROCgXgQVvzZVIKFkYanNIhYD/P4wWj2vkjFea2HlK0gDuJdYiBY+yY1mzSKGYt
S2Qy6CyTAh6kWbk3w3VvnQWXpd3Ua++WhTaDquRGHnQ01bRj2Wb53Fzi//wdb9fQ
bwIDAQAB
-----END PUBLIC KEY-----"""

def get_hwid():
    """Tạo mã định danh duy nhất cho từng máy tính (HWID)."""
    raw_id = ""
    try:
        cmd = 'powershell "(Get-CimInstance -Class Win32_ComputerSystemProduct).UUID"'
        output = subprocess.check_output(cmd, shell=True).decode().strip()
        if output and len(output) > 10:
            raw_id = output
    except Exception:
        pass

    if not raw_id:
        raw_id = str(uuid.getnode())

    hashed = hashlib.sha256((raw_id + "hluan_secure_hwid_v2").encode()).hexdigest().upper()
    return f"HL-{hashed[:4]}-{hashed[4:8]}-{hashed[8:12]}-{hashed[12:16]}"

def generate_license_key(hwid, days):
    """Tạo mã kích hoạt bằng chữ ký số RSA 2048-bit (CHỈ CHẠY ĐƯỢC TRÊN MÁY CHỦ CÓ PRIVATE KEY)."""
    if not os.path.exists(PRIVATE_KEY_PATH):
        raise Exception("Lỗi: Máy tính này không phải là Máy Chủ Admin (Không có khóa bí mật admin_private.pem)!")

    with open(PRIVATE_KEY_PATH, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    hwid = hwid.strip().upper()
    expiry_date = datetime.now() + timedelta(days=int(days))
    expiry_str = expiry_date.strftime("%Y-%m-%d %H:%M:%S")

    payload = f"{hwid}|{expiry_str}".encode("utf-8")
    signature = private_key.sign(
        payload,
        padding.PKCS1v15(),
        hashes.SHA256()
    )

    full_data = {
        "hwid": hwid,
        "expiry": expiry_str,
        "sig": base64.b64encode(signature).decode("utf-8")
    }

    encoded_key = base64.b64encode(json.dumps(full_data).encode("utf-8")).decode("utf-8")
    return encoded_key, expiry_str

def parse_version(v):
    """'1.2.3' -> (1, 2, 3). Phần không phải số coi như 0."""
    parts = []
    for p in str(v).strip().lstrip("vV").split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)

def _update_payload(version, sha256):
    return f"UPDATE|{str(version).strip()}|{str(sha256).strip().lower()}".encode("utf-8")

def sign_update(version, sha256):
    """Ký bản cập nhật (CHỈ chạy được trên máy admin có admin_private.pem)."""
    if not os.path.exists(PRIVATE_KEY_PATH):
        raise Exception("Máy này không có khóa bí mật admin_private.pem!")
    with open(PRIVATE_KEY_PATH, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)
    sig = private_key.sign(_update_payload(version, sha256), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode("utf-8")

def verify_update_signature(version, sha256, sig_b64):
    """Kiểm tra bản cập nhật có đúng do admin ký không."""
    try:
        public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM.encode("utf-8"))
        public_key.verify(
            base64.b64decode(sig_b64.encode("utf-8")),
            _update_payload(version, sha256),
            padding.PKCS1v15(),
            hashes.SHA256()
        )
        return True
    except Exception:
        return False

def verify_license():
    """Xác thực mã kích hoạt bằng Khóa công khai RSA."""
    if not os.path.exists(LICENSE_FILE):
        return False, "Thiết bị chưa được kích hoạt bản quyền", None

    try:
        with open(LICENSE_FILE, "r", encoding="utf-8") as f:
            encoded_key = f.read().strip()

        data = json.loads(base64.b64decode(encoded_key.encode("utf-8")).decode("utf-8"))
        hwid = data.get("hwid")
        expiry_str = data.get("expiry")
        sig_bytes = base64.b64decode(data.get("sig").encode("utf-8"))

        current_hwid = get_hwid()
        if hwid != current_hwid:
            return False, "Mã bản quyền không khớp với thiết bị này!", None

        # Kiểm tra chữ ký RSA với Public Key
        public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM.encode("utf-8"))
        payload = f"{hwid}|{expiry_str}".encode("utf-8")

        public_key.verify(
            sig_bytes,
            payload,
            padding.PKCS1v15(),
            hashes.SHA256()
        )

        # Kiểm tra chống lùi giờ Windows
        ok_clock, clock_err = _verify_and_update_clock()
        if not ok_clock:
            return False, clock_err, None

        # Kiểm tra thời hạn
        expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
        if datetime.now() > expiry_date:
            return False, f"Bản quyền đã hết hạn vào {expiry_str}", expiry_str

        return True, "Hợp lệ", expiry_str

    except Exception:
        return False, "Mã kích hoạt không hợp lệ hoặc đã bị chỉnh sửa!", None

def apply_license(encoded_key):
    """Lưu và kích hoạt mã bản quyền."""
    encoded_key = encoded_key.strip()
    try:
        data = json.loads(base64.b64decode(encoded_key.encode("utf-8")).decode("utf-8"))
        hwid = data.get("hwid")
        expiry_str = data.get("expiry")
        sig_bytes = base64.b64decode(data.get("sig").encode("utf-8"))

        current_hwid = get_hwid()
        if hwid != current_hwid:
            return False, "Mã kích hoạt không khớp với thiết bị này!"

        public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM.encode("utf-8"))
        payload = f"{hwid}|{expiry_str}".encode("utf-8")

        public_key.verify(
            sig_bytes,
            payload,
            padding.PKCS1v15(),
            hashes.SHA256()
        )

        expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
        # Kiểm tra chống lùi giờ Windows
        ok_clock, clock_err = _verify_and_update_clock()
        if not ok_clock:
            return False, clock_err

        if datetime.now() > expiry_date:
            return False, "Mã kích hoạt này đã hết hạn sử dụng!"

        with open(LICENSE_FILE, "w", encoding="utf-8") as f:
            f.write(encoded_key)

        return True, f"Kích hoạt thành công! Hạn sử dụng đến: {expiry_str}"

    except Exception:
        return False, "Mã kích hoạt không hợp lệ!"
