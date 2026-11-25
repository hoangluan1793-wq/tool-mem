import asyncio
import json
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

from datetime import datetime, timedelta
from telethon import errors
from core.database import get_connection, log_action
from core.telegram_client import post_to_channel

_scheduler_task = None
_is_running = False

async def execute_campaign(campaign_id):
    conn = get_connection()
    try:
        campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if not campaign or campaign["status"] in ["PAUSED", "STOPPED", "COMPLETED"]:
            return

        schedule = conn.execute("SELECT * FROM campaign_schedule WHERE campaign_id = ?", (campaign_id,)).fetchone()
        media = conn.execute("SELECT * FROM campaign_media WHERE campaign_id = ?", (campaign_id,)).fetchone()

        target_channels = []
        if campaign["target_channels"]:
            try:
                target_channels = json.loads(campaign["target_channels"])
            except Exception:
                target_channels = [campaign["target_channels"]]

        if not target_channels:
            log_action(campaign_id, "PUBLISH", "Không có nhóm mục tiêu nào được chỉ định", "WARNING")
            conn.execute("UPDATE campaigns SET status = 'FAILED' WHERE id = ?", (campaign_id,))
            conn.commit()
            return

        media_path = media["file_path"] if media and os.path.exists(media["file_path"]) else None
        post_content = campaign["content"] or ""
        dest_link = (campaign["destination_link"] or "").strip()
        if dest_link and dest_link not in post_content:
            post_content = f"{post_content}\n\n🔗 {dest_link}"

        # Cập nhật trạng thái thành ACTIVE
        conn.execute("UPDATE campaigns SET status = 'ACTIVE' WHERE id = ?", (campaign_id,))
        conn.commit()
        log_action(campaign_id, "PUBLISH_START", f"Bắt đầu gửi bài '{campaign['name']}' tới {len(target_channels)} nhóm", "INFO")

        has_failure = False
        for idx, channel in enumerate(target_channels):
            # Kiểm tra nếu người dùng đã tạm dừng hoặc dừng giữa chừng
            cur = conn.execute("SELECT status FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
            if cur and cur["status"] in ("PAUSED", "STOPPED"):
                log_action(campaign_id, "PUBLISH_STOPPED", "Đã dừng gửi bài theo yêu cầu người dùng", "WARNING")
                return

            ch_id = channel.get("id") or channel.get("username") if isinstance(channel, dict) else str(channel)
            ch_title = channel.get("title", ch_id) if isinstance(channel, dict) else ch_id
            topic_id = channel.get("topic_id") if isinstance(channel, dict) else None
            topic_title = channel.get("topic_title") if isinstance(channel, dict) else None
            display_title = f"{ch_title} › {topic_title}" if topic_title else ch_title

            try:
                msg_id = await post_to_channel(ch_id, post_content, media_path=media_path, topic_id=topic_id)
                conn.execute("""
                    INSERT INTO campaign_posts (campaign_id, channel_id, channel_title, message_id, posted_at, status)
                    VALUES (?, ?, ?, ?, datetime('now', 'localtime'), 'SUCCESS')
                """, (campaign_id, ch_id, display_title, msg_id))
                conn.commit()
                log_action(campaign_id, "POST_SUCCESS", f"Đã gửi thành công vào '{display_title}' (ID tin: {msg_id})", "SUCCESS")
                if idx < len(target_channels) - 1:
                    await asyncio.sleep(3) # Giãn cách an toàn giữa các nhóm
            except errors.FloodWaitError as fw:
                has_failure = True
                err_msg = f"Telegram yêu cầu chờ {fw.seconds}s"
                conn.execute("""
                    INSERT INTO campaign_posts (campaign_id, channel_id, channel_title, status, error_message)
                    VALUES (?, ?, ?, 'FAILED', ?)
                """, (campaign_id, ch_id, display_title, err_msg))
                conn.commit()
                log_action(campaign_id, "POST_ERROR", f"FloodWait tại '{display_title}': {err_msg}", "ERROR")
                await asyncio.sleep(min(fw.seconds + 1, 15))
            except Exception as e:
                has_failure = True
                err_msg = str(e)
                conn.execute("""
                    INSERT INTO campaign_posts (campaign_id, channel_id, channel_title, status, error_message)
                    VALUES (?, ?, ?, 'FAILED', ?)
                """, (campaign_id, ch_id, display_title, err_msg))
                conn.commit()
                log_action(campaign_id, "POST_ERROR", f"Lỗi gửi vào '{display_title}': {err_msg}", "ERROR")

        # Kiểm tra xem có phải lịch lặp lại định kỳ không
        sched_mode = (schedule["schedule_mode"] if schedule and "schedule_mode" in schedule.keys() else "once") or "once"
        interval_mins = int(schedule["interval_minutes"] or 0) if schedule and "interval_minutes" in schedule.keys() else 0

        if sched_mode == "interval" and interval_mins > 0:
            next_run = (datetime.now() + timedelta(minutes=interval_mins)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE campaigns SET status = 'SCHEDULED', updated_at = datetime('now', 'localtime') WHERE id = ?", (campaign_id,))
            conn.execute("UPDATE campaign_schedule SET start_time = ?, is_posted = 0, last_attempt = datetime('now', 'localtime') WHERE campaign_id = ?", (next_run, campaign_id))
            conn.commit()
            log_action(campaign_id, "PUBLISH_NEXT", f"Hoàn tất lượt gửi. Hẹn lượt tiếp theo lúc {next_run} (lặp mỗi {interval_mins} phút)", "SUCCESS" if not has_failure else "WARNING")
        else:
            new_status = "COMPLETED" if not has_failure else "FAILED"
            conn.execute("UPDATE campaigns SET status = ?, updated_at = datetime('now', 'localtime') WHERE id = ?", (new_status, campaign_id))
            conn.execute("UPDATE campaign_schedule SET is_posted = 1, last_attempt = datetime('now', 'localtime') WHERE campaign_id = ?", (campaign_id,))
            conn.commit()
            log_action(campaign_id, "PUBLISH_FINISH", f"Hoàn tất gửi bài với trạng thái: {new_status}", "SUCCESS" if not has_failure else "WARNING")

    except Exception as ex:
        log_action(campaign_id, "SYSTEM_ERROR", f"Lỗi hệ thống khi chạy chiến dịch: {str(ex)}", "ERROR")
    finally:
        conn.close()

async def scheduler_loop():
    global _is_running
    _is_running = True
    print("[*] Scheduler Worker đã khởi động, quét lịch đăng mỗi 10 giây...")

    while _is_running:
        try:
            conn = get_connection()
            # Lấy các chiến dịch đã đến giờ đăng
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            query = """
                SELECT c.id, s.retry_count, s.max_retries
                FROM campaigns c
                JOIN campaign_schedule s ON c.id = s.campaign_id
                WHERE (c.status = 'SCHEDULED' OR (c.status = 'FAILED' AND s.retry_count < s.max_retries))
                  AND s.is_posted = 0
                  AND s.start_time <= ?
            """
            due_campaigns = conn.execute(query, (now_str,)).fetchall()
            conn.close()

            for item in due_campaigns:
                await execute_campaign(item["id"])

        except Exception as e:
            print(f"[!] Scheduler loop error: {e}")

        await asyncio.sleep(10)

def start_scheduler():
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(scheduler_loop())

def stop_scheduler():
    global _is_running
    _is_running = False

# --- CÁC HÀM ĐIỀU KHIỂN CHIẾN DỊCH ---
def set_campaign_status(campaign_id, new_status):
    conn = get_connection()
    try:
        conn.execute("UPDATE campaigns SET status = ?, updated_at = datetime('now', 'localtime') WHERE id = ?", (new_status, campaign_id))
        conn.commit()
        log_action(campaign_id, "STATUS_CHANGE", f"Trạng thái chiến dịch đổi thành {new_status}", "INFO")
        return True
    finally:
        conn.close()

def delete_campaign(campaign_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
        conn.commit()
        log_action(campaign_id, "DELETE", f"Đã xóa chiến dịch ID {campaign_id}", "WARNING")
        return True
    finally:
        conn.close()
