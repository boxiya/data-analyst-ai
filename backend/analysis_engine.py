# ============================================================
# analysis_engine.py —— 分析引擎（解决差距二、三）
#
# 这个文件是整个系统升级的核心。
#
# 原来的流程（单轮）：
#   用户问题 → RAG → LLM生成SQL → 执行SQL → LLM生成结论
#
# 升级后的流程（分析型问题）：
#   用户问题
#     ↓ 意图识别（是简单查询还是分析型问题？）
#     ↓ 如果是分析型：
#       ↓ 问题拆解（拆成2-4个子问题）
#       ↓ 对每个子问题：RAG检索 → 生成SQL → 执行SQL
#       ↓ 综合所有子查询结果 → 生成结构化分析报告
#     ↓ 如果是简单查询：
#       ↓ 走原来的单轮路径
#
# 这样就实现了：
# - 差距二：系统能自己拆问题、多次查询（有分析能力）
# - 差距三：结论有方法论框架（异动归因/趋势/对比/分布）
# ============================================================

from db_service import execute_sql
from rag_service import search_schema, search_knowledge
from llm_service import (
    generate_sql,
    generate_conclusion,
    detect_intent,
    decompose_question,
    synthesize_analysis,
)


def run_simple_query(question: str, history_text: str = "") -> dict:
    """
    简单查询路径：一条 SQL 回答一个问题。
    这是原来的流程，保持不变。
    
    返回格式：
    {
        "mode": "simple",
        "conclusion": "...",
        "data": [...],
        "sql": "...",
        "error": "..."  # 可选
    }
    """
    # RAG 检索
    schema_context = search_schema(question)
    knowledge_context = search_knowledge(question)
    full_context = "\n\n".join(filter(None, [schema_context, knowledge_context]))

    # 生成并执行 SQL
    sql = generate_sql(question, full_context, history_text)
    try:
        data = execute_sql(sql)
    except Exception as e:
        return {
            "mode": "simple",
            "error": str(e),
            "sql": sql,
            "data": [],
            "conclusion": "",
        }

    # 生成结论
    conclusion = generate_conclusion(question, data, history_text)

    return {
        "mode": "simple",
        "conclusion": conclusion,
        "data": data,
        "sql": sql,
    }


def run_analysis(question: str, analysis_type: str, history_text: str = "") -> dict:
    """
    分析型问题路径：自动拆子问题 → 多次查询 → 综合结论。
    
    返回格式：
    {
        "mode": "analysis",
        "analysis_type": "anomaly" | "trend" | "comparison" | "distribution",
        "steps": [
            {
                "step": 1,
                "sub_question": "...",
                "purpose": "...",
                "sql": "...",
                "data": [...],
                "error": "..."  # 可选
            },
            ...
        ],
        "conclusion": "综合分析报告",
    }
    """
    # 先用 RAG 检索一次，给问题拆解提供表结构参考
    schema_context = search_schema(question)

    # 第一步：把大问题拆成多个子问题
    sub_questions = decompose_question(question, analysis_type, schema_context, history_text)

    # 第二步：对每个子问题，分别 RAG + 生成SQL + 执行SQL
    sub_results = []
    for sq in sub_questions:
        sub_q = sq.get("sub_question", question)
        purpose = sq.get("purpose", "")
        step_num = sq.get("step", len(sub_results) + 1)

        # 每个子问题单独检索（检索词更精准）
        sub_schema = search_schema(sub_q)
        sub_knowledge = search_knowledge(sub_q)
        sub_context = "\n\n".join(filter(None, [sub_schema, sub_knowledge]))

        # 生成 SQL（子问题不带 history，避免干扰）
        sql = generate_sql(sub_q, sub_context, history_text)

        # 执行 SQL
        try:
            data = execute_sql(sql)
            sub_results.append({
                "step": step_num,
                "sub_question": sub_q,
                "purpose": purpose,
                "sql": sql,
                "data": data,
            })
        except Exception as e:
            sub_results.append({
                "step": step_num,
                "sub_question": sub_q,
                "purpose": purpose,
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
    }


def process_question(question: str, history_text: str = "") -> dict:
    """
    统一入口：自动判断走简单查询还是分析引擎。
    
    这是 main.py 调用的唯一接口，屏蔽了内部路径选择的复杂性。
    """
    # 意图识别
    intent = detect_intent(question, history_text)
    intent_type = intent.get("type", "simple_query")
    analysis_type = intent.get("analysis_type")

    if intent_type == "analysis" and analysis_type:
        # 走分析引擎路径
        result = run_analysis(question, analysis_type, history_text)
    else:
        # 走简单查询路径
        result = run_simple_query(question, history_text)

    # 把意图识别结果也带回去，前端可以展示"分析模式"标签
    result["intent"] = intent
    return result
