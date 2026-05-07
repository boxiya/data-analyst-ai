# ============================================================
# rag_service.py —— RAG 向量检索模块
# 作用：把数据库表结构和业务知识存成向量，根据用户问题检索相关内容
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


def get_collection(name: str):
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection(
        name=name,
        embedding_function=get_embedding_fn(),
    )


# ── 存入表结构 ────────────────────────────────────────────
def index_schema(schema: list[dict]):
    """把数据库字段结构向量化存入 ChromaDB"""
    collection = get_collection("schema")

    existing = collection.get()
    if existing["ids"]:
        collection.delete(ids=existing["ids"])

    documents, ids, metadatas = [], [], []

    for i, row in enumerate(schema):
        comment = row["comment"] if row["comment"] else "无备注"
        text = (
            f"表名：{row['table']}，"
            f"字段名：{row['column']}，"
            f"类型：{row['type']}，"
            f"含义：{comment}"
        )
        documents.append(text)
        ids.append(f"schema_{i}")
        metadatas.append({"table": row["table"], "column": row["column"]})

    collection.add(documents=documents, ids=ids, metadatas=metadatas)
    return len(documents)


# ── 存入业务知识（指标口径 + 术语映射 + 表关联）────────────
def index_knowledge(knowledge: list[dict]):
    """
    把业务知识向量化存入 ChromaDB
    knowledge 格式：[{"type": "metric/term/relation", "text": "..."}]
    """
    collection = get_collection("knowledge")

    # 清空旧数据
    existing = collection.get()
    if existing["ids"]:
        collection.delete(ids=existing["ids"])

    documents, ids, metadatas = [], [], []

    for i, item in enumerate(knowledge):
        documents.append(item["text"])
        ids.append(f"knowledge_{i}")
        metadatas.append({"type": item["type"]})

    collection.add(documents=documents, ids=ids, metadatas=metadatas)
    return len(documents)


# ── 检索相关内容 ──────────────────────────────────────────
def search_schema(question: str, top_k: int = 8) -> str:
    """检索相关字段信息"""
    collection = get_collection("schema")
    if collection.count() == 0:
        return ""

    results = collection.query(
        query_texts=[question],
        n_results=min(top_k, collection.count()),
    )
    docs = results["documents"][0] if results["documents"] else []
    if not docs:
        return ""
    return "【相关表字段】\n" + "\n".join(docs)


def search_knowledge(question: str, top_k: int = 5) -> str:
    """
    检索相关业务知识（指标口径、术语映射、表关联）
    和 search_schema 分开检索，最后拼在一起给 LLM
    """
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
