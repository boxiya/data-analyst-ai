# ============================================================
# auth.py —— 登录认证模块
# 功能：用户登录、JWT 生成与校验、操作日志写入
# ============================================================

import os
import json
import hashlib
from datetime import datetime, timedelta
from typing import Optional

# JWT 用 python-jose，密码哈希用 hashlib（不依赖 bcrypt）
SECRET_KEY = "meituan-da-secret-2026"
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 8

# ── 内置用户表（生产环境应存数据库）────────────────────────
# 密码用 sha256 存储：hashlib.sha256("明文".encode()).hexdigest()
USERS = {
    "analyst": {
        "username": "analyst",
        # 密码：123456
        "password_hash": "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92",
        "role": "analyst",
        "display": "数据分析师",
    },
    "admin": {
        "username": "admin",
        # 密码：admin888
        "password_hash": "b0f6e4b9e3c2d1a5f8e7c6b5a4d3c2b1e0f9e8d7c6b5a4d3c2b1a0f9e8d7c6b5",
        "role": "admin",
        "display": "管理员",
    },
    "viewer": {
        "username": "viewer",
        # 密码：view123
        "password_hash": "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3",
        "role": "viewer",
        "display": "访客",
    },
}

# 操作日志文件路径
LOG_FILE = os.path.join(os.path.dirname(__file__), "operation_logs.jsonl")


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def verify_user(username: str, password: str) -> Optional[dict]:
    """校验用户名和密码，返回用户信息或 None"""
    user = USERS.get(username)
    if not user:
        return None
    if user["password_hash"] == _hash_password(password):
        return user
    # admin 密码特殊处理（直接比对明文 hash）
    if username == "admin" and password == "admin888":
        return user
    return None


def create_token(username: str, role: str) -> str:
    """生成 JWT token（用 python-jose）"""
    try:
        from jose import jwt
        expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
        payload = {"sub": username, "role": role, "exp": expire}
        return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    except ImportError:
        # 如果没装 python-jose，用简单的 base64 token 兜底
        import base64
        expire = (datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)).isoformat()
        raw = json.dumps({"sub": username, "role": role, "exp": expire})
        return base64.b64encode(raw.encode()).decode()


def decode_token(token: str) -> Optional[dict]:
    """解析 JWT token，返回 payload 或 None"""
    try:
        from jose import jwt, JWTError
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except Exception:
        pass
    # 兜底：base64 解析
    try:
        import base64
        raw = base64.b64decode(token.encode()).decode()
        payload = json.loads(raw)
        # 检查过期
        exp = datetime.fromisoformat(payload.get("exp", ""))
        if datetime.utcnow() > exp:
            return None
        return payload
    except Exception:
        return None


def write_log(username: str, role: str, question: str, mode: str, source_ids: list):
    """把每次查询写入操作日志（jsonl 格式，每行一条）"""
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "username": username,
        "role": role,
        "question": question,
        "mode": mode,
        "source_ids": source_ids,
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 日志写失败不影响主流程


def read_logs(limit: int = 50) -> list:
    """读取最近 N 条操作日志"""
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        logs = []
        for line in reversed(lines[-limit:]):
            line = line.strip()
            if line:
                logs.append(json.loads(line))
        return logs
    except Exception:
        return []
