# ============================================================
# main.py —— 后端入口文件（v4，多数据源）
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
#
# v4 升级（多数据源）：
# - /init 接口改为索引所有注册数据源的表结构（带库名标签）
# - /sources 新接口：返回当前注册的数据源列表
# - /ask 返回结构新增 source_ids 字段（本次查了哪些库）
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from db_service import get_schema, get_all_schemas, execute_sql, get_sources
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


# ── 接口 2：初始化 RAG（v4：索引所有数据源）─────────────
@app.post("/init")
def init():
    # v4 升级：改为索引所有注册数据源的表结构（带库名标签）
    # 这样 RAG 检索时 LLM 能知道每个字段属于哪个库
    schema = get_all_schemas()
    schema_count = index_schema(schema)
    knowledge = get_all_knowledge()
    knowledge_count = index_knowledge(knowledge)
    
    # 统计每个数据源的字段数
    source_stats = {}
    for row in schema:
        sid = row.get("source_id", "default")
        source_stats[sid] = source_stats.get(sid, 0) + 1
    stats_str = "、".join([f"{k}库{v}个字段" for k, v in source_stats.items()])
    
    return {
        "message": f"初始化完成：{stats_str}，业务知识 {knowledge_count} 条",
        "schema_count": schema_count,
        "knowledge_count": knowledge_count,
        "source_stats": source_stats,
    }


# ── 接口 3：数据源列表（v4 新增）────────────────────────
@app.get("/sources")
def list_sources():
    """返回当前注册的所有数据源，前端可以展示数据源选择器"""
    sources = get_sources()
    return {
        "sources": [
            {
                "id": s["id"],
                "name": s["name"],
                "description": s["description"],
                "tables": s["tables"],
            }
            for s in sources
        ]
    }


# ── 接口 4：核心问答接口（v4，多数据源）─────────────────
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

    # ── 返回结构说明（v4）────────────────────────────────
    # 公共字段：
    #   mode: "simple" | "analysis"
    #   conclusion: 分析结论文字
    #   context_summary: 本轮分析焦点摘要（前端下次请求时带回）
    #   intent: 意图识别结果
    #   source_ids: 本次查询涉及的数据源列表，如 ["online", "offline"]
    #
    # simple 模式额外字段：
    #   data: 合并后的查询结果（带 _source_id 标签）
    #   sql: 第一个数据源的 SQL（代表性展示）
    #   source_results: 每个数据源的独立结果 [{source_id, source_name, sql, data}]
    #
    # analysis 模式额外字段：
    #   analysis_type: "anomaly" | "trend" | "comparison" | "distribution"
    #   steps: [{ step, sub_question, purpose, source_id, source_name, sql, data, error? }]
    return result
