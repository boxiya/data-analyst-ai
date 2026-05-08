# ============================================================
# db_service.py —— MySQL 数据库操作模块（v2，多数据源）
#
# v1：单库连接，固定读 .env 里的 MYSQL_DATABASE
# v2 升级：
# - get_connection(database) 支持传入任意库名
# - execute_sql(sql, database) 支持指定在哪个库执行
# - get_schema_for_source(database) 读取指定库的表结构
# - get_all_schemas() 读取所有注册数据源的表结构（带库名标签）
# ============================================================

import os
import json
import pymysql
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# 读取数据源注册表
_SOURCES_PATH = os.path.join(os.path.dirname(__file__), "sources.json")

def get_sources() -> list[dict]:
    """读取 sources.json，返回所有注册的数据源配置"""
    with open(_SOURCES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["sources"]


def get_source_by_id(source_id: str) -> Optional[dict]:
    """根据 source_id 查找数据源配置"""
    for s in get_sources():
        if s["id"] == source_id:
            return s
    return None


# ── 连接管理 ──────────────────────────────────────────────

def get_connection(database: str = None):
    """
    创建并返回一个 MySQL 数据库连接。
    
    database 参数：
    - 传入库名（如 "offline_db"）→ 连接到该库
    - 不传 → 使用 .env 里的 MYSQL_DATABASE（向后兼容）
    """
    db = database or os.getenv("MYSQL_DATABASE", "")
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=db,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


# ── SQL 执行 ──────────────────────────────────────────────

def execute_sql(sql: str, database: str = None) -> list:
    """
    执行一条 SQL 查询语句，返回结果列表。
    
    database 参数：
    - 传入库名 → 在该库执行
    - 不传 → 使用默认库（向后兼容）
    
    例如：
    execute_sql("SELECT * FROM offline_stores", database="offline_db")
    execute_sql("SELECT * FROM orders WHERE status='completed'")  # 默认库
    """
    conn = get_connection(database)
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall()
    finally:
        conn.close()


# ── 表结构读取 ────────────────────────────────────────────

def get_schema(database: str = None) -> list[dict]:
    """
    读取指定库的所有表字段结构（向后兼容，不传 database 读默认库）。
    
    返回格式：
    [
        {"table": "orders", "column": "amount", "type": "decimal(10,2)", "comment": "订单金额"},
        ...
    ]
    """
    db = database or os.getenv("MYSQL_DATABASE", "")
    sql = """
        SELECT
            TABLE_NAME   AS `table`,
            COLUMN_NAME  AS `column`,
            COLUMN_TYPE  AS `type`,
            COLUMN_COMMENT AS `comment`
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """
    conn = get_connection(db)
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (db,))
            return cursor.fetchall()
    finally:
        conn.close()


def get_all_schemas() -> list[dict]:
    """
    读取所有注册数据源的表结构，每条记录带上 source_id 和 database 标签。
    
    返回格式（比 get_schema 多了两个字段）：
    [
        {
            "source_id": "online",
            "database": "meituan_travel",
            "table": "orders",
            "column": "amount",
            "type": "decimal(10,2)",
            "comment": "订单金额"
        },
        {
            "source_id": "offline",
            "database": "offline_db",
            "table": "offline_orders",
            "column": "amount",
            "type": "decimal(10,2)",
            "comment": "订单金额（元）"
        },
        ...
    ]
    
    这些信息会被存入 RAG，让 LLM 知道每个字段属于哪个库，
    生成 SQL 时能正确选择数据源。
    """
    all_rows = []
    for source in get_sources():
        rows = get_schema(source["database"])
        for row in rows:
            row["source_id"] = source["id"]
            row["database"] = source["database"]
            row["source_name"] = source["name"]
        all_rows.extend(rows)
    return all_rows
