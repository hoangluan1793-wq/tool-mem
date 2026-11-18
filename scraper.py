import os
import sys
import json
import asyncio
import html
import csv
import re
from datetime import datetime

try:
    from telethon import TelegramClient, errors
    from telethon.tl.functions.messages import ImportChatInviteRequest
    from telethon.tl.types import (
    ChannelParticipantsSearch,
    UserStatusOnline,
    UserStatusOffline,
    UserStatusRecently,
    UserStatusLastWeek,
    UserStatusLastMonth
)
except ImportError:
    print("=" * 60)
    print("CHƯA CÀI ĐẶT THƯ VIỆN TELETHON!")
    print("Vui lòng chạy lệnh sau trong cmd / terminal:")
    print("pip install telethon")
    print("=" * 60)
    sys.exit(1)

CONFIG_FILE = "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def format_phone(phone_input):
    digits = re.sub(r'\D', '', str(phone_input))
    if digits.startswith('0'):
        return '+84' + digits[1:]
    elif digits.startswith('84'):
        return '+' + digits
    return '+' + digits

def get_credentials():
    config = load_config()
    if config and config.get("api_id") and config.get("api_hash") and config.get("phone"):
        config['phone'] = format_phone(config.get('phone', ''))
        print(f"[*] Đã nhận cấu hình tài khoản: {config['phone']}")
        choice = input("Bạn có muốn dùng thông tin này không? (Y/n): ").strip().lower()
        if choice not in ['n', 'no']:
            save_config(config)
            return config

    print("\n--- NHẬP THÔNG TIN TELEGRAM API (Lấy từ https://my.telegram.org) ---")
    api_id = input("Nhập API ID: ").strip()
    api_hash = input("Nhập API HASH: ").strip()
    phone_input = input("Nhập Số điện thoại (bắt đầu bằng 0, vd: 0338047294): ").strip()
    clean_phone = format_phone(phone_input)

    try:
        api_id_int = int(api_id)
    except ValueError:
        print("[!] API ID phải là số!")
        sys.exit(1)

    config = {
        "api_id": api_id_int,
        "api_hash": api_hash,
        "phone": clean_phone
    }
    save_config(config)
    print(f"[*] Đã lưu thông tin cấu hình vào file config.json (Số: {clean_phone})\n")
    return config

async def resolve_target(client, target_input):
    target_input = target_input.strip()
    if target_input.lower() in ['me', 'saved', 'self', '']:
        return 'me'
    try:
        return await client.get_entity(target_input)
    except Exception as e:
        print(f"[!] Lỗi khi tìm kênh/chat đích: {e}")
        return None

from datetime import datetime, timezone

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

async def scrape_members(client, source_group, filter_days=None, only_online=False):
    print(f"\n[*] Đang kết nối tới nhóm: {source_group}...")
    try:
        # Nếu là link join dạng https://t.me/+... hoặc https://t.me/joinchat/...
        if "t.me/+" in source_group or "t.me/joinchat/" in source_group:
            invite_hash = source_group.split("+")[-1].split("joinchat/")[-1].strip()
            try:
                await client(ImportChatInviteRequest(invite_hash))
                print("[*] Đã tự động tham gia nhóm kín thành công!")
            except errors.UserAlreadyParticipantError:
                pass
            except Exception as e:
                print(f"[-] Thông báo join link: {e}")

        entity = await client.get_entity(source_group)
    except Exception as e:
        print(f"[!] Không thể truy cập nhóm '{source_group}'. Lỗi: {e}")
        return None

    print("[*] Đang tiến hành quét và lọc thành viên...")
    members = []
    seen_ids = set()

    try:
        async for user in client.iter_participants(entity, aggressive=True):
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
            full_name = f"{first} {last}".strip()
            if not full_name:
                full_name = "Người dùng ẩn danh"

            members.append({
                "id": user.id,
                "first_name": first,
                "last_name": last,
                "full_name": full_name,
                "username": user.username or "",
                "phone": user.phone or "",
                "status": status_str,
                "profile_link": f"tg://user?id={user.id}"
            })
    except errors.ChatAdminRequiredError:
        print("[!] Nhóm này đã BẬT tính năng ẨN THÀNH VIÊN (Admin ẩn danh sách).")
        print("[!] Chỉ Admin nhóm mới có thể xem toàn bộ.")
        return []
    except Exception as e:
        print(f"[!] Gặp lỗi trong quá trình quét: {e}")
        return members

    return members

async def send_to_telegram(client, members, target_chat):
    total = len(members)
    print(f"\n[*] Bắt đầu gửi {total} thành viên sang Telegram...")
    chunk_size = 50  # 50 người / 1 tin nhắn để đảm bảo < 4096 ký tự
    total_chunks = (total + chunk_size - 1) // chunk_size

    for idx, i in enumerate(range(0, total, chunk_size), start=1):
        chunk = members[i:i + chunk_size]
        # Sử dụng HTML để an toàn tuyệt đối với tên có chứa ký tự đặc biệt
        lines = []
        for u in chunk:
            safe_name = html.escape(u["full_name"])
            lines.append(f'<a href="tg://user?id={u["id"]}">{safe_name}</a>')
        
        message_body = "\n".join(lines)

        sent = False
        while not sent:
            try:
                await client.send_message(
                    target_chat,
                    message_body,
                    parse_mode='html',
                    link_preview=False
                )
                sent = True
                print(f"[+] Đã gửi gói {idx}/{total_chunks} ({min(i + chunk_size, total)}/{total} mem)")
                await asyncio.sleep(2.5)  # Nghỉ 2.5 giây tránh rate limit
            except errors.FloodWaitError as e:
                print(f"[!] Telegram yêu cầu tạm dừng {e.seconds} giây để chống spam. Đang chờ...")
                await asyncio.sleep(e.seconds + 1)
            except Exception as e:
                print(f"[!] Lỗi khi gửi tin nhắn: {e}")
                break

    print("\n[✓] ĐÃ GỬI HOÀN TẤT TOÀN BỘ DANH SÁCH!")

