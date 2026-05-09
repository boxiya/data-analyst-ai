# ============================================================
# analysis_engine.py —— AI 分析引擎（核心大脑）
#
# 这个文件是整个系统最核心的部分，负责把用户的自然语言问题
# 转化为 SQL 查询，执行后生成分析结论。
#
# 完整流程：
#   用户问题
#     ↓ detect_sources()      判断查哪个库（线上/线下/都查）
#     ↓ detect_intent()       判断问题类型（简单查询 or 分析型）
#     ↓ 如果是简单查询：
#         search_schema()     RAG检索相关字段
#         search_knowledge()  RAG检索业务规则
#         generate_sql()      DeepSeek生成SQL
#         execute_sql()       MySQL执行SQL
#         generate_conclusion() DeepSeek生成结论
#     ↓ 如果是分析型（异动/趋势/对比/分布）：
#         decompose_question() 拆成多个子问题
#         对每个子问题重复上面的 RAG→SQL→执行 流程
#         synthesize_analysis() DeepSeek综合所有结果生成报告
#
# 两种查询模式的区别：
#   simple_query → 一个问题一条SQL，适合"昨天订单量是多少"
#   analysis     → 拆成多步，适合"三亚订单为什么下滑"（需要多角度分析）
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
    fix_sql,
)


# ── 工具函数 ──────────────────────────────────────────────

def _merge_results(results_by_source: list[dict]) -> list[dict]:
    """
    用 pandas 合并多个数据源的查询结果。

    为什么需要合并？
      线上库查出来：[{"city":"北京","count":75}]
      线下库查出来：[{"city":"北京","count":30}]
      合并后：[{"city":"北京","count":75,"_source_id":"online"},
               {"city":"北京","count":30,"_source_id":"offline"}]
      这样 AI 生成结论时能区分数据来源，前端也能显示数据源标签。

    参数格式：
      [
        {"source_id": "online", "source_name": "线上业务库", "data": [...]},
        {"source_id": "offline", "source_name": "线下门店库", "data": [...]},
      ]
    """
    dfs = []
    for item in results_by_source:
        if item.get("data"):
            df = pd.DataFrame(item["data"])
            df["_source_id"] = item["source_id"]      # 标记数据来自哪个库
            df["_source_name"] = item["source_name"]  # 中文名，给 AI 看
            dfs.append(df)

    if not dfs:
        return []

    merged = pd.concat(dfs, ignore_index=True)
    merged = merged.where(pd.notnull(merged), None)  # NaN 换成 None，避免 JSON 序列化报错
    return merged.to_dict(orient="records")


def _build_source_context(source: dict) -> str:
    """
    构建数据源说明文本，注入到 SQL 生成的 prompt 里。

    为什么需要这个？
      告诉 AI "你现在操作的是线上库，只能查 orders/hotels/users 这几张表，
      不能跨库 JOIN"，防止 AI 生成错误的 SQL。

    返回示例：
      【当前数据源】
      数据库名：meituan_travel
      数据源说明：美团旅行线上预订数据
      包含的表：users, hotels, orders
      注意：生成的 SQL 只能查询以上表，不能跨库 JOIN
    """
    return (
        f"【当前数据源】\n"
        f"数据库名：{source['database']}\n"
        f"数据源说明：{source['description']}\n"
        f"包含的表：{', '.join(source['tables'])}\n"
        f"注意：生成的 SQL 只能查询以上表，不能跨库 JOIN"
    )


def _execute_with_retry(sql: str, database: str, question: str, context: str, max_retry: int = 3):
    """
    执行 SQL，失败时自动让 AI 修正后重试，最多重试 max_retry 次。

    为什么需要重试？
      AI 生成的 SQL 偶尔有语法错误或字段名写错，
      与其直接报错，不如把错误信息反馈给 AI，让它自己修正。

    重试流程：
      第1次：执行原始 SQL
      失败 → 把 SQL + 错误信息发给 DeepSeek，让它修正
      第2次：执行修正后的 SQL
      失败 → 再次修正
      第3次：执行再次修正的 SQL
      还失败 → 返回空数据 + 错误信息

    返回：(data, final_sql, error)
      data     → 查询结果列表（失败时为空列表）
      final_sql→ 最终执行的 SQL（可能是修正后的版本）
      error    → 错误信息（成功时为 None）
    """
    current_sql = sql
    last_error = None

    for attempt in range(max_retry):
        try:
            data = execute_sql(current_sql, database=database)
            return data, current_sql, None  # 成功，直接返回
        except Exception as e:
            last_error = str(e)
            if attempt < max_retry - 1:
                # 还有重试机会，让 AI 修正 SQL
                try:
                    current_sql = fix_sql(
                        original_sql=current_sql,
                        error_message=last_error,
                        question=question,
                        context=context,
                    )
                except Exception:
                    pass  # fix_sql 本身失败了，继续用原 SQL 重试

    # 所有重试都失败了
    return [], current_sql, last_error


