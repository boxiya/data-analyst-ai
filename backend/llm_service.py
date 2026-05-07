# ============================================================
# llm_service.py —— LLM 调用模块
# ============================================================

import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY", ""),
    base_url="https://api.deepseek.com",
)

MODEL = "deepseek-chat"


def chat(messages: list) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.3,
    )
    return response.choices[0].message.content


def generate_sql(question: str, schema_context: str, history_text: str = "") -> str:
    """
    生成 SQL，支持多轮对话上下文
    history_text 是历史对话字符串，有了它 LLM 能理解追问
    例如用户说"那三亚的呢"，LLM 结合历史知道是在问三亚的订单
    """
    # 如果有历史对话，拼在字段信息后面
    context = schema_context
    if history_text:
        context = f"{history_text}\n\n{schema_context}"

    system_prompt = (
        "你是一个数据分析师，根据用户问题和数据库表结构生成 MySQL SQL 查询语句。\n"
        "规则：\n"
        "1. 只输出纯 SQL，不要有任何解释，不要用 markdown 代码块包裹\n"
        "2. 所有关键词必须用英文（SELECT/FROM/WHERE/GROUP BY/ORDER BY 等）\n"
        "3. 字段名和表名要和提供的表结构完全一致\n"
        "4. 只用 SELECT，不允许 INSERT/UPDATE/DELETE\n"
        "5. 结果要有意义，适当加 ORDER BY 和 LIMIT\n"
        "6. 如果用户的问题是追问（如'那三亚的呢'），结合历史对话理解完整意图"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{context}\n\n用户问题：{question}\n\nSQL："}
    ]
    return chat(messages)


def generate_conclusion(question: str, data: list, history_text: str = "") -> str:
    """
    生成分析结论，支持多轮对话上下文
    有了历史对话，结论可以做对比、引用上一轮的数据
    例如"和上次查的相比，三亚这次下降了..."
    """
    context = f"{history_text}\n\n" if history_text else ""
    messages = [
        {
            "role": "system",
            "content": (
                "你是数据分析师，把数据查询结果用简洁的中文解释给用户。\n"
                "如果有历史对话，可以结合上下文做对比分析，让回答更连贯。"
            )
        },
        {
            "role": "user",
            "content": f"{context}用户问题：{question}\n\n查询结果：{data}\n\n请给出分析结论："
        }
    ]
    return chat(messages)


def clarify_question(question: str, history_text: str = "") -> str:
    """
    当用户问题不够清晰时，让 LLM 反问用户
    例如用户只说"帮我看看数据"，LLM 会追问"你想看哪方面的数据？"
    （这个函数目前预留，后续可以在前端加意图识别逻辑）
    """
    context = f"{history_text}\n\n" if history_text else ""
    messages = [
        {
            "role": "system",
            "content": "你是数据分析助手。如果用户问题不够明确，礼貌地追问用户想了解哪方面的数据。"
        },
        {
            "role": "user",
            "content": f"{context}用户说：{question}"
        }
    ]
    return chat(messages)
