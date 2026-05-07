# ============================================================
# main.py —— 后端入口文件（v3）
#
# v2 升级：
# - /ask 接口接入分析引擎，自动判断走简单查询还是分析路径
# - 返回结构新增 mode / analysis_type / steps / intent 字段
#
# v3 升级（通用上下文记忆）：
# - AskRequest 新增 context_summary 字段（上一轮的分析焦点摘要）
# - 每轮查询完成后调用 summarize_context() 生成本轮摘要
# - 摘要注入 history_text，让 LLM 精准理解追问上下文
# - 返回结构新增 context_summary 字段，前端存起来下次带回
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from db_service import get_schema, execute_sql
from rag_service import index_schema, index_knowledge
from llm_service import generate_sql, generate_conclusion, clarify_question, summarize_context
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


# ── 接口 3：核心问答接口（v3，通用上下文记忆）────────────
class Message(BaseModel):
    role: str      # "user" | "assistant" | "context_summary"
    content: str   # 消息内容


class AskRequest(BaseModel):
    question: str
    history: Optional[list[Message]] = []
    # v3 新增：上一轮的分析焦点摘要
    # 前端每次把上一轮返回的 context_summary 带回来
    # 第一次提问时传空字符串 ""
    context_summary: Optional[str] = ""


@app.post("/ask")
def ask(req: AskRequest):
    # ── 构建 history_text ────────────────────────────────
    # v3 升级：在历史对话文字之前，先注入上一轮的分析焦点摘要
    # 这样即使历史对话被截断，LLM 也能通过摘要理解追问的上下文
    #
    # 最终拼出来的 history_text 格式：
    #
    # 【上一轮分析焦点】
    # 三亚2026年4月完成订单GMV共16039元，按酒店分组
    #
    # 【历史对话】
    # 用户：三亚上个月的完成订单金额是多少？
    # 助手：16,039.93元...
    # 用户：那环比呢？
    #
    # LLM 看到"上一轮分析焦点"就能精准理解"那"指什么，
    # 不需要从长篇历史对话里自己推断

    parts = []

    # 1. 注入上一轮摘要（最关键的上下文）
    if req.context_summary:
        parts.append(f"【上一轮分析焦点】\n{req.context_summary}")

    # 2. 注入最近 6 条历史对话（补充细节）
    if req.history:
        lines = []
        for msg in req.history[-6:]:
            if msg.role == "user":
                lines.append(f"用户：{msg.content}")
            elif msg.role == "assistant":
                lines.append(f"助手：{msg.content}")
            # context_summary 类型的条目不重复注入
        if lines:
            parts.append("【历史对话】\n" + "\n".join(lines))

    history_text = "\n\n".join(parts)

    # ── 调用分析引擎 ──────────────────────────────────────
    result = process_question(req.question, history_text)

    # ── 生成本轮分析焦点摘要 ──────────────────────────────
    # 从结果里提取 conclusion 和 sql，生成一句话摘要
    # 分析模式取综合结论，简单查询取单条结论
    conclusion_for_summary = result.get("conclusion", "")
    if result.get("mode") == "analysis" and result.get("steps"):
        # 分析模式：用第一步的 SQL 作为代表
        sql_for_summary = result["steps"][0].get("sql", "")
    else:
        sql_for_summary = result.get("sql", "")

    new_summary = summarize_context(
        question=req.question,
        conclusion=conclusion_for_summary,
        sql=sql_for_summary,
    )

    # 把摘要加入返回结果，前端存起来，下次提问时带回来
    result["context_summary"] = new_summary

    # ── 返回结构说明（v3）────────────────────────────────
    # 公共字段：
    #   mode: "simple" | "analysis"
    #   conclusion: 分析结论文字
    #   context_summary: 本轮分析焦点摘要（前端下次请求时带回）
    #   intent: 意图识别结果
    #
    # simple 模式额外字段：
    #   data: 查询结果列表
    #   sql: 执行的 SQL
    #
    # analysis 模式额外字段：
    #   analysis_type: "anomaly" | "trend" | "comparison" | "distribution"
    #   steps: [{ step, sub_question, purpose, sql, data, error? }]
    return result
