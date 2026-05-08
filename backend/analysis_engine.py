# ============================================================
# analysis_engine.py —— 分析引擎（v2，多数据源）
#
# v1 流程（单库）：
#   用户问题 → 意图识别 → [简单查询 | 分析型多步查询] → 结论
#
# v2 升级（多数据源）：
#   用户问题
#     ↓ 数据源识别（detect_sources）：判断查哪些库
#     ↓ 意图识别（detect_intent）：判断走哪条路径
#     ↓ 如果是简单查询：
#       ↓ 对每个数据源分别 RAG + 生成SQL + 执行SQL
#       ↓ pandas 合并结果 → 生成结论
#     ↓ 如果是分析型：
#       ↓ 问题拆解（子问题带数据源标注）
#       ↓ 对每个子问题路由到正确的库执行
#       ↓ pandas 合并跨库结果 → 综合结论
#
# 核心设计：
# - 每个子查询结果都带 source_id 标签，合并时不丢失来源信息
# - pandas 用于跨库数据合并（如线上+线下总GMV）
# - LLM 生成 SQL 时 prompt 里明确告知当前操作的是哪个库
# ============================================================

import pandas as pd
from db_service import execute_sql, get_sources, get_source_by_id
from rag_service import search_schema, search_knowledge
from llm_service import (
    generate_sql,
    generate_conclusion,
    detect_intent,
    detect_sources,
    decompose_question,
    synthesize_analysis,
)


# ── 工具函数 ──────────────────────────────────────────────

def _merge_results(results_by_source: list[dict]) -> list[dict]:
    """
    用 pandas 合并多个数据源的查询结果。
    每条记录加上 _source_id 和 _source_name 标签，方便 LLM 区分来源。
    
    results_by_source 格式：
    [
        {"source_id": "online", "source_name": "线上业务库", "data": [...]},
        {"source_id": "offline", "source_name": "线下门店库", "data": [...]},
    ]
    """
    dfs = []
    for item in results_by_source:
        if item.get("data"):
            df = pd.DataFrame(item["data"])
            df["_source_id"] = item["source_id"]
            df["_source_name"] = item["source_name"]
            dfs.append(df)
    
    if not dfs:
        return []
    
    merged = pd.concat(dfs, ignore_index=True)
    # 把 NaN 替换成 None，避免 JSON 序列化问题
    merged = merged.where(pd.notnull(merged), None)
    return merged.to_dict(orient="records")


def _build_source_context(source: dict) -> str:
    """构建数据源上下文说明，注入 SQL 生成 prompt"""
    return (
        f"【当前数据源】\n"
        f"数据库名：{source['database']}\n"
        f"数据源说明：{source['description']}\n"
        f"包含的表：{', '.join(source['tables'])}\n"
        f"注意：生成的 SQL 只能查询以上表，不能跨库 JOIN"
    )


# ── 简单查询路径（v2：多数据源）─────────────────────────
def run_simple_query(question: str, source_ids: list[str], history_text: str = "") -> dict:
    """
    简单查询路径：对每个数据源分别生成 SQL 并执行，最后合并结果。
    
    返回格式：
    {
        "mode": "simple",
        "conclusion": "...",
        "data": [...],          # 合并后的数据（带 _source_id 标签）
        "sql": "...",           # 第一个数据源的 SQL（代表性展示）
        "source_results": [...] # 每个数据源的独立结果
    }
    """
    sources = get_sources()
    target_sources = [s for s in sources if s["id"] in source_ids]
    
    # 如果没有匹配的数据源，退化到默认库
    if not target_sources:
        target_sources = sources[:1]

    source_results = []
    all_sqls = []

    for source in target_sources:
        # RAG 检索（只检索该数据源的字段）
        schema_context = search_schema(question, source_filter=source["id"])
        knowledge_context = search_knowledge(question)
        source_context = _build_source_context(source)
        full_context = "\n\n".join(filter(None, [source_context, schema_context, knowledge_context]))

        if history_text:
            full_context = f"{history_text}\n\n{full_context}"

        # 生成并执行 SQL
        sql = generate_sql(question, full_context)
        all_sqls.append(sql)

        try:
            data = execute_sql(sql, database=source["database"])
            source_results.append({
                "source_id": source["id"],
                "source_name": source["name"],
                "sql": sql,
                "data": data,
            })
        except Exception as e:
            source_results.append({
                "source_id": source["id"],
                "source_name": source["name"],
                "sql": sql,
                "data": [],
                "error": str(e),
            })

    # 合并所有数据源的结果
    merged_data = _merge_results(source_results)

    # 生成结论（把所有数据源的结果都给 LLM）
    conclusion = generate_conclusion(question, merged_data, history_text)

    return {
        "mode": "simple",
        "conclusion": conclusion,
        "data": merged_data,
        "sql": all_sqls[0] if all_sqls else "",
        "source_results": source_results,
    }