def export_to_files(members, source_group):
    clean_name = "".join([c if c.isalnum() else "_" for c in str(source_group)])
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = "output"
    os.makedirs(folder, exist_ok=True)

    csv_file = os.path.join(folder, f"mem_{clean_name}_{timestamp}.csv")
    txt_file = os.path.join(folder, f"mem_{clean_name}_{timestamp}.txt")

    # Xuất CSV
    with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "full_name", "username", "phone", "status", "profile_link"])
        writer.writeheader()
        for m in members:
            writer.writerow({
                "id": m["id"],
                "full_name": m["full_name"],
                "username": m["username"],
                "phone": m["phone"],
                "status": m.get("status", ""),
                "profile_link": m["profile_link"]
            })

    # Xuất TXT dạng Markdown Link
    with open(txt_file, "w", encoding="utf-8") as f:
        for m in members:
            f.write(f"[{m['full_name']}](tg://user?id={m['id']})\n")

    print(f"\n[✓] Đã xuất file CSV: {csv_file}")
    print(f"[✓] Đã xuất file TXT: {txt_file}")

async def main():
    print("=" * 60)
    print("       TOOL CÀO THÀNH VIÊN TELEGRAM (PROFILE CLICK LINK)       ")
    print("=" * 60)

    config = get_credentials()
    client = TelegramClient('tele_session', config['api_id'], config['api_hash'])

    await client.start(config['phone'])
    print("[*] Đăng nhập Telegram thành công!")

    while True:
        print("\n" + "=" * 40)
        print("MENU CHỨC NĂNG:")
        print("1. Quét thành viên & Gửi trực tiếp vào Channel/Chat (Giống mẫu)")
        print("2. Quét thành viên & Lưu ra file (Excel CSV + TXT)")
        print("3. Quét, Lưu file VÀ Gửi sang Telegram (Cả 2)")
        print("4. Cập nhật lại tài khoản (API ID / Hash)")
        print("0. Thoát")
        print("=" * 40)

        choice = input("Chọn chức năng (0-4): ").strip()

        if choice == "0":
            print("Tạm biệt!")
            break
        elif choice == "4":
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
            config = get_credentials()
            continue
        elif choice in ["1", "2", "3"]:
            source = input("\nNhập Username hoặc Link nhóm cần quét (vd: @nhommau hoặc https://t.me/nhommau): ").strip()
            if not source:
                print("[!] Chưa nhập nhóm nguồn!")
                continue

            print("\n--- BỘ LỌC THỜI GIAN HOẠT ĐỘNG (ONLINE) ---")
            print("1. Lấy tất cả thành viên")
            print("2. Chỉ lấy người ĐANG ONLINE ngay bây giờ")
            print("3. Online trong vòng 1 ngày (24 giờ)")
            print("4. Online trong vòng 3 ngày")
            print("5. Online trong vòng 7 ngày (1 tuần)")
            print("6. Nhập số ngày tùy chọn")
            filter_choice = input("Chọn bộ lọc (1-6, mặc định 1): ").strip()

            filter_days = None
            only_online = False

            if filter_choice == "2":
                only_online = True
            elif filter_choice == "3":
                filter_days = 1.0
            elif filter_choice == "4":
                filter_days = 3.0
            elif filter_choice == "5":
                filter_days = 7.0
            elif filter_choice == "6":
                try:
                    custom_days = float(input("Nhập số ngày (vd: 5): ").strip())
                    filter_days = max(custom_days, 0.1)
                except ValueError:
                    print("[!] Số ngày không hợp lệ, lấy tất cả.")
                    filter_days = None

            members = await scrape_members(client, source, filter_days=filter_days, only_online=only_online)
            if not members:
                print("[!] Không tìm thấy thành viên nào hoặc nhóm không cho phép quét.")
                continue

            print(f"[✓] Quét thành công tổng cộng: {len(members)} thành viên!")

            # Nếu chọn 2 hoặc 3: Lưu ra file
            if choice in ["2", "3"]:
                export_to_files(members, source)

            # Nếu chọn 1 hoặc 3: Gửi sang Telegram
            if choice in ["1", "3"]:
                print("\nNơi nhận danh sách:")
                print("- Để trống hoặc gõ 'me' để gửi vào Tin nhắn đã lưu (Saved Messages)")
                print("- Hoặc nhập @username kênh/nhóm của bạn (Bạn phải có quyền gửi tin)")
                target_input = input("Gửi vào đâu? (mặc định: 'me'): ").strip()
                target_chat = await resolve_target(client, target_input)
                if target_chat:
                    await send_to_telegram(client, members, target_chat)
                else:
                    print("[!] Không tìm thấy đích gửi, danh sách chưa được gửi đi.")
        else:
            print("[!] Lựa chọn không hợp lệ!")

if __name__ == "__main__":
    asyncio.run(main())