# ── 简单查询路径 ──────────────────────────────────────────
def run_simple_query(question: str, source_ids: list[str], history_text: str = "",
                     trace_callback=None) -> dict:
    """
    简单查询路径：对每个数据源分别生成 SQL 并执行，最后合并结果。

    适用场景：
      "昨天订单量是多少"、"各城市的GMV"、"APP渠道的订单数"

    trace_callback：
      如果传入了回调函数，每完成一步就调用它推送进度事件，
      用于 /ask/stream 接口的实时进度展示。

    返回格式：
      {
        "mode": "simple",
        "conclusion": "分析结论文字",
        "data": [...],          # 合并后的数据（带 _source_id 标签）
        "sql": "SELECT ...",    # 第一个数据源的 SQL
        "source_results": [...]  # 每个数据源的独立结果
      }
    """
    def emit(label, detail="", status="done"):
        """推送进度事件的快捷函数"""
        if trace_callback:
            trace_callback({"type": "step", "label": label, "detail": detail, "status": status})

    sources = get_sources()
    target_sources = [s for s in sources if s["id"] in source_ids]
    if not target_sources:
        target_sources = sources[:1]  # 兜底：至少查一个库

    source_results = []
    all_sqls = []

    for source in target_sources:
        emit(f"RAG检索 [{source['name']}]", "检索相关字段和业务规则...", "running")

        # RAG 检索：只检索当前数据源的字段，避免混入其他库的字段
        schema_context = search_schema(question, source_filter=source["id"])
        knowledge_context = search_knowledge(question)
        source_context = _build_source_context(source)
        full_context = "\n\n".join(filter(None, [source_context, schema_context, knowledge_context]))

        if history_text:
            full_context = f"{history_text}\n\n{full_context}"

        emit(f"RAG检索 [{source['name']}]", f"找到相关字段，准备生成SQL", "done")
        emit(f"生成SQL [{source['name']}]", "DeepSeek正在生成SQL...", "running")

        # 让 DeepSeek 根据问题和检索到的上下文生成 SQL
        sql = generate_sql(question, full_context)
        all_sqls.append(sql)

        emit(f"生成SQL [{source['name']}]", sql[:100] + ("..." if len(sql) > 100 else ""), "done")
        emit(f"执行SQL [{source['name']}]", "连接MySQL执行查询...", "running")

        # 执行 SQL（带自动重试）
        data, final_sql, error = _execute_with_retry(sql, source["database"], question, full_context)

        if error:
            emit(f"执行SQL [{source['name']}]", f"失败: {error[:80]}", "error")
        else:
            emit(f"执行SQL [{source['name']}]", f"返回 {len(data)} 条数据", "done")

        source_results.append({
            "source_id": source["id"],
            "source_name": source["name"],
            "sql": final_sql,
            "data": data,
            "error": error,
        })

    # 用 pandas 合并所有数据源的结果
    merged_data = _merge_results(source_results)

    emit("生成结论", "DeepSeek正在分析数据...", "running")
    conclusion = generate_conclusion(question, merged_data, history_text)
    emit("生成结论", "完成", "done")

    return {
        "mode": "simple",
        "conclusion": conclusion,
        "data": merged_data,
        "sql": all_sqls[0] if all_sqls else "",
        "source_results": source_results,
    }


