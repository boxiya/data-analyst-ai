# ============================================================
# main.py —— 后端入口文件（v6）
#
# 这个文件是整个后端的"大门"，负责：
#   1. 启动 FastAPI 服务，监听 8000 端口
#   2. 注册所有 HTTP 接口（路由）
#   3. 每个请求进来后，验证身份、调用对应模块、返回结果
#
# 接口清单：
#   GET  /health      → 健康检查，确认服务是否在跑
#   POST /login       → 用户登录，返回 JWT token
#   POST /init        → 初始化 RAG 向量库（把表结构和业务知识存入 ChromaDB）
#   GET  /sources     → 返回所有数据源列表
#   POST /ask         → 核心问答接口（需要登录）
#   POST /ask/stream  → SSE 流式问答，实时推送每步进度（需要登录）
#   GET  /logs        → 查看操作日志（仅 admin 角色）
#
# 各模块分工：
#   auth.py           → 登录验证、token 生成/解析、日志读写
#   analysis_engine.py→ 核心 AI 分析引擎（数据源识别→意图识别→SQL→结论）
#   db_service.py     → MySQL 数据库连接和查询
#   rag_service.py    → ChromaDB 向量库的读写和检索
#   llm_service.py    → 调用 DeepSeek API 生成 SQL/结论
#   knowledge.py      → 业务知识库（指标定义、SQL规范等）
# ============================================================

import json
import asyncio
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from db_service import get_schema, get_all_schemas, execute_sql, get_sources
from rag_service import index_schema, index_knowledge, search_knowledge, search_schema
from llm_service import generate_sql, generate_conclusion, clarify_question, summarize_context, fix_sql
from knowledge import get_all_knowledge
from analysis_engine import process_question_with_trace
from auth import verify_user, create_token, decode_token, write_log, read_logs

app = FastAPI()

# 允许前端跨域访问（前端跑在 5174 端口，后端跑在 8000 端口，不同端口就是跨域）
# allow_origins=["*"] 表示允许所有来源，生产环境应改为具体域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTTPBearer：从请求 Header 里提取 "Authorization: Bearer <token>"
# auto_error=False 表示没有 token 时不自动报错，由我们自己处理
security = HTTPBearer(auto_error=False)


# ── 认证依赖函数 ──────────────────────────────────────────
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    FastAPI 依赖注入：在需要登录的接口上加 Depends(get_current_user)，
    FastAPI 会自动调用这个函数，从 Header 里取出 token 并验证。

    流程：
      1. 从 Header 取出 token（格式：Authorization: Bearer eyJhbGci...）
      2. 调用 auth.py 的 decode_token() 解析 token
      3. 返回用户信息 {username, role}，后续接口可以直接用

    如果 token 不存在或已过期：
      → 抛出 401 错误，前端收到后自动跳转到登录页
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="未登录，请先登录")
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="token 已过期，请重新登录")
    return {"username": payload.get("sub"), "role": payload.get("role", "viewer")}


