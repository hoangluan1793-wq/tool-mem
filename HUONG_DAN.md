# HƯỚNG DẪN SỬ DỤNG TOOL CÀO THÀNH VIÊN TELEGRAM

Tool này giúp bạn cào toàn bộ thành viên từ một group Telegram (nếu nhóm mở danh sách thành viên) và tự động tạo danh sách link chữ xanh: **bấm vào tên là mở thẳng trang cá nhân (Profile)** của người đó, giống hệt ảnh mẫu.

---

### BƯỚC 1: LẤY API ID VÀ API HASH TỪ TELEGRAM
1. Mở trình duyệt web, truy cập: [https://my.telegram.org](https://my.telegram.org)
2. Nhập số điện thoại Telegram của bạn (có mã quốc gia, ví dụ `+84912345678`), Telegram sẽ gửi mã code xác thực qua app Telegram.
3. Sau khi đăng nhập, chọn mục **"API development tools"**.
4. Điền tên ứng dụng bất kỳ (ví dụ: `AppTitle`: `mytool`, `Short name`: `mytool123`), sau đó bấm **Save**.
5. Bạn sẽ thấy 2 mục:
   - **`App api_id`** (là một dãy số, ví dụ `28374921`)
   - **`App api_hash`** (là một chuỗi ký tự, ví dụ `d8e3b7f4a...`)
👉 Copy lại 2 thông tin này để nhập vào tool.

---

### BƯỚC 2: CHẠY TOOL
- Chỉ cần **nhấp đúp chuột vào file `run.bat`**.
- Lần đầu chạy, tool sẽ hỏi:
  + `API ID`: Dán api_id của bạn vào
  + `API HASH`: Dán api_hash của bạn vào
  + `Số điện thoại`: Nhập số điện thoại tài khoản Telegram
- Telegram sẽ gửi mã OTP đăng nhập về ứng dụng Telegram của bạn. Nhập mã OTP vào màn hình đen (nếu có mật khẩu 2 bước 2FA thì nhập thêm mật khẩu 2FA).
- Tool sẽ tự động tạo file `config.json` và lưu phiên đăng nhập `tele_session.session`, các lần sau bạn không cần nhập lại nữa!

---

### BƯỚC 3: CHỌN CHỨC NĂNG TRÊN MENU
1. **Phím 1**: Quét thành viên và gửi thẳng vào Channel/Kênh hoặc mục Saved Messages (Định dạng link chữ xanh click-to-profile).
2. **Phím 2**: Quét thành viên và xuất ra file Excel (`.csv`) cùng file Text (`.txt`).
3. **Phím 3**: Vừa lưu file vừa gửi vào Telegram.

---

### LƯU Ý KHI SỬ DỤNG
- **Nơi nhận (Đích đến):**
  + Nếu bạn để trống hoặc gõ `me`: Tool sẽ gửi thẳng vào **Tin nhắn đã lưu (Saved Messages)** của chính bạn.
  + Nếu muốn gửi vào Kênh/Nhóm riêng: Bạn phải là Admin hoặc có quyền đăng tin trong kênh đó, và nhập username kênh (vd `@kenh_cua_toi`).
- **Chống giới hạn Telegram (Flood Limit):**
  + Tool đã tự động chia nhỏ thành từng mẻ 50 người / tin nhắn và nghỉ 2.5 giây giữa các tin nhắn để bảo vệ an toàn cho nick của bạn.
