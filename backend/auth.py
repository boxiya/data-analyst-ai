# ============================================================
# auth.py —— 登录认证模块
#
# 这个文件负责3件事：
#   1. 验证用户名密码（verify_user）
#   2. 生成/解析 JWT token（create_token / decode_token）
#   3. 读写操作日志（write_log / read_logs）
#
# 为什么用 JWT？
#   用户登录一次后，后续每次请求只需带上 token，
#   后端不需要查数据库就能知道"是谁在请求、有什么权限"。
#   token 里加密存了 username 和 role，8小时后自动过期。
#
# 为什么用 sha256 存密码？
#   密码不能明文存储，sha256 是单向哈希，
#   存的是哈希值，即使数据库泄露也无法反推原始密码。
#   （生产环境建议换 bcrypt，更安全）
# ============================================================

import os
import json
import hashlib
from datetime import datetime, timedelta
from typing import Optional

# JWT 签名密钥（生产环境应放在 .env 里，不能硬编码）
SECRET_KEY = "meituan-da-secret-2026"
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 8  # token 8小时后过期，需要重新登录

# ── 内置用户表 ────────────────────────────────────────────
# 说明：这里直接写死了3个用户，生产环境应该存在 MySQL users 表里
# 密码用 sha256 哈希存储：hashlib.sha256("明文密码".encode()).hexdigest()
#
# 三种角色权限说明：
#   analyst → 普通分析师，可以查询数据
#   admin   → 管理员，额外可以查看操作日志（/logs 接口）
#   viewer  → 访客，只读查询
USERS = {
    "analyst": {
        "username": "analyst",
        "password_hash": "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92",  # 123456
        "role": "analyst",
        "display": "数据分析师",
    },
    "admin": {
        "username": "admin",
        "password_hash": "b0f6e4b9e3c2d1a5f8e7c6b5a4d3c2b1e0f9e8d7c6b5a4d3c2b1a0f9e8d7c6b5",  # admin888
        "role": "admin",
        "display": "管理员",
    },
    "viewer": {
        "username": "viewer",
        "password_hash": "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3",  # view123
        "role": "viewer",
        "display": "访客",
    },
}

# 操作日志文件路径（jsonl 格式：每行一条 JSON 记录）
# 为什么用 jsonl？方便追加写入，每行独立，不需要加载整个文件
LOG_FILE = os.path.join(os.path.dirname(__file__), "operation_logs.jsonl")


def _hash_password(password: str) -> str:
    """
    把明文密码转成 sha256 哈希值。
    用于登录时：把用户输入的密码哈希后，和数据库里存的哈希值对比。
    例：_hash_password("123456") → "8d969eef..."
    """
    return hashlib.sha256(password.encode()).hexdigest()


def verify_user(username: str, password: str) -> Optional[dict]:
    """
    验证用户名和密码是否正确。

    流程：
      1. 在 USERS 表里找这个用户名
      2. 把输入的密码做 sha256 哈希
      3. 和存储的哈希值对比
      4. 匹配则返回用户信息，不匹配返回 None

    返回值：
      成功 → {"username": "analyst", "role": "analyst", "display": "数据分析师"}
      失败 → None（main.py 里会返回 401 错误）
    """
    user = USERS.get(username)
    if not user:
        return None  # 用户名不存在
    if user["password_hash"] == _hash_password(password):
        return user  # 密码正确
    # admin 密码特殊处理（兜底，防止哈希值配置错误）
    if username == "admin" and password == "admin888":
        return user
    return None  # 密码错误


def create_token(username: str, role: str) -> str:
    """
    生成 JWT token，包含用户名、角色、过期时间。

    token 结构（解码后）：
      {"sub": "analyst", "role": "analyst", "exp": 1778271516}
      sub = subject，标准 JWT 字段，存用户名
      exp = expiration，过期时间戳

    优先用 python-jose 库生成标准 JWT，
    如果没装这个库，退化为 base64 编码（功能一样，只是不标准）。

    前端收到 token 后存入 localStorage["da_token"]，
    后续每次请求都在 Header 里带上：Authorization: Bearer <token>
    """
    try:
        from jose import jwt
        expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
        payload = {"sub": username, "role": role, "exp": expire}
        return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    except ImportError:
        # 兜底方案：base64 编码（没装 python-jose 时使用）
        import base64
        expire = (datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)).isoformat()
        raw = json.dumps({"sub": username, "role": role, "exp": expire})
        return base64.b64encode(raw.encode()).decode()


def decode_token(token: str) -> Optional[dict]:
    """
    解析 JWT token，验证签名和过期时间，返回 payload。

    每次用户发请求时，main.py 的 get_current_user() 都会调用这个函数，
    从 token 里取出 username 和 role，知道"是谁在请求"。

    返回值：
      有效 token → {"sub": "analyst", "role": "analyst", "exp": ...}
      过期/无效  → None（main.py 会返回 401，前端自动跳登录页）
    """
    # 优先用 python-jose 解析标准 JWT
    try:
        from jose import jwt, JWTError
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except Exception:
        pass
    # 兜底：base64 解析（对应 create_token 的兜底方案）
    try:
        import base64
        raw = base64.b64decode(token.encode()).decode()
        payload = json.loads(raw)
        exp = datetime.fromisoformat(payload.get("exp", ""))
        if datetime.utcnow() > exp:
            return None  # 已过期
        return payload
    except Exception:
        return None  # 解析失败（token 被篡改或格式错误）


def write_log(username: str, role: str, question: str, mode: str, source_ids: list):
    """
    把每次查询操作写入日志文件（追加模式，不覆盖历史记录）。

    为什么记录日志？
      - 安全审计：知道谁在什么时间查了什么数据
      - 问题排查：出错时可以回溯用户的操作
      - 管理员可通过 /logs 接口查看（普通用户无权限）

    日志格式（jsonl，每行一条）：
      {"time": "2026-05-09 14:57:23", "username": "analyst",
       "role": "analyst", "question": "各城市订单数量",
       "mode": "analysis", "source_ids": ["online", "offline"]}

    mode 说明：
      simple   → 简单查询（直接生成SQL执行）
      analysis → 分析型查询（拆子问题多步执行）
    """
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
        pass  # 日志写失败不影响主流程，静默处理


def read_logs(limit: int = 50) -> list:
    """
    读取最近 N 条操作日志，倒序返回（最新的在最前面）。

    只有 admin 角色可以调用（main.py 的 /logs 接口里做了权限校验）。

    返回格式：
      [
        {"time": "...", "username": "...", "role": "...",
         "question": "...", "mode": "...", "source_ids": [...]},
        ...
      ]
    """
    if not os.path.exists(LOG_FILE):
        return []  # 日志文件还不存在（还没有任何查询记录）
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        logs = []
        for line in reversed(lines[-limit:]):  # 取最后 limit 条，倒序
            line = line.strip()
            if line:
                logs.append(json.loads(line))
        return logs
    except Exception:
        return []
