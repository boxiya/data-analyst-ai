# ============================================================
# main.py —— 后端入口文件
# 作用：定义所有 HTTP 接口，把各个模块串联起来
# 启动命令：uvicorn main:app --reload
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 导入我们自己写的模块
from db_service import get_schema, execute_sql      # MySQL 相关
from rag_service import index_schema, search_schema  # 向量检索相关
from llm_service import generate_sql, generate_conclusion  # LLM 相关

# 创建 FastAPI 应用实例
app = FastAPI()

# 允许前端跨域访问
# 因为前端运行在 localhost:5173，后端在 localhost:8000，端口不同就是"跨域"
# 不加这个配置，浏览器会拦截前端发出的请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # * 表示允许所有来源，生产环境应该改成具体域名
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 接口 1：健康检查 ──────────────────────────────────────
# 用来确认后端是否正常运行，访问 /health 返回 ok 就说明服务活着
@app.get("/health")
def health():
    return {"status": "ok"}


# ── 接口 2：初始化 RAG ────────────────────────────────────
# 这个接口只需要在第一次使用时调用一次
# 作用：读取 MySQL 里所有表的字段信息，存入 ChromaDB 向量库
# 之后用户提问时，系统会从这个向量库里检索相关字段，告诉 LLM 有哪些表可以用
@app.post("/init")
def init():
    # 第一步：从 MySQL 的 information_schema 读取所有表结构
    schema = get_schema()
    # 第二步：把表结构向量化存入 ChromaDB
    count = index_schema(schema)
    return {"message": f"表结构已存入 RAG，共 {count} 个字段"}


# ── 接口 3：核心问答接口 ──────────────────────────────────
# 这是整个系统最核心的接口，完整流程：
# 用户问题 → RAG检索相关字段 → LLM生成SQL → 执行SQL → LLM生成结论 → 返回前端

# 定义请求体的数据格式（用户只需要传一个 question 字段）
class AskRequest(BaseModel):
    question: str  # 用户的自然语言问题，例如"各城市订单总金额是多少"

@app.post("/ask")
def ask(req: AskRequest):

    # 第一步：RAG 检索
    # 把用户问题转成向量，在 ChromaDB 里找最相似的字段描述
    # 例如用户问"订单金额"，会检索到 orders 表的 amount 字段
    schema_context = search_schema(req.question)

    # 第二步：LLM 生成 SQL
    # 把"用户问题 + 检索到的字段信息"一起发给 DeepSeek
    # LLM 根据这些信息生成对应的 SQL 查询语句
    sql = generate_sql(req.question, schema_context)

    # 第三步：执行 SQL
    # 拿着 LLM 生成的 SQL 去 MySQL 里真实查询数据
    try:
        data = execute_sql(sql)
    except Exception as e:
        # 如果 SQL 执行报错，把错误和 SQL 都返回，方便调试
        return {"error": str(e), "sql": sql}

    # 第四步：LLM 生成结论
    # 把查询结果发给 LLM，让它用自然语言解释数据含义
    conclusion = generate_conclusion(req.question, data)

    # 返回三个内容给前端：
    # - conclusion：LLM 的文字分析结论
    # - data：原始查询数据（前端会渲染成表格）
    # - sql：生成的 SQL（前端可以展开查看，方便理解和调试）
    return {
        "conclusion": conclusion,
        "data": data,
        "sql": sql,
    }
