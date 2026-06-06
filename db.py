"""
量化交易系统 — 账户数据库模块
==============================
SQLite 数据库，存储用户账户、密码哈希、会话令牌。
"""

import sqlite3
import hashlib
import uuid
import os
import secrets
import time
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "accounts.db"
USER_DATA_ROOT = BASE_DIR / "user_data"


def get_db():
    """获取数据库连接（自动创建表）."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_tables(conn)
    return conn


def _init_tables(conn):
    """初始化数据库表."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            expires_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    # 兼容已有数据库：如果 display_name 列不存在则添加
    try:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 列已存在


# Token 有效期（天）
TOKEN_EXPIRE_DAYS = 7


def hash_password(password: str) -> str:
    """PBKDF2 加盐密码哈希。格式: pbkdf2$salt$hash"""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), 100000)
    return f"pbkdf2${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """验证密码。兼容旧的 SHA-256 格式（自动升级）。"""
    if stored_hash.startswith("pbkdf2$"):
        try:
            _, salt, hashed = stored_hash.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), 100000)
            return dk.hex() == hashed
        except ValueError:
            return False
    # 兼容旧 SHA-256 格式
    old_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return old_hash == stored_hash


def _maybe_upgrade_hash(username: str, password: str, stored_hash: str):
    """如果密码是旧格式，自动升级为 PBKDF2 格式."""
    if not stored_hash.startswith("pbkdf2$"):
        conn = get_db()
        try:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (hash_password(password), username)
            )
            conn.commit()
        finally:
            conn.close()


def generate_token() -> str:
    """生成随机会话令牌."""
    return uuid.uuid4().hex + uuid.uuid4().hex  # 64字符


# ═══════════════════════════════════════════════════════════════
#  用户操作
# ═══════════════════════════════════════════════════════════════

def register_user(username: str, password: str) -> dict:
    """注册新用户。成功返回用户信息，失败返回错误字典."""
    username = username.strip()
    if not username or not password:
        return {"ok": False, "error": "用户名和密码不能为空"}
    if len(username) < 1 or len(username) > 30:
        return {"ok": False, "error": "用户名长度 1-30 个字符"}
    if len(password) < 4:
        return {"ok": False, "error": "密码至少 4 位"}

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, hash_password(password))
        )
        conn.commit()
        user_id = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()["id"]

        # 为新用户创建数据目录
        user_dir = USER_DATA_ROOT / username
        user_dir.mkdir(parents=True, exist_ok=True)

        return {"ok": True, "user": {"id": user_id, "username": username, "displayName": ""}}
    except sqlite3.IntegrityError:
        return {"ok": False, "error": "用户名已存在"}
    finally:
        conn.close()


def login_user(username: str, password: str) -> dict:
    """登录验证。成功返回 token 和用户信息."""
    username = username.strip()
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT id, username, display_name, password_hash FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if not user:
            return {"ok": False, "error": "用户不存在"}
        if not verify_password(password, user["password_hash"]):
            return {"ok": False, "error": "密码错误"}
        # 自动升级旧格式密码
        _maybe_upgrade_hash(username, password, user["password_hash"])

        # 生成会话令牌，7天有效期
        token = generate_token()
        expires = (datetime.now() + timedelta(days=TOKEN_EXPIRE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        # 清除旧会话 + 过期会话
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM sessions WHERE expires_at < datetime('now','localtime')")
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user["id"], expires)
        )
        conn.commit()

        return {
            "ok": True,
            "token": token,
            "user": {"id": user["id"], "username": user["username"],
                     "displayName": user["display_name"] or ""}
        }
    finally:
        conn.close()


def get_user_by_token(token: str) -> dict | None:
    """通过 token 获取当前用户，token 无效或过期返回 None."""
    if not token:
        return None
    conn = get_db()
    try:
        # 先清理过期会话
        conn.execute("DELETE FROM sessions WHERE expires_at < datetime('now','localtime')")
        row = conn.execute("""
            SELECT u.id, u.username, u.display_name FROM users u
            JOIN sessions s ON u.id = s.user_id
            WHERE s.token = ? AND (s.expires_at IS NULL OR s.expires_at > datetime('now','localtime'))
        """, (token,)).fetchone()
        if row:
            return {"id": row["id"], "username": row["username"],
                    "displayName": row["display_name"] or ""}
        return None
    finally:
        conn.close()


def logout_user(token: str):
    """登出，删除会话."""
    if not token:
        return
    conn = get_db()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def reset_password(username: str, old_password: str, new_password: str) -> dict:
    """重置密码。需要验证旧密码。"""
    username = username.strip()
    if not old_password or not new_password:
        return {"ok": False, "error": "密码不能为空"}
    if len(new_password) < 4:
        return {"ok": False, "error": "新密码至少 4 位"}
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not user:
            return {"ok": False, "error": "用户不存在"}
        if not verify_password(old_password, user["password_hash"]):
            return {"ok": False, "error": "旧密码错误"}
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(new_password), username)
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def update_profile(username: str, display_name: str) -> dict:
    """更新用户昵称."""
    username = username.strip()
    display_name = (display_name or "").strip()
    if len(display_name) > 50:
        return {"ok": False, "error": "昵称不能超过50个字符"}
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET display_name = ? WHERE username = ?",
            (display_name, username)
        )
        conn.commit()
        return {"ok": True, "displayName": display_name}
    finally:
        conn.close()


def search_users(keyword: str) -> list:
    """搜索用户（模糊匹配用户名）."""
    if not keyword or not keyword.strip():
        return []
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, username, created_at FROM users WHERE username LIKE ? LIMIT 20",
            (f"%{keyword.strip()}%",)
        ).fetchall()
        return [{"id": r["id"], "username": r["username"], "createdAt": r["created_at"]} for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
#  用户数据目录
# ═══════════════════════════════════════════════════════════════

def get_user_dir(username: str) -> Path:
    """获取用户数据目录路径."""
    return USER_DATA_ROOT / username


def ensure_user_dir(username: str) -> Path:
    """确保用户数据目录存在."""
    d = get_user_dir(username)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ═══════════════════════════════════════════════════════════════
#  初始化：预置账户
# ═══════════════════════════════════════════════════════════════

def init_preset_accounts():
    """初始化预置账户 A8."""
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", ("A8",)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("A8", hash_password("13590703676"))
            )
            conn.commit()
            # 创建数据目录并复制现有数据
            user_dir = USER_DATA_ROOT / "A8"
            user_dir.mkdir(parents=True, exist_ok=True)
            _migrate_existing_data(user_dir)
    finally:
        conn.close()


def _migrate_existing_data(user_dir: Path):
    """将项目根目录的现有数据迁移到用户目录."""
    import shutil
    files_to_migrate = [
        "trades.json", "strategy_state.json", "watchlist.json",
        "strategies.json",
    ]
    for fname in files_to_migrate:
        src = BASE_DIR / fname
        dst = user_dir / fname
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

    # 复制 .env 为用户配置
    env_src = BASE_DIR / ".env"
    env_dst = user_dir / ".env"
    if env_src.exists() and not env_dst.exists():
        shutil.copy2(env_src, env_dst)


if __name__ == "__main__":
    # 首次运行：初始化数据库和预置账户
    init_preset_accounts()
    print(f"数据库初始化完成: {DB_PATH}")
    print(f"预置账户: A8 / 13590703676")

    # 列出所有用户
    conn = get_db()
    users = conn.execute("SELECT id, username, created_at FROM users").fetchall()
    print("\n所有用户:")
    for u in users:
        print(f"  ID={u['id']}  用户名={u['username']}  创建时间={u['created_at']}")
    conn.close()
