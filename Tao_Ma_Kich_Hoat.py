import sys
import os

# Thêm thư mục gốc vào path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.licensing import generate_license_key

def main():
    print("=" * 60)
    print("   TOOL TẠO MÃ KÍCH HOẠT BẢN QUYỀN - TELEGRAM TOOLKIT PRO    ")
    print("   (Dành riêng cho Admin / Chủ tool cho thuê)                ")
    print("=" * 60)
    print()

    while True:
        hwid = input("1. Dán MÃ MÁY TÍNH của khách hàng: ").strip()
        if not hwid:
            print("[!] Mã máy tính không được để trống!\n")
            continue

        days_input = input("2. Bạn muốn cho thiết bị này sử dụng trong vòng bao nhiêu NGÀY (vd: 3, 7, 30, 365): ").strip()
        try:
            days = int(days_input)
            if days <= 0:
                print("[!] Số ngày phải lớn hơn 0!\n")
                continue
        except ValueError:
            print("[!] Vui lòng nhập số ngày là số nguyên!\n")
            continue

        key, expiry_str = generate_license_key(hwid, days)

        print("\n" + "-" * 60)
        print(">>> TẠO MÃ KÍCH HOẠT THÀNH CÔNG! <<<")
        print(f"Mã máy tính (HWID): {hwid}")
        print(f"Thời hạn sử dụng:   {days} ngày (Hết hạn vào: {expiry_str})")
        print("\nMÃ KÍCH HOẠT (Gửi chuỗi mã này cho khách hàng):")
        print("=" * 60)
        print(key)
        print("=" * 60)

        choice = input("\nBạn có muốn tạo tiếp mã cho khách khác không? (y/N): ").strip().lower()
        if choice not in ['y', 'yes']:
            break

if __name__ == "__main__":
    main()