def decode_token_from_header(authorization: str):
    """
    从 "Authorization: Bearer xxx" 字符串里提取并解析 token。
    SSE 接口（/ask/stream）无法用 Depends，所以手动解析 Header。
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]  # 去掉 "Bearer " 前缀，剩下就是 token
    return decode_token(token)


# ── 接口 1：健康检查 ──────────────────────────────────────
@app.get("/health")
def health():
    """
    最简单的接口，用来确认后端服务是否正常运行。
    前端启动时可以先 GET /health，如果返回 {"status":"ok"} 说明后端在线。
    """
    return {"status": "ok"}


# ── 接口 2：登录 ──────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/login")
def login(req: LoginRequest):
    """
    用户登录接口。

    流程：
      1. 收到 {username, password}
      2. 调用 auth.py 的 verify_user() 验证密码
      3. 验证通过 → 调用 create_token() 生成 JWT
      4. 返回 token 和用户信息给前端

    前端收到后：
      → 把 token 存入 localStorage["da_token"]
      → 把 username 存入 localStorage["da_user"]
      → 把 role 存入 localStorage["da_role"]
      → 跳转到主界面

    为什么返回 role？
      前端根据 role 决定显示什么：
      admin → 显示"📋 操作日志"按钮
      analyst/viewer → 不显示
    """
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
    """
    初始化 RAG 向量库，把两类知识存入 ChromaDB：
      1. 表结构（schema）：从 MySQL information_schema 读取所有表的字段信息
         → 让 AI 知道"有哪些表、哪些字段、字段是什么类型"
      2. 业务知识（knowledge）：从 knowledge.py 读取指标定义、SQL规范等
         → 让 AI 知道"完成订单怎么定义、GMV怎么算"

    什么时候需要执行 /init？
      - 第一次启动项目时
      - 修改了 knowledge.py 的业务知识后
      - MySQL 表结构发生变化后

    ChromaDB 存在哪里？
      → backend/vector_store/ 目录（已加入 .gitignore，不上传 GitHub）
    """
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
    """
    返回所有注册的数据源信息（从 sources.json 读取）。
    前端可以用这个接口展示"当前系统接了哪些数据库"。
    """
    sources = get_sources()
    return {
        "sources": [
            {"id": s["id"], "name": s["name"], "description": s["description"], "tables": s["tables"]}
            for s in sources
        ]
    }


# ── 数据模型定义 ──────────────────────────────────────────
class Message(BaseModel):
    """对话历史里的一条消息，role 是 user 或 assistant"""
    role: str
    content: str


class AskRequest(BaseModel):
    """
    /ask 和 /ask/stream 接口的请求体。

    question:        用户这次的问题
    history:         最近几轮对话记录（让 AI 理解上下文，支持追问）
    context_summary: 上一轮分析的摘要（比完整 history 更精简，节省 token）
    """
    question: str
    history: Optional[list[Message]] = []
    context_summary: Optional[str] = ""


# ── 接口 5：核心问答（普通 JSON 返回）────────────────────
@app.post("/ask")
def ask(req: AskRequest, current_user: dict = Depends(get_current_user)):
    """
    核心问答接口，一次性返回完整结果（JSON 格式）。

    流程：
      1. get_current_user() 验证 token（Depends 自动执行）
      2. _build_history_text() 把历史对话拼成文本
      3. process_question_with_trace() 调用 AI 分析引擎
      4. _enrich_result() 补充 RAG 溯源标签和上下文摘要
      5. write_log() 写操作日志
      6. 返回完整结果给前端

    返回的 JSON 结构：
      {
        "mode": "simple" | "analysis",
        "conclusion": "分析结论文字",
        "data": [...],           # SQL 查询结果，前端用来画图
        "sql": "SELECT ...",     # 生成的 SQL（简单查询时有）
        "steps": [...],          # 子问题步骤（分析型时有）
        "source_ids": [...],     # 用了哪些数据源
        "rag_sources": [...],    # RAG 检索到的知识片段（溯源）
        "context_summary": "...",# 本轮摘要，下次对话带上
        "intent": {...}          # 意图识别结果
      }
    """
    history_text = _build_history_text(req)
    result, trace_events = process_question_with_trace(req.question, history_text)
    result = _enrich_result(req.question, result)
    write_log(
        username=current_user["username"],
        role=current_user["role"],
        question=req.question,
        mode=result.get("mode", "simple"),
        source_ids=result.get("source_ids", []),
    )
    # 把完整链路数据写入内存缓存，供 /trace/last 接口读取
    _last_trace[current_user["username"]] = _build_trace(
        username=current_user["username"],
        role=current_user["role"],
        question=req.question,
        result=result,
        trace_events=trace_events,
    )
    return result


# ── 接口 6：SSE 流式问答（实时推送每步进度）─────────────
@app.post("/ask/stream")
async def ask_stream(req: AskRequest, request: Request):
    """
    SSE（Server-Sent Events）流式接口，实时推送每步执行进度。

    和 /ask 的区别：
      /ask       → 等所有步骤都完成后，一次性返回结果（用户等待时看不到进度）
      /ask/stream→ 每完成一步就立刻推送一个事件，用户能实时看到"现在在干什么"

    SSE 格式（每个事件是一行 "data: {...}\n\n"）：
      data: {"type":"step","label":"识别数据源","detail":"线上库+线下库","status":"done"}
      data: {"type":"step","label":"生成SQL","detail":"SELECT city...","status":"done"}
      data: {"type":"result","data":{...},"status":"done"}  ← 最后推送完整结果

    为什么 SSE 不能用 Depends(get_current_user)？
      Depends 是同步的，SSE 是异步流，两者不兼容，
      所以手动从 Header 里取 token 解析。
    """
    auth_header = request.headers.get("Authorization", "")
    payload = decode_token_from_header(auth_header)
    if not payload:
        async def err():
            yield _sse({"type": "error", "label": "认证失败", "detail": "请重新登录", "status": "error"})
        return StreamingResponse(err(), media_type="text/event-stream")

    current_user = {"username": payload.get("sub"), "role": payload.get("role", "viewer")}
    history_text = _build_history_text(req)

    async def event_generator():
        loop = asyncio.get_event_loop()
        trace_queue = asyncio.Queue()
        collected_steps = []  # 收集所有步骤事件，用于写入 _last_trace

        def on_trace(event: dict):
            """
            分析引擎每完成一步，调用这个回调函数。
            用 call_soon_threadsafe 把事件放入队列（线程安全）。
            """
            loop.call_soon_threadsafe(trace_queue.put_nowait, event)

        import concurrent.futures
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        # 在线程池里跑同步的分析引擎，不阻塞异步事件循环
        future = loop.run_in_executor(
            executor,
            lambda: process_question_with_trace(req.question, history_text, trace_callback=on_trace)
        )

        # 持续从队列取事件推送，直到分析完成
        while not future.done():
            try:
                event = await asyncio.wait_for(trace_queue.get(), timeout=0.1)
                collected_steps.append(event)  # 同时收集，用于 trace
                yield _sse(event)
            except asyncio.TimeoutError:
                continue  # 没有新事件，继续等待

        # 取出队列里剩余的事件（分析完成后可能还有未推送的）
        while not trace_queue.empty():
            event = trace_queue.get_nowait()
            collected_steps.append(event)
            yield _sse(event)

        # 获取最终结果
        try:
            result, _ = await future
        except Exception as e:
            yield _sse({"type": "error", "label": "执行出错", "detail": str(e), "status": "error"})
            return

        result = _enrich_result(req.question, result)
        write_log(
            username=current_user["username"],
            role=current_user["role"],
            question=req.question,
            mode=result.get("mode", "simple"),
            source_ids=result.get("source_ids", []),
        )
        # 把完整链路数据写入内存缓存，供 /trace/last 接口读取
        # collected_steps 是 SSE 推送过程中收集的所有步骤事件
        _last_trace[current_user["username"]] = _build_trace(
            username=current_user["username"],
            role=current_user["role"],
            question=req.question,
            result=result,
            trace_events=collected_steps,
        )
        # 最后推送完整结果，前端收到 type="result" 后渲染图表和结论
        yield _sse({"type": "result", "data": result, "status": "done"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ── 接口 7：操作日志（仅 admin）──────────────────────────
@app.get("/logs")
def get_logs(limit: int = 50, current_user: dict = Depends(get_current_user)):
    """
    查看操作日志，只有 admin 角色可以访问。

    为什么只有 admin 能看？
      日志里包含所有用户的查询记录，属于敏感信息，
      普通分析师不应该看到其他人查了什么。

    返回格式：
      {"logs": [...], "total": 50}
      每条日志：{"time","username","role","question","mode","source_ids"}
    """
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可查看操作日志")
    logs = read_logs(limit)
    return {"logs": logs, "total": len(logs)}


# ── 工具函数 ──────────────────────────────────────────────
def _build_history_text(req: AskRequest) -> str:
    """
    把前端传来的对话历史拼成文本，注入给 AI 作为上下文。

    为什么需要历史？
      支持追问，比如：
        第1轮：三亚订单量是多少？→ AI 回答
        第2轮：为什么下滑了？    → AI 需要知道上一轮在说三亚

    只取最近6条（3轮对话），避免 token 太长。
    context_summary 是上一轮的精简摘要，比完整历史更省 token。
    """
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
    return "\n\n".join(parts)


def _enrich_result(question: str, result: dict) -> dict:
    """
    在分析引擎返回结果的基础上，补充两样东西：

    1. context_summary（上下文摘要）
       把本轮的问题+结论+SQL 压缩成一句话，
       下次对话时带上，让 AI 知道上一轮在聊什么。

    2. rag_sources（RAG 溯源标签）
       把本次检索到的业务规则返回给前端展示，
       让用户看到"AI 参考了哪些规则得出这个结论"，
       体现可解释性（不是黑盒）。
    """
    # 补充上下文摘要
    conclusion_for_summary = result.get("conclusion", "")
    if result.get("mode") == "analysis" and result.get("steps"):
        sql_for_summary = result["steps"][0].get("sql", "")
    else:
        sql_for_summary = result.get("sql", "")
    result["context_summary"] = summarize_context(
        question=question,
        conclusion=conclusion_for_summary,
        sql=sql_for_summary,
    )

    # 补充 RAG 溯源标签
    try:
        raw_knowledge = search_knowledge(question, top_k=3)
        rag_items = []
        if raw_knowledge:
            lines = raw_knowledge.replace("【相关业务规则】\n", "").strip().split("\n")
            for line in lines:
                line = line.strip()
                if line:
                    # 根据内容判断知识类型，用不同颜色标签展示
                    t = "metric" if any(w in line for w in ["GMV", "金额", "口径", "计算"]) else \
                        "term" if any(w in line for w in ["等于", "定义", "指", "是"]) else "rule"
                    rag_items.append({"type": t, "text": line})
        result["rag_sources"] = rag_items
    except Exception:
        result["rag_sources"] = []

    return result


def _sse(data: dict) -> str:
    """
    把一个字典格式化为 SSE 事件字符串。
    SSE 协议要求格式：以 "data: " 开头，以 "\n\n" 结尾。
    前端用 EventSource 或 fetch + ReadableStream 接收。
    """
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── 全局 trace 缓存（内存，重启清空）────────────────────────
# 每次有用户提问，就把这次请求的完整链路数据写到这里。
# 前端可以通过 GET /trace/last 随时拉取，展示"上一次请求发生了什么"。
#
# 为什么用内存缓存而不是数据库？
#   这是调试/可观测性功能，不需要持久化，重启后重置即可。
#   如果要持久化，可以改成写 jsonl 文件（和 operation_logs.jsonl 一样）。
_last_trace: dict = {}


def _build_trace(username: str, role: str, question: str, result: dict, trace_events: list) -> dict:
    """
    把一次完整请求的所有关键数据打包成 trace 对象。

    包含以下几层：
      1. 身份层   → 谁在请求、用的什么 token（角色）
      2. 意图层   → AI 识别出的问题类型、选了哪些数据源
      3. RAG层    → 检索到了哪些表字段和业务规则
      4. SQL层    → 生成了什么 SQL、执行结果有多少行
      5. 结论层   → AI 生成的分析结论
      6. 日志层   → 写入 operation_logs.jsonl 的那条记录
      7. 执行步骤 → SSE 推送的每一步进度事件
    """
    from datetime import datetime

    # 提取 SQL 信息（简单查询 vs 分析型查询结构不同）
    sql_info = []
    if result.get("mode") == "simple":
        sql_info.append({
            "source_id": (result.get("source_ids") or ["?"])[0],
            "sql": result.get("sql", ""),
            "row_count": len(result.get("data") or []),
            "error": None,
        })
    elif result.get("mode") == "analysis":
        for step in (result.get("steps") or []):
            sql_info.append({
                "step": step.get("step"),
                "sub_question": step.get("sub_question"),
                "source_id": step.get("source_id"),
                "sql": step.get("sql", ""),
                "row_count": len(step.get("data") or []),
                "error": step.get("error"),
            })

    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # ① 身份层
        "identity": {
            "username": username,
            "role": role,
            "token_info": f"JWT HS256，角色={role}，8小时有效",
        },
        # ② 意图层
        "intent": {
            "question": question,
            "detected": result.get("intent", {}),
            "source_ids": result.get("source_ids", []),
            "mode": result.get("mode", "simple"),
            "analysis_type": result.get("analysis_type"),
        },
        # ③ RAG层
        "rag": {
            "knowledge_hits": result.get("rag_sources", []),
            "knowledge_count": len(result.get("rag_sources") or []),
            "stored_in": "backend/vector_store/（ChromaDB，本地文件）",
        },
        # ④ SQL层
        "sql": sql_info,
        # ⑤ 结论层
        "conclusion": {
            "text": result.get("conclusion", ""),
            "context_summary": result.get("context_summary", ""),
        },
        # ⑥ 日志层
        "log_record": {
            "file": "backend/operation_logs.jsonl",
            "written": {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "username": username,
                "role": role,
                "question": question,
                "mode": result.get("mode", "simple"),
                "source_ids": result.get("source_ids", []),
            },
        },
        # ⑦ 执行步骤（SSE 推送的每一步）
        "steps": trace_events,
    }


# ── 接口 8：全链路追踪（登录用户均可查看自己的上一次请求）──
@app.get("/trace/last")
def get_last_trace(current_user: dict = Depends(get_current_user)):
    """
    返回当前用户最近一次请求的完整链路数据。

    包含：身份验证 → 数据源识别 → RAG检索 → SQL生成 → SQL执行 → 结论生成 → 日志写入
    每一层都说明"产生了什么数据、存在哪里"。

    前端用这个接口驱动 TracePanel 组件，让用户看到完整的请求链路。
    """
    username = current_user["username"]
    # 只返回当前用户自己的 trace（隐私隔离）
    trace = _last_trace.get(username)
    if not trace:
        return {"trace": None, "message": "还没有查询记录，先提一个问题吧"}
    return {"trace": trace}
