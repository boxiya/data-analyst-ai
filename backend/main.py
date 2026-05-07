# ============================================================
# main.py —— 后端入口文件（v2）
#
# v2 升级：
# - /ask 接口接入分析引擎，自动判断走简单查询还是分析路径
# - 返回结构新增 mode / analysis_type / steps / intent 字段
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from db_service import get_schema, execute_sql
from rag_service import index_schema, index_knowledge, search_schema, search_knowledge
from llm_service import generate_sql, generate_conclusion, clarify_question
from knowledge import get_all_knowledge
from analysis_engine import process_question

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


# ── 接口 3：核心问答接口（v2，支持分析引擎）──────────────
class Message(BaseModel):
    role: str      # "user" 或 "assistant"
    content: str   # 消息内容


class AskRequest(BaseModel):
    question: str
    history: Optional[list[Message]] = []


@app.post("/ask")
def ask(req: AskRequest):
    # 把历史对话转成字符串，拼进 prompt 让 LLM 理解上下文
    history_text = ""
    if req.history:
        lines = []
        for msg in req.history[-6:]:  # 只取最近 6 条，避免 token 过多
            role_name = "用户" if msg.role == "user" else "助手"
            lines.append(f"{role_name}：{msg.content}")
        history_text = "【历史对话】\n" + "\n".join(lines)

    # 调用分析引擎（内部自动判断走简单查询还是分析路径）
    result = process_question(req.question, history_text)

    # ── 返回结构说明 ──────────────────────────────────────
    # mode = "simple"：简单查询，字段同 v1（conclusion / data / sql）
    # mode = "analysis"：分析模式，新增字段：
    #   - analysis_type: "anomaly" | "trend" | "comparison" | "distribution"
    #   - steps: 每个子查询的详情列表
    #     [{ step, sub_question, purpose, sql, data, error? }]
    #   - conclusion: 综合分析报告
    #   - intent: 意图识别结果 { type, analysis_type, reason }
    return result