# ── 分析型查询路径（v2：多数据源）───────────────────────
def run_analysis(question: str, analysis_type: str, source_ids: list[str], history_text: str = "") -> dict:
    """
    分析型问题路径：自动拆子问题 → 多库查询 → pandas 合并 → 综合结论。
    
    v2 升级：
    - 子问题拆解时告知可用数据源
    - 每个子问题路由到正确的库执行
    - 跨库结果用 pandas 合并后再综合分析
    
    返回格式：
    {
        "mode": "analysis",
        "analysis_type": "anomaly" | "trend" | "comparison" | "distribution",
        "steps": [
            {
                "step": 1,
                "sub_question": "...",
                "purpose": "...",
                "source_id": "online",
                "sql": "...",
                "data": [...],
                "error": "..."  # 可选
            },
            ...
        ],
        "conclusion": "综合分析报告",
        "source_ids": ["online", "offline"]  # 本次涉及的数据源
    }
    """
    sources = get_sources()
    target_sources = [s for s in sources if s["id"] in source_ids]
    if not target_sources:
        target_sources = sources[:1]

    # 先用 RAG 检索一次（所有数据源），给问题拆解提供表结构参考
    schema_context = search_schema(question)

    # 第一步：把大问题拆成多个子问题
    sub_questions = decompose_question(question, analysis_type, schema_context, history_text)

    # 第二步：对每个子问题，路由到正确的库执行
    sub_results = []
    for sq in sub_questions:
        sub_q = sq.get("sub_question", question)
        purpose = sq.get("purpose", "")
        step_num = sq.get("step", len(sub_results) + 1)
        # 子问题可以指定数据源，也可以不指定（自动路由）
        sub_source_id = sq.get("source_id")

        # 确定这个子问题查哪个库
        if sub_source_id and sub_source_id in source_ids:
            sub_source = get_source_by_id(sub_source_id)
            sub_sources_to_query = [sub_source] if sub_source else target_sources
        else:
            # 没有指定数据源，对所有目标数据源都查一遍
            sub_sources_to_query = target_sources

        # 对每个数据源执行子查询
        sub_source_results = []
        for source in sub_sources_to_query:
            sub_schema = search_schema(sub_q, source_filter=source["id"])
            sub_knowledge = search_knowledge(sub_q)
            source_context = _build_source_context(source)
            sub_context = "\n\n".join(filter(None, [source_context, sub_schema, sub_knowledge]))

            sql = generate_sql(sub_q, sub_context, history_text)

            try:
                data = execute_sql(sql, database=source["database"])
                sub_source_results.append({
                    "source_id": source["id"],
                    "source_name": source["name"],
                    "data": data,
                })
                # 记录第一个成功的 SQL 作为代表
                if not any(r.get("sql") for r in sub_results if r.get("step") == step_num):
                    pass
                sub_results.append({
                    "step": step_num,
                    "sub_question": sub_q,
                    "purpose": purpose,
                    "source_id": source["id"],
                    "source_name": source["name"],
                    "sql": sql,
                    "data": data,
                })
            except Exception as e:
                sub_results.append({
                    "step": step_num,
                    "sub_question": sub_q,
                    "purpose": purpose,
                    "source_id": source["id"],
                    "source_name": source["name"],
                    "sql": sql,
                    "data": [],
                    "error": str(e),
                })

    # 第三步：综合所有子查询结果，生成结构化分析报告
    conclusion = synthesize_analysis(question, sub_results, analysis_type, history_text)

    return {
        "mode": "analysis",
        "analysis_type": analysis_type,
        "steps": sub_results,
        "conclusion": conclusion,
        "source_ids": source_ids,
    }


# ── 统一入口（v2：多数据源）──────────────────────────────
def process_question(question: str, history_text: str = "") -> dict:
    """
    统一入口：自动判断数据源 + 走简单查询还是分析引擎。
    
    v2 升级：在意图识别之前，先做数据源识别，
    让后续所有步骤都知道要查哪些库。
    """
    sources = get_sources()

    # ── 第一步：数据源识别 ────────────────────────────────
    # 判断这个问题需要查哪些数据源
    source_ids = detect_sources(question, sources, history_text)

    # ── 第二步：意图识别 ──────────────────────────────────
    intent = detect_intent(question, history_text)
    intent_type = intent.get("type", "simple_query")
    analysis_type = intent.get("analysis_type")

    # ── 第三步：路由到对应路径 ────────────────────────────
    if intent_type == "analysis" and analysis_type:
        result = run_analysis(question, analysis_type, source_ids, history_text)
    else:
        result = run_simple_query(question, source_ids, history_text)

    # 把意图识别和数据源识别结果都带回去，前端可以展示
    result["intent"] = intent
    result["source_ids"] = source_ids
    return result
