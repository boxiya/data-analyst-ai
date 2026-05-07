# ============================================================
# main.py —— 后端入口文件
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from db_service import get_schema, execute_sql
from rag_service import index_schema, index_knowledge, search_schema, search_knowledge
from llm_service import generate_sql, generate_conclusion
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
# 把表结构 + 业务知识全部存入向量库，只需执行一次
@app.post("/init")
def init():
    # 存表结构
    schema = get_schema()
    schema_count = index_schema(schema)

    # 存业务知识（指标口径 + 术语映射 + 表关联）
    knowledge = get_all_knowledge()
    knowledge_count = index_knowledge(knowledge)

    return {
        "message": f"初始化完成：表结构 {schema_count} 个字段，业务知识 {knowledge_count} 条"
    }


# ── 接口 3：核心问答接口 ──────────────────────────────────
class AskRequest(BaseModel):
    question: str

@app.post("/ask")
def ask(req: AskRequest):

    # 第一步：RAG 同时检索字段信息和业务知识
    schema_context = search_schema(req.question)
    knowledge_context = search_knowledge(req.question)

    # 把两部分拼在一起给 LLM
    # 例如用户问"完成订单金额"，会同时检索到：
    # - 字段信息：orders.amount、orders.status
    # - 业务规则：完成订单 = status='completed'，不含取消和退款
    full_context = "\n\n".join(filter(None, [schema_context, knowledge_context]))

    # 第二步：LLM 生成 SQL
    sql = generate_sql(req.question, full_context)

    # 第三步：执行 SQL
    try:
        data = execute_sql(sql)
    except Exception as e:
        return {"error": str(e), "sql": sql}

    # 第四步：LLM 生成结论
    conclusion = generate_conclusion(req.question, data)

    return {
        "conclusion": conclusion,
        "data": data,
        "sql": sql,
    }
