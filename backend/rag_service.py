# ============================================================
# rag_service.py —— RAG 向量检索模块（v2，多数据源）
#
# v1：schema 索引不区分数据源，LLM 不知道字段属于哪个库
# v2 升级：
# - index_schema 接受带 source_id/database 标签的字段列表
# - 向量文本里加入"数据源：xxx库"前缀，让 LLM 知道字段归属
# - metadata 里存 source_id 和 database，支持按库过滤检索
# - search_schema 支持 source_filter 参数，可只检索某个库的字段
# ============================================================

import os
import chromadb
from chromadb.utils import embedding_functions

CHROMA_PATH = os.path.join(os.path.dirname(__file__), "vector_store")

_embedding_fn = None

def get_embedding_fn():
    global _embedding_fn
    if _embedding_fn is None:
        _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )
    return _embedding_fn


def get_client():
    return chromadb.PersistentClient(path=CHROMA_PATH)


def get_collection(name: str):
    client = get_client()
    return client.get_or_create_collection(
        name=name,
        embedding_function=get_embedding_fn(),
    )


def reset_collection(name: str):
    """删除并重建 collection，彻底清空旧数据。"""
    client = get_client()
    try:
        client.delete_collection(name=name)
    except Exception:
        pass
    return client.create_collection(
        name=name,
        embedding_function=get_embedding_fn(),
    )


# ── 存入表结构（v2：带数据源标签）────────────────────────
def index_schema(schema: list[dict]):
    """
    把数据库字段结构向量化存入 ChromaDB。
    
    v2 升级：schema 列表里的每条记录可以带 source_id / database / source_name 字段。
    如果有这些字段，向量文本里会加入"数据源"前缀，让 LLM 知道字段属于哪个库。
    
    支持两种输入格式：
    1. 旧格式（单库）：{"table": "orders", "column": "amount", "type": "...", "comment": "..."}
    2. 新格式（多库）：{"source_id": "online", "database": "meituan_travel", "source_name": "线上业务库",
                        "table": "orders", "column": "amount", "type": "...", "comment": "..."}
    """
    collection = reset_collection("schema")

    documents, ids, metadatas = [], [], []

    for i, row in enumerate(schema):
        comment = row.get("comment") or "无备注"
        
        # v2：如果有数据源信息，加入向量文本
        source_prefix = ""
        if row.get("source_name"):
            source_prefix = f"数据源：{row['source_name']}（{row.get('database', '')}），"
        
        text = (
            f"{source_prefix}"
            f"表名：{row['table']}，"
            f"字段名：{row['column']}，"
            f"类型：{row['type']}，"
            f"含义：{comment}"
        )
        documents.append(text)
        ids.append(f"schema_{i}")
        
        # metadata 里存 source_id 和 database，支持按库过滤
        metadatas.append({
            "table": row["table"],
            "column": row["column"],
            "source_id": row.get("source_id", "default"),
            "database": row.get("database", ""),
        })

    collection.add(documents=documents, ids=ids, metadatas=metadatas)
    return len(documents)


# ── 存入业务知识 ──────────────────────────────────────────
def index_knowledge(knowledge: list[dict]):
    """
    把业务知识向量化存入 ChromaDB。
    knowledge 格式：[{"type": "metric/term/relation", "text": "..."}]
    """
    collection = reset_collection("knowledge")

    documents, ids, metadatas = [], [], []

    for i, item in enumerate(knowledge):
        documents.append(item["text"])
        ids.append(f"knowledge_{i}")
        metadatas.append({"type": item["type"]})

    collection.add(documents=documents, ids=ids, metadatas=metadatas)
    return len(documents)


# ── 检索相关内容 ──────────────────────────────────────────
def search_schema(question: str, top_k: int = 10, source_filter: str = None) -> str:
    """
    检索相关字段信息。
    
    source_filter：可选，传入 source_id（如 "online" 或 "offline"）
    只检索该数据源的字段，用于精准路由场景。
    不传则检索所有数据源的字段。
    """
    collection = get_collection("schema")
    if collection.count() == 0:
        return ""

    query_kwargs = {
        "query_texts": [question],
        "n_results": min(top_k, collection.count()),
    }
    
    # 如果指定了数据源过滤
    if source_filter:
        query_kwargs["where"] = {"source_id": source_filter}

    results = collection.query(**query_kwargs)
    docs = results["documents"][0] if results["documents"] else []
    if not docs:
        return ""
    return "【相关表字段】\n" + "\n".join(docs)


def search_knowledge(question: str, top_k: int = 5) -> str:
    """检索相关业务知识（指标口径、术语映射、表关联）"""
    collection = get_collection("knowledge")
    if collection.count() == 0:
        return ""

    results = collection.query(
        query_texts=[question],
        n_results=min(top_k, collection.count()),
    )
    docs = results["documents"][0] if results["documents"] else []
    if not docs:
        return ""
    return "【相关业务规则】\n" + "\n".join(docs)
