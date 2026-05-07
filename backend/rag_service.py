# ============================================================
# rag_service.py —— RAG 向量检索模块
# 作用：把数据库表结构存成向量，根据用户问题检索相关字段
#
# 什么是 RAG？
# RAG = Retrieval-Augmented Generation（检索增强生成）
# 简单说：在调用 LLM 之前，先从知识库里检索相关信息，
# 把检索结果一起塞进 prompt，让 LLM 回答得更准确
#
# 在这个项目里：
# 知识库 = 数据库表结构（哪个表有哪些字段，字段是什么含义）
# 检索 = 根据用户问题，找出最相关的字段
# 目的 = 告诉 LLM "你可以用这些表和字段来写 SQL"
# ============================================================

import os
import chromadb
from chromadb.utils import embedding_functions

# ChromaDB 数据存储路径（本地文件夹，持久化保存）
CHROMA_PATH = os.path.join(os.path.dirname(__file__), "vector_store")

# 嵌入模型（全局只初始化一次，避免重复加载）
# 嵌入模型的作用：把文字转成一串数字（向量），相似的文字转出来的向量也相似
# 例如"订单金额"和"amount字段"会被转成相近的向量
_embedding_fn = None

def get_embedding_fn():
    """
    获取嵌入模型（懒加载，第一次调用时才初始化）
    使用 paraphrase-multilingual-MiniLM-L12-v2 模型，支持中文
    首次运行会自动从网上下载模型文件（约 90MB），之后会缓存
    """
    global _embedding_fn
    if _embedding_fn is None:
        _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )
    return _embedding_fn


def get_collection(name: str):
    """
    获取或创建一个 ChromaDB 集合（类似数据库里的"表"）
    如果集合不存在会自动创建，存在则直接返回
    """
    client = chromadb.PersistentClient(path=CHROMA_PATH)  # 持久化到本地文件
    return client.get_or_create_collection(
        name=name,
        embedding_function=get_embedding_fn(),  # 指定用哪个模型做向量化
    )


def index_schema(schema: list[dict]):
    """
    把数据库表结构向量化存入 ChromaDB
    这个函数在 /init 接口被调用，只需执行一次

    原理：
    1. 把每个字段描述成一句话，例如：
       "表名：orders，字段名：amount，类型：decimal(10,2)，含义：订单金额"
    2. 用嵌入模型把这句话转成向量（一串数字）
    3. 把向量存入 ChromaDB

    之后用户提问时，问题也会被转成向量，
    ChromaDB 会找出和问题向量最相似的字段描述，返回给我们
    """
    collection = get_collection("schema")

    # 先清空旧数据，避免重复存储
    existing = collection.get()
    if existing["ids"]:
        collection.delete(ids=existing["ids"])

    documents = []   # 存字段描述文本
    ids = []         # 每条记录的唯一 ID
    metadatas = []   # 存额外信息（表名、字段名），方便后续过滤

    for i, row in enumerate(schema):
        comment = row["comment"] if row["comment"] else "无备注"

        # 把字段信息拼成一句自然语言描述
        # 这样做是因为嵌入模型对自然语言理解更好
        text = (
            f"表名：{row['table']}，"
            f"字段名：{row['column']}，"
            f"类型：{row['type']}，"
            f"含义：{comment}"
        )
        documents.append(text)
        ids.append(f"schema_{i}")
        metadatas.append({"table": row["table"], "column": row["column"]})

    # 批量存入 ChromaDB（内部会自动调用嵌入模型转成向量）
    collection.add(documents=documents, ids=ids, metadatas=metadatas)
    return len(documents)


def search_schema(question: str, top_k: int = 10) -> str:
    """
    根据用户问题检索最相关的字段信息
    返回拼接好的字符串，直接塞进 LLM 的 prompt

    原理：
    1. 把用户问题转成向量
    2. 在 ChromaDB 里计算和所有字段向量的相似度
    3. 返回相似度最高的 top_k 个字段描述

    例如用户问"各城市订单金额"，
    会检索到 orders.city（城市）、orders.amount（订单金额）等相关字段
    """
    collection = get_collection("schema")

    # 如果向量库是空的（还没执行 /init），直接返回空字符串
    if collection.count() == 0:
        return ""

    results = collection.query(
        query_texts=[question],
        n_results=min(top_k, collection.count()),
        include=["documents", "distances"]  # 加上这行
    )

    # 打印出来看看
    print(results["distances"])  # 数值越小越相似（距离 = 1 - 相似度）

    docs = results["documents"][0] if results["documents"] else []
    if not docs:
        return ""

    # 把检索到的字段描述拼成一段文字，后面会塞进 LLM 的 prompt
    return "【相关表字段信息】\n" + "\n".join(docs)
