# ============================================================
# llm_service.py —— LLM 调用模块
# 作用：封装所有和 DeepSeek 大模型的交互
#
# 为什么用 OpenAI 的库调用 DeepSeek？
# DeepSeek 的 API 完全兼容 OpenAI 的接口格式，
# 只需要换一个 base_url，其他代码完全一样
# 这种设计叫"OpenAI 兼容接口"，很多国产大模型都支持
# ============================================================

import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# 初始化 DeepSeek 客户端
# api_key：身份验证，从 .env 文件读取
# base_url：DeepSeek 的 API 地址（替换掉 OpenAI 的地址）
client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY", ""),
    base_url="https://api.deepseek.com",
)

MODEL = "deepseek-chat"  # 使用的模型名称


def chat(messages: list) -> str:
    """
    最基础的 LLM 调用函数
    messages 是对话历史，格式：
    [
        {"role": "system", "content": "你是一个助手"},   # 系统提示，设定 LLM 的角色
        {"role": "user",   "content": "你好"},           # 用户说的话
        {"role": "assistant", "content": "你好！"},      # LLM 之前的回复（多轮对话用）
    ]
    temperature 控制回复的随机性：0 = 最确定，1 = 最随机
    生成代码/SQL 时用低 temperature，保证输出稳定
    """
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.3,  # 偏低，让输出更稳定、不乱发挥
    )
    # 从响应中取出文字内容
    return response.choices[0].message.content


def generate_sql(question: str, schema_context: str) -> str:
    """
    根据用户问题和表结构信息，让 LLM 生成 SQL 查询语句

    prompt 设计思路：
    - system 消息：告诉 LLM 它的角色和输出规则（只输出 SQL，不要解释）
    - user 消息：把"表结构信息 + 用户问题"一起发过去

    schema_context 是 RAG 检索到的字段信息，例如：
    "表名：orders，字段名：city，类型：varchar(50)，含义：城市
     表名：orders，字段名：amount，类型：decimal(10,2)，含义：订单金额"

    有了这些信息，LLM 就知道该用哪个表、哪个字段来写 SQL
    """
    system_prompt = (
        "你是一个数据分析师，根据用户问题和数据库表结构生成 MySQL SQL 查询语句。\n"
        "规则：\n"
        "1. 只输出纯 SQL，不要有任何解释，不要用 markdown 代码块包裹\n"
        "2. 所有关键词必须用英文（SELECT/FROM/WHERE/GROUP BY/ORDER BY 等）\n"
        "3. 字段名和表名要和提供的表结构完全一致\n"
        "4. 只用 SELECT，不允许 INSERT/UPDATE/DELETE\n"
        "5. 结果要有意义，适当加 ORDER BY 和 LIMIT"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        # 把 RAG 检索到的字段信息和用户问题一起发给 LLM
        {"role": "user", "content": f"{schema_context}\n\n用户问题：{question}\n\nSQL："}
    ]
    return chat(messages)


def generate_conclusion(question: str, data: list) -> str:
    """
    根据 SQL 查询结果，让 LLM 生成自然语言分析结论

    data 是 execute_sql 返回的数据列表，例如：
    [{"city": "三亚", "total_amount": 32000}, {"city": "上海", "total_amount": 10000}]

    LLM 会把这些数字转化成人话，指出关键趋势和结论
    """
    messages = [
        {
            "role": "system",
            "content": "你是数据分析师，把数据查询结果用简洁的中文解释给用户，重点突出关键数字和结论。"
        },
        {
            "role": "user",
            "content": f"用户问题：{question}\n\n查询结果：{data}\n\n请给出分析结论："
        }
    ]
    return chat(messages)
