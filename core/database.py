import sqlite3
import os
import sys
import json
from datetime import datetime

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.path.join(BASE_DIR, "toolkit.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Bảng Chiến dịch quảng cáo (campaigns)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        content TEXT NOT NULL,
        destination_link TEXT,
        target_channels TEXT, -- JSON array of channel IDs/usernames
        status TEXT DEFAULT 'DRAFT', -- DRAFT, SCHEDULED, ACTIVE, PAUSED, COMPLETED, STOPPED, FAILED
        budget REAL DEFAULT 0.0,
        cpm REAL DEFAULT 0.0,
        created_at TEXT DEFAULT (datetime('now', 'localtime')),
        updated_at TEXT DEFAULT (datetime('now', 'localtime'))
    );
    """)

    # 2. Bảng Bài đăng theo từng kênh (campaign_posts)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS campaign_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
        channel_id TEXT NOT NULL,
        channel_title TEXT,
        message_id INTEGER,
        posted_at TEXT,
        status TEXT DEFAULT 'PENDING', -- PENDING, SUCCESS, FAILED
        error_message TEXT,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    );
    """)

    # 3. Bảng Tệp phương tiện quảng cáo (campaign_media)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS campaign_media (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
        media_type TEXT NOT NULL, -- image, video
        file_path TEXT NOT NULL,
        file_name TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    );
    """)

    # 4. Bảng Lên lịch đăng bài (campaign_schedule)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS campaign_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
        start_time TEXT NOT NULL, -- YYYY-MM-DD HH:MM:SS
        end_time TEXT,
        is_posted INTEGER DEFAULT 0,
        retry_count INTEGER DEFAULT 0,
        max_retries INTEGER DEFAULT 3,
        last_attempt TEXT
    );
    """)

    # 5. Bảng Nhật ký hoạt động (campaign_logs)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS campaign_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
        action TEXT NOT NULL,
        level TEXT DEFAULT 'INFO', -- INFO, SUCCESS, WARNING, ERROR
        message TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    );
    """)

    # 6. Bảng Phân tích số liệu (campaign_analytics)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS campaign_analytics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
        channel_id TEXT,
        views INTEGER DEFAULT 0,
        forwards INTEGER DEFAULT 0,
        recorded_at TEXT DEFAULT (datetime('now', 'localtime'))
    );
    """)

    # 7. Bảng Lịch sử thành viên đã cào (scraped_history)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS scraped_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        scraped_at TEXT DEFAULT (datetime('now', 'localtime')),
        UNIQUE(group_id, user_id)
    );
    """)

    # Chỉ mục (Indexes) tối ưu hóa truy vấn
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_schedule_time ON campaign_schedule(start_time, is_posted);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_campaign ON campaign_posts(campaign_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_campaign ON campaign_logs(campaign_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scraped_history_group ON scraped_history(group_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scraped_history_user ON scraped_history(user_id);")

    conn.commit()
    conn.close()

# --- CÁC HÀM XỬ LÝ LOG ---
def log_action(campaign_id, action, message, level="INFO"):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO campaign_logs (campaign_id, action, level, message) VALUES (?, ?, ?, ?)",
            (campaign_id, action, level, message)
        )
        conn.commit()
    finally:
        conn.close()

def get_recent_logs(limit=20):
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT l.*, c.name as campaign_name 
            FROM campaign_logs l
            LEFT JOIN campaigns c ON l.campaign_id = c.id
            ORDER BY l.id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

# --- CÁC HÀM DASHBOARD METRICS ---
def get_dashboard_metrics():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM campaigns")
        total_campaigns = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM campaigns WHERE status = 'ACTIVE'")
        active_campaigns = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM campaigns WHERE status = 'SCHEDULED'")
        scheduled_campaigns = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM campaigns WHERE status = 'COMPLETED'")
        completed_campaigns = cursor.fetchone()[0]

        cursor.execute("SELECT COALESCE(SUM(views), 0) FROM campaign_analytics")
        total_views = cursor.fetchone()[0]

        cursor.execute("SELECT COALESCE(SUM(budget), 0) FROM campaigns")
        total_budget = cursor.fetchone()[0]

        # Lấy số bài đã đăng thành công theo ngày (7 ngày gần nhất) cho biểu đồ
        chart_data = conn.execute("""
            SELECT date(created_at) as post_date, COUNT(*) as count
            FROM campaign_posts
            WHERE status = 'SUCCESS'
            GROUP BY date(created_at)
            ORDER BY post_date DESC LIMIT 7
        """).fetchall()

        return {
            "total_campaigns": total_campaigns,
            "active_campaigns": active_campaigns,
            "scheduled_campaigns": scheduled_campaigns,
            "completed_campaigns": completed_campaigns,
            "total_views": total_views,
            "total_budget": total_budget,
            "chart_data": [dict(r) for r in reversed(chart_data)]
        }
    finally:
        conn.close()

# --- CÁC HÀM LỊCH SỬ CÀO THÀNH VIÊN ---
def get_scraped_user_ids(group_id=None):
    """Trả về set các user_id đã từng cào (theo group_id hoặc toàn bộ)."""
    conn = get_connection()
    try:
        if group_id is not None:
            rows = conn.execute(
                "SELECT user_id FROM scraped_history WHERE group_id = ?",
                (str(group_id),)
            ).fetchall()
        else:
            rows = conn.execute("SELECT DISTINCT user_id FROM scraped_history").fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def save_scraped_user_ids(group_id, user_ids):
    """Lưu danh sách user_id đã cào vào lịch sử (bỏ qua nếu đã tồn tại)."""
    if not user_ids:
        return
    conn = get_connection()
    try:
        gid = str(group_id)
        conn.executemany(
            "INSERT OR IGNORE INTO scraped_history (group_id, user_id) VALUES (?, ?)",
            [(gid, int(uid)) for uid in user_ids]
        )
        conn.commit()
    finally:
        conn.close()


def get_scraped_history_count(group_id=None):
    """Đếm số thành viên duy nhất đã lưu trong lịch sử cào."""
    conn = get_connection()
    try:
        if group_id is not None:
            row = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM scraped_history WHERE group_id = ?",
                (str(group_id),)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(DISTINCT user_id) FROM scraped_history").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def clear_scraped_history(group_id=None):
    """Xóa lịch sử thành viên đã cào (theo nhóm hoặc toàn bộ)."""
    conn = get_connection()
    try:
        if group_id is not None:
            conn.execute("DELETE FROM scraped_history WHERE group_id = ?", (str(group_id),))
        else:
            conn.execute("DELETE FROM scraped_history")
        conn.commit()
    finally:
        conn.close()


# Khởi tạo DB khi module được nạp
init_db()
