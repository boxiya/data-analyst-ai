# ============================================================
# main.py —— 后端入口文件
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from db_service import get_schema, execute_sql
from rag_service import index_schema, index_knowledge, search_schema, search_knowledge
from llm_service import generate_sql, generate_conclusion, clarify_question
from knowledge import get_all_knowledge

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 接口 1：健康检查 ──────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


# ── 接口 2：初始化 RAG ────────────────────────────────────
@app.post("/init")
def init():
    schema = get_schema()
    schema_count = index_schema(schema)
    knowledge = get_all_knowledge()
    knowledge_count = index_knowledge(knowledge)
    return {
        "message": f"初始化完成：表结构 {schema_count} 个字段，业务知识 {knowledge_count} 条"
    }


# ── 接口 3：核心问答接口（支持多轮对话）──────────────────
class Message(BaseModel):
    # 单条历史消息的格式
    # role: "user" 或 "assistant"
    # content: 消息内容
    role: str
    content: str

class AskRequest(BaseModel):
    question: str
    # history 是历史对话列表，前端每次把完整的对话历史传过来
    # 第一次提问时传空列表 []
    history: Optional[list[Message]] = []

@app.post("/ask")
def ask(req: AskRequest):

    # 把历史对话转成字符串，拼进 prompt 让 LLM 理解上下文
    # 例如：
    # 用户：各城市订单金额
    # 助手：三亚最高，32000元...
    # 用户：那三亚的详细订单呢   ← LLM 需要知道前面的对话才能理解"那"指什么
    history_text = ""
    if req.history:
        lines = []
        for msg in req.history[-6:]:  # 只取最近 6 条，避免 token 过多
            role_name = "用户" if msg.role == "user" else "助手"
            lines.append(f"{role_name}：{msg.content}")
        history_text = "【历史对话】\n" + "\n".join(lines)

    # 第一步：RAG 检索（结合历史对话一起检索，更准确）
    search_query = req.question
    if req.history:
        # 把最近一条历史加进检索词，帮助理解追问的上下文
        last_user_msg = next(
            (m.content for m in reversed(req.history) if m.role == "user"), ""
        )
        search_query = f"{last_user_msg} {req.question}"

    schema_context = search_schema(search_query)
    knowledge_context = search_knowledge(search_query)
    full_context = "\n\n".join(filter(None, [schema_context, knowledge_context]))

    # 第二步：LLM 生成 SQL（带上历史对话）
    sql = generate_sql(req.question, full_context, history_text)

    # 第三步：执行 SQL
    try:
        data = execute_sql(sql)
    except Exception as e:
        return {"error": str(e), "sql": sql}

    # 第四步：LLM 生成结论（带上历史对话，让结论更连贯）
    conclusion = generate_conclusion(req.question, data, history_text)

    return {
        "conclusion": conclusion,
        "data": data,
        "sql": sql,
    }
