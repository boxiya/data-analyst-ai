# ============================================================
# main.py —— 后端入口文件（v5）
#
# v5 新增：
# - /login 接口：用户名+密码登录，返回 JWT token
# - /ask 接口：需要 Bearer token 认证，记录操作日志
# - /logs 接口：管理员查看操作日志（仅 admin 角色）
# - /ask 返回 rag_sources 字段：RAG 检索到的知识片段（溯源）
# ============================================================

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
from db_service import get_schema, get_all_schemas, execute_sql, get_sources
from rag_service import index_schema, index_knowledge, search_knowledge, search_schema
from llm_service import generate_sql, generate_conclusion, clarify_question, summarize_context
from knowledge import get_all_knowledge
from analysis_engine import process_question
from auth import verify_user, create_token, decode_token, write_log, read_logs

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)


# ── 认证依赖：从 Bearer token 解析当前用户 ────────────────
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="未登录，请先登录")
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="token 已过期，请重新登录")
    return {"username": payload.get("sub"), "role": payload.get("role", "viewer")}


# ── 接口 1：健康检查 ──────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


# ── 接口 2：登录 ──────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/login")
def login(req: LoginRequest):
    user = verify_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token(user["username"], user["role"])
    return {
        "token": token,
        "username": user["username"],
        "role": user["role"],
        "display": user.get("display", user["username"]),
    }


# ── 接口 3：初始化 RAG ────────────────────────────────────
@app.post("/init")
def init():
    schema = get_all_schemas()
    schema_count = index_schema(schema)
    knowledge = get_all_knowledge()
    knowledge_count = index_knowledge(knowledge)

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


# ── 接口 4：数据源列表 ────────────────────────────────────
@app.get("/sources")
def list_sources():
    sources = get_sources()
    return {
        "sources": [
            {"id": s["id"], "name": s["name"], "description": s["description"], "tables": s["tables"]}
            for s in sources
        ]
    }


# ── 接口 5：核心问答（需要登录）──────────────────────────
class Message(BaseModel):
    role: str
    content: str


class AskRequest(BaseModel):
    question: str
    history: Optional[list[Message]] = []
    context_summary: Optional[str] = ""


@app.post("/ask")
def ask(req: AskRequest, current_user: dict = Depends(get_current_user)):
    # 构建 history_text
    parts = []
    if req.context_summary:
        parts.append(f"【上一轮分析焦点】\n{req.context_summary}")
    if req.history:
        lines = []
        for msg in req.history[-6:]:
            if msg.role == "user":
                lines.append(f"用户：{msg.content}")
            elif msg.role == "assistant":
                lines.append(f"助手：{msg.content}")
        if lines:
            parts.append("【历史对话】\n" + "\n".join(lines))
    history_text = "\n\n".join(parts)

    # 调用分析引擎
    result = process_question(req.question, history_text)

    # 生成上下文摘要
    conclusion_for_summary = result.get("conclusion", "")
    if result.get("mode") == "analysis" and result.get("steps"):
        sql_for_summary = result["steps"][0].get("sql", "")
    else:
        sql_for_summary = result.get("sql", "")

    new_summary = summarize_context(
        question=req.question,
        conclusion=conclusion_for_summary,
        sql=sql_for_summary,
    )
    result["context_summary"] = new_summary

    # ── RAG 溯源：把本次检索到的知识片段返回给前端 ──────
    # 让用户看到"AI 参考了哪些业务规则"，体现可解释性
    try:
        raw_knowledge = search_knowledge(req.question, top_k=3)
        # 把字符串拆成列表，每条知识片段单独展示
        rag_items = []
        if raw_knowledge:
            # search_knowledge 返回的是带前缀的字符串，去掉前缀后按行拆分
            lines = raw_knowledge.replace("【相关业务规则】\n", "").strip().split("\n")
            for line in lines:
                line = line.strip()
                if line:
                    # 判断知识类型（metric/term/relation）
                    t = "metric" if any(w in line for w in ["GMV", "金额", "口径", "计算"]) else \
                        "term" if any(w in line for w in ["等于", "定义", "指", "是"]) else "rule"
                    rag_items.append({"type": t, "text": line})
        result["rag_sources"] = rag_items
    except Exception:
        result["rag_sources"] = []

    # ── 写操作日志 ────────────────────────────────────────
    write_log(
        username=current_user["username"],
        role=current_user["role"],
        question=req.question,
        mode=result.get("mode", "simple"),
        source_ids=result.get("source_ids", []),
    )

    return result


# ── 接口 6：操作日志（仅 admin）──────────────────────────
@app.get("/logs")
def get_logs(limit: int = 50, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可查看操作日志")
    logs = read_logs(limit)
    return {"logs": logs, "total": len(logs)}