# ── 分析型查询路径 ────────────────────────────────────────
def run_analysis(question: str, analysis_type: str, source_ids: list[str],
                 history_text: str = "", trace_callback=None) -> dict:
    """
    分析型问题路径：自动拆子问题 → 多库查询 → pandas 合并 → 综合结论。

    适用场景：
      "三亚订单为什么下滑"（异动归因 anomaly）
      "最近30天订单趋势"（趋势分析 trend）
      "APP和小程序哪个更好"（对比分析 comparison）
      "各渠道订单占比"（分布分析 distribution）

    为什么要拆子问题？
      复杂问题需要从多个角度分析，比如"为什么下滑"需要：
        子问题1：整体订单量趋势
        子问题2：各城市分布变化
        子问题3：各渠道占比变化
      每个子问题独立查询，最后综合分析。

    返回格式：
      {
        "mode": "analysis",
        "analysis_type": "anomaly" | "trend" | "comparison" | "distribution",
        "steps": [
          {"step":1, "sub_question":"...", "purpose":"...",
           "source_id":"online", "sql":"...", "data":[...]},
          ...
        ],
        "conclusion": "综合分析报告",
        "source_ids": ["online", "offline"]
      }
    """
    def emit(label, detail="", status="done"):
        if trace_callback:
            trace_callback({"type": "step", "label": label, "detail": detail, "status": status})

    sources = get_sources()
    target_sources = [s for s in sources if s["id"] in source_ids]
    if not target_sources:
        target_sources = sources[:1]

    emit("拆解问题", f"分析类型：{analysis_type}，正在拆解子问题...", "running")

    # 先做一次全局 RAG 检索，给问题拆解提供表结构参考
    schema_context = search_schema(question)

    # 让 DeepSeek 把大问题拆成多个子问题
    sub_questions = decompose_question(question, analysis_type, schema_context, history_text)
    emit("拆解问题", f"拆解为 {len(sub_questions)} 个子问题", "done")

    sub_results = []

    for sq in sub_questions:
        sub_q = sq.get("sub_question", question)
        purpose = sq.get("purpose", "")
        step_num = sq.get("step", len(sub_results) + 1)
        sub_source_id = sq.get("source_id")  # 子问题可以指定查哪个库

        # 确定这个子问题查哪些库
        if sub_source_id and sub_source_id in source_ids:
            sub_source = get_source_by_id(sub_source_id)
            sub_sources_to_query = [sub_source] if sub_source else target_sources
        else:
            sub_sources_to_query = target_sources  # 没指定就查所有目标库

        for source in sub_sources_to_query:
            emit(f"子问题{step_num} [{source['name']}]", sub_q[:50], "running")

            # 每个子问题独立做 RAG 检索
            sub_schema = search_schema(sub_q, source_filter=source["id"])
            sub_knowledge = search_knowledge(sub_q)
            source_context = _build_source_context(source)
            sub_context = "\n\n".join(filter(None, [source_context, sub_schema, sub_knowledge]))

            # 生成 SQL
            sql = generate_sql(sub_q, sub_context, history_text)

            # 执行 SQL（带自动重试）
            data, final_sql, error = _execute_with_retry(sql, source["database"], sub_q, sub_context)

            if error:
                emit(f"子问题{step_num} [{source['name']}]", f"失败: {error[:60]}", "error")
            else:
                emit(f"子问题{step_num} [{source['name']}]", f"返回 {len(data)} 条", "done")

            sub_results.append({
                "step": step_num,
                "sub_question": sub_q,
                "purpose": purpose,
                "source_id": source["id"],
                "source_name": source["name"],
                "sql": final_sql,
                "data": data,
                "error": error,
            })

    emit("综合分析", "DeepSeek正在综合所有数据生成报告...", "running")
    conclusion = synthesize_analysis(question, sub_results, analysis_type, history_text)
    emit("综合分析", "完成", "done")

    return {
        "mode": "analysis",
        "analysis_type": analysis_type,
        "steps": sub_results,
        "conclusion": conclusion,
        "source_ids": source_ids,
    }


# ── 统一入口 ──────────────────────────────────────────────
def process_question(question: str, history_text: str = "") -> dict:
    """
    统一入口（不带进度回调），内部调用 process_question_with_trace。
    保留这个函数是为了向后兼容旧代码。
    """
    result, _ = process_question_with_trace(question, history_text)
    return result


def process_question_with_trace(question: str, history_text: str = "",
                                 trace_callback=None) -> tuple[dict, list]:
    """
    带进度追踪的统一入口，main.py 的 /ask 和 /ask/stream 都调用这里。

    流程：
      第1步：detect_sources()  → 判断查哪些库
      第2步：detect_intent()   → 判断走哪条路径
      第3步：路由到对应路径执行

    trace_callback：
      传入回调函数后，每完成一步都会调用它，
      /ask/stream 用这个实现实时进度推送。

    返回：(result_dict, trace_events_list)
      result_dict   → 完整分析结果
      trace_events_list → 所有进度事件的列表（用于调试）
    """
    def emit(label, detail="", status="done"):
        event = {"type": "step", "label": label, "detail": detail, "status": status}
        trace_events.append(event)
        if trace_callback:
            trace_callback(event)

    trace_events = []
    sources = get_sources()

    # 第1步：数据源识别
    # 根据问题里的关键词判断查哪个库
    # 例："线下门店" → offline，"APP预订" → online，"对比" → 两个都查
    emit("识别数据源", "分析问题涉及哪些数据库...", "running")
    source_ids = detect_sources(question, sources, history_text)
    source_names = [s["name"] for s in sources if s["id"] in source_ids]
    emit("识别数据源", f"{'、'.join(source_names)}", "done")

    # 第2步：意图识别
    # 判断这是简单查询还是需要多步分析
    emit("识别意图", "判断问题类型...", "running")
    intent = detect_intent(question, history_text)
    intent_type = intent.get("type", "simple_query")
    analysis_type = intent.get("analysis_type")
    emit("识别意图", f"类型：{intent_type}" + (f" / {analysis_type}" if analysis_type else ""), "done")

    # 第3步：路由到对应路径
    if intent_type == "analysis" and analysis_type:
        # 分析型：拆子问题多步执行
        result = run_analysis(question, analysis_type, source_ids, history_text, trace_callback)
    else:
        # 简单查询：直接生成SQL执行
        result = run_simple_query(question, source_ids, history_text, trace_callback)

    # 把意图和数据源信息附加到结果里，前端可以展示
    result["intent"] = intent
    result["source_ids"] = source_ids

    return result, trace_events
