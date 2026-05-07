# ============================================================
# db_service.py —— MySQL 数据库操作模块
# 作用：负责连接 MySQL、执行 SQL、读取表结构
# ============================================================

import os
import pymysql
from dotenv import load_dotenv

# 从 .env 文件加载配置（数据库密码、地址等）
load_dotenv()


def get_connection():
    """
    创建并返回一个 MySQL 数据库连接
    连接参数从 .env 文件读取，不硬编码在代码里（安全）
    DictCursor 让查询结果以字典格式返回，例如 {"city": "北京", "amount": 1000}
    而不是元组格式 ("北京", 1000)，更直观
    """
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),      # 数据库地址
        port=int(os.getenv("MYSQL_PORT", 3306)),          # 端口，MySQL 默认 3306
        user=os.getenv("MYSQL_USER", "root"),             # 用户名
        password=os.getenv("MYSQL_PASSWORD", ""),         # 密码
        database=os.getenv("MYSQL_DATABASE", ""),         # 数据库名
        charset="utf8mb4",                                # 支持中文和 emoji
        cursorclass=pymysql.cursors.DictCursor,           # 结果以字典格式返回
    )


def execute_sql(sql: str) -> list:
    """
    执行一条 SQL 查询语句，返回结果列表
    例如：execute_sql("SELECT city, SUM(amount) FROM orders GROUP BY city")
    返回：[{"city": "北京", "SUM(amount)": 3600}, ...]

    注意：每次执行完都会关闭连接（finally 块保证一定执行）
    这是好习惯，避免连接数耗尽
    """
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall()  # 获取所有结果行
    finally:
        conn.close()  # 无论成功还是报错，都关闭连接


def get_schema() -> list[dict]:
    """
    读取数据库中所有表的字段结构信息
    从 MySQL 自带的 information_schema 系统库查询
    information_schema 是 MySQL 内置的"数据字典"，记录了所有表和字段的元信息

    返回格式：
    [
        {"table": "orders", "column": "amount", "type": "decimal(10,2)", "comment": "订单金额"},
        {"table": "orders", "column": "city",   "type": "varchar(50)",   "comment": "城市"},
        ...
    ]
    这些信息会被存入 RAG，让 LLM 知道数据库里有哪些表和字段
    """
    sql = """
        SELECT
            TABLE_NAME   AS `table`,    -- 表名
            COLUMN_NAME  AS `column`,   -- 字段名
            COLUMN_TYPE  AS `type`,     -- 字段类型
            COLUMN_COMMENT AS `comment` -- 字段注释（我们建表时写的中文说明）
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s         -- 只查当前数据库的表，不查系统表
        ORDER BY TABLE_NAME, ORDINAL_POSITION  -- 按表名和字段顺序排列
    """
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            # %s 是占位符，防止 SQL 注入，实际值通过第二个参数传入
            cursor.execute(sql, (os.getenv("MYSQL_DATABASE"),))
            return cursor.fetchall()
    finally:
        conn.close()
