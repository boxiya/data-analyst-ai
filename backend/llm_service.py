# ============================================================
# llm_service.py —— LLM 调用模块（v4，多数据源）
#
# v2 升级内容：
# 1. generate_sql：prompt 更严格，明确禁止翻译关键词，强化口径规则
# 2. generate_conclusion：内置分析方法论框架（异动归因/趋势/对比/分布）
# 3. detect_intent：新增意图识别，判断是"简单查询"还是"分析型问题"
# 4. decompose_question：新增问题拆解，把分析型问题拆成多个子查询
#
# v3 新增：
# 5. summarize_context：每轮查询后提取"分析焦点摘要"，用自然语言描述
#    本轮查询的核心条件，不依赖预定义字段，对任意数据库通用
#
# v4 新增（多数据源）：
# 6. detect_sources：根据用户问题判断需要查哪些数据源（online/offline/both）
#    返回 source_ids 列表，驱动 analysis_engine 路由到正确的库
# ============================================================

import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY", ""),
    base_url="https://api.deepseek.com",
)

MODEL = "deepseek-chat"


def chat(messages: list, temperature: float = 0.3) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content


# ── 生成 SQL ──────────────────────────────────────────────
def generate_sql(question: str, schema_context: str, history_text: str = "") -> str:
    """
    根据用户问题和 RAG 检索到的上下文生成 SQL。
    history_text 让 LLM 理解追问（如"那三亚的呢"）。
    """
    context = schema_context
    if history_text:
        context = f"{history_text}\n\n{schema_context}"

    system_prompt = """你是一个严谨的数据分析师，根据用户问题和数据库表结构生成 MySQL SQL 查询语句。

【硬性规则，必须遵守】
1. 只输出纯 SQL，不要有任何解释，不要用 markdown 代码块包裹
2. 所有 SQL 关键词必须用英文：SELECT FROM WHERE GROUP BY ORDER BY HAVING JOIN ON AS CASE WHEN THEN END COUNT SUM AVG MAX MIN DISTINCT LIMIT
3. 字段名和表名要和提供的表结构完全一致，区分大小写
4. 只用 SELECT，不允许 INSERT / UPDATE / DELETE / DROP
5. 结果要有意义，适当加 ORDER BY 和 LIMIT（默认 LIMIT 20）

【业务口径规则，必须遵守】
6. 凡是涉及"金额/GMV/实收"，必须加 WHERE status='completed'，除非用户明确说"所有订单"
7. 凡是涉及"取消率"，分母是全部订单（不加 status 过滤），分子是 status='cancelled'
8. 凡是涉及"酒店名称"，必须 JOIN hotels ON orders.hotel_id = hotels.hotel_id
9. 凡是涉及"用户等级/用户名"，必须 JOIN users ON orders.user_id = users.user_id
10. 同比 = 和去年同月比；环比 = 和上个月比，不要混淆

【上下文规则】
11. 如果用户的问题是追问（如"那三亚的呢"、"再细分渠道"），结合历史对话理解完整意图，继承上一轮的过滤条件"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{context}\n\n用户问题：{question}\n\nSQL："}
    ]
    return chat(messages)


# ── 生成分析结论（内置方法论框架）────────────────────────
def generate_conclusion(question: str, data: list, history_text: str = "") -> str:
    """
    根据查询结果生成有结构的分析结论。
    v2 升级：内置异动归因/趋势/对比/分布四种分析框架，结论有骨架。
    """
    context = f"{history_text}\n\n" if history_text else ""

    system_prompt = """你是一个资深数据分析师，把数据查询结果转化为有结构的分析结论。

【结论质量要求】
1. 结论要有数字支撑，不说空话（如"有所下降"要改成"下降了23%"）
2. 根据问题类型选择对应的分析框架：

   - 异动归因类（为什么下滑/增长）：
     先说总量变化 → 再说结构变化（哪个维度贡献最大）→ 最后说具体原因
     
   - 趋势分析类（同比/环比/趋势）：
     先说方向和幅度 → 再说拐点时间 → 最后说是否需要关注
     
   - 对比分析类（A和B比较）：
     先说绝对差 → 再说相对差（百分比）→ 最后说差距原因
     
   - 分布分析类（各城市/各渠道分布）：
     先说头部集中度（TOP3占比）→ 再说尾部情况 → 最后说是否健康

3. 如果有历史对话，结论要和上一轮对比，让分析更连贯
4. 结论控制在200字以内，简洁有力
5. 如果数据为空，说明可能的原因（时间范围、过滤条件等）"""

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"{context}用户问题：{question}\n\n查询结果（共{len(data)}行）：{data[:50]}\n\n请给出分析结论："
        }
    ]
    return chat(messages)


# ── 意图识别：判断是简单查询还是分析型问题 ────────────────
def detect_intent(question: str, history_text: str = "") -> dict:
    """
    判断用户问题的类型，决定走哪条处理路径。
    
    返回格式：
    {
        "type": "simple_query" | "analysis",
        "analysis_type": "anomaly" | "trend" | "comparison" | "distribution" | null,
        "reason": "判断理由"
    }
    
    - simple_query：直接查一个数字/列表，走原来的 Text2SQL 路径
    - analysis：需要多步查询+综合结论，走分析引擎路径
    """
    context = f"{history_text}\n\n" if history_text else ""

    system_prompt = """你是一个问题分类器。判断用户的问题属于哪种类型，只输出 JSON，不要有任何解释。

分类规则：
- simple_query：用户只是想查一个具体数字或列表，一条 SQL 就能回答
  例如："三亚上个月的订单数是多少"、"各城市GMV排名"、"高端用户有多少人"
  
- analysis（异动归因 anomaly）：用户想知道"为什么"，需要多维度拆解
  例如："三亚订单为什么下滑"、"上个月GMV下降的原因"、"哪里出了问题"
  
- analysis（趋势分析 trend）：用户想看变化趋势、同比环比
  例如："最近3个月的订单趋势"、"同比增长了多少"、"环比变化"
  
- analysis（对比分析 comparison）：用户想比较两个或多个维度
  例如："APP和小程序哪个渠道更好"、"三亚和海口对比"、"高端用户和普通用户的差异"
  
- analysis（分布分析 distribution）：用户想看占比、分布、结构
  例如："各渠道的订单占比"、"用户等级分布"、"城市集中度"

输出格式（严格 JSON）：
{"type": "simple_query", "analysis_type": null, "reason": "只需查一个数字"}
{"type": "analysis", "analysis_type": "anomaly", "reason": "用户问为什么，需要多维拆解"}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{context}用户问题：{question}"}
    ]

    raw = chat(messages, temperature=0.1)

    # 解析 JSON，如果解析失败默认走 simple_query
    import json
    try:
        # 有时 LLM 会在 JSON 外面加文字，尝试提取花括号内的内容
        start = raw.find('{')
        end = raw.rfind('}') + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass

    return {"type": "simple_query", "analysis_type": None, "reason": "解析失败，默认简单查询"}


# ── 问题拆解：把分析型问题拆成多个子查询 ─────────────────
def decompose_question(question: str, analysis_type: str, schema_context: str, history_text: str = "") -> list[dict]:
    """
    把一个分析型问题拆解成 2-4 个子查询。
    每个子查询都是一个独立的、可以直接生成 SQL 的小问题。
    
    返回格式：
    [
        {"step": 1, "sub_question": "三亚上个月的总订单数和GMV是多少？", "purpose": "了解总量基线"},
        {"step": 2, "sub_question": "三亚上个月各渠道的订单数占比？", "purpose": "排查渠道结构变化"},
        ...
    ]
    """
    context = f"{history_text}\n\n" if history_text else ""

    # 根据分析类型给出不同的拆解指引
    type_guide = {
        "anomaly": (
            "异动归因分析，按三层拆解：\n"
            "第1步：查总量（整体指标是多少，和上期对比）\n"
            "第2步：查结构（按城市/渠道/星级分组，找出变化最大的维度）\n"
            "第3步：查个体（在变化最大的维度里，找出具体是哪几个拖累了整体）"
        ),
        "trend": (
            "趋势分析，按时间维度拆解：\n"
            "第1步：查本期和上期的总量对比（同比或环比）\n"
            "第2步：查按月/按周的趋势变化\n"
            "第3步：查趋势中变化最明显的细分维度"
        ),
        "comparison": (
            "对比分析，按对比维度拆解：\n"
            "第1步：查各对比项的核心指标（订单数、GMV、客单价）\n"
            "第2步：查各对比项的效率指标（取消率、转化率）\n"
            "第3步：查各对比项的用户构成差异"
        ),
        "distribution": (
            "分布分析，按集中度拆解：\n"
            "第1步：查各维度的绝对量和占比\n"
            "第2步：计算头部集中度（TOP3/TOP5占比）\n"
            "第3步：查尾部长尾的情况"
        ),
    }

    guide = type_guide.get(analysis_type, "拆解成2-3个独立的子查询，每个子查询回答一个具体的小问题")

    system_prompt = f"""你是一个数据分析师，把用户的分析问题拆解成多个具体的子查询。

拆解原则：
1. 每个子查询必须是独立的、可以直接写 SQL 的具体问题
2. 子查询之间有逻辑递进关系（从总到分，从现象到原因）
3. 控制在 2-4 个子查询，不要太多
4. 每个子查询要说明"查这个的目的是什么"

分析类型指引：
{guide}

只输出 JSON 数组，不要有任何解释：
[
  {{"step": 1, "sub_question": "具体的子问题", "purpose": "查这个的目的"}},
  {{"step": 2, "sub_question": "具体的子问题", "purpose": "查这个的目的"}}
]"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{context}表结构参考：\n{schema_context}\n\n用户问题：{question}\n\n请拆解："}
    ]

    raw = chat(messages, temperature=0.2)

    import json
    try:
        start = raw.find('[')
        end = raw.rfind(']') + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass

    # 解析失败，返回一个默认子查询（退化为简单查询）
    return [{"step": 1, "sub_question": question, "purpose": "直接回答用户问题"}]


# ── 综合结论：把多个子查询结果整合成最终分析报告 ──────────
def synthesize_analysis(question: str, sub_results: list[dict], analysis_type: str, history_text: str = "") -> str:
    """
    把多个子查询的结果综合成一份有结构的分析报告。
    
    sub_results 格式：
    [
        {"step": 1, "sub_question": "...", "purpose": "...", "data": [...], "sql": "..."},
        ...
    ]
    """
    context = f"{history_text}\n\n" if history_text else ""

    # 把子查询结果整理成文字
    results_text = ""
    for r in sub_results:
        results_text += f"\n【第{r['step']}步】{r['sub_question']}\n"
        results_text += f"目的：{r['purpose']}\n"
        if r.get('error'):
            results_text += f"查询出错：{r['error']}\n"
        else:
            results_text += f"数据（共{len(r.get('data', []))}行）：{r.get('data', [])[:20]}\n"

    type_format = {
        "anomaly": "【结论格式】\n背景：总量变化是...\n发现：主要问题在...\n原因：深层原因是...\n建议：下一步可以...",
        "trend": "【结论格式】\n趋势：整体方向是...\n幅度：变化了...\n拐点：从...开始...\n关注点：需要注意...",
        "comparison": "【结论格式】\n差距：A比B...\n原因：差距来自...\n优劣势：A的优势是...，B的优势是...\n建议：...",
        "distribution": "【结论格式】\n集中度：TOP3占...\n头部：...\n尾部：...\n健康度：...",
    }

    format_guide = type_format.get(analysis_type, "给出有结构的分析结论，包含发现、原因、建议三部分")

    system_prompt = f"""你是一个资深数据分析师，根据多步查询的结果，综合输出一份完整的分析报告。

要求：
1. 结论要有数字支撑，不说空话
2. 逻辑要清晰：从现象到原因，从总量到细节
3. 如果某步查询出错，跳过该步，基于其他步骤给出结论
4. 控制在300字以内

{format_guide}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"{context}用户原始问题：{question}\n\n多步查询结果：{results_text}\n\n请给出综合分析报告："
        }
    ]
    return chat(messages)


# ── 澄清问题（预留，意图不明时使用）─────────────────────
def clarify_question(question: str, history_text: str = "") -> str:
    """
    当用户问题不够清晰时，让 LLM 反问用户。
    例如用户只说"帮我看看数据"，LLM 会追问"你想看哪方面的数据？"
    """
    context = f"{history_text}\n\n" if history_text else ""
    messages = [
        {
            "role": "system",
            "content": "你是数据分析助手。如果用户问题不够明确，礼貌地追问用户想了解哪方面的数据。"
        },
        {
            "role": "user",
            "content": f"{context}用户说：{question}"
        }
    ]
    return chat(messages)


# ── 上下文摘要（v3 新增，通用上下文记忆核心）────────────
def summarize_context(question: str, conclusion: str, sql: str) -> str:
    """
    每轮查询完成后，提取本轮的"分析焦点摘要"。
    
    【为什么不用结构化 JSON？】
    结构化方案（city/time_range/metric 等字段）只对特定数据库有效。
    用自然语言摘要，LLM 自己决定提取哪些关键条件，对任意数据库通用：
    - 酒店数据：提取城市、时间、酒店名、指标
    - 电商数据：提取商品类目、时间、渠道、指标
    - 广告数据：提取投放计划、时间段、转化指标
    
    【摘要格式】
    一句话，包含：分析对象 + 时间范围 + 过滤条件 + 核心指标 + 关键结论数字
    例如：
    - "三亚2026年4月完成订单GMV（16039元），按酒店分组"
    - "APP vs 小程序渠道对比，高端用户订单数和GMV"
    - "全平台近30天取消率趋势，按渠道拆分"
    
    【用途】
    这个摘要会作为"上一轮分析焦点"注入下一轮的 prompt，
    让 LLM 理解追问的上下文，不依赖历史对话文字的长度。
    """
    # 只取 SQL 的前 300 字符，避免 token 浪费
    sql_snippet = sql[:300] if sql else ""

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个上下文提取器。根据用户问题、SQL和结论，"
                "用一句话总结本轮分析的核心焦点。\n\n"
                "要求：\n"
                "1. 包含分析对象（城市/渠道/用户群等）\n"
                "2. 包含时间范围（如果有）\n"
                "3. 包含核心指标名称\n"
                "4. 包含最关键的一个结论数字（如果有）\n"
                "5. 控制在30字以内，不要废话\n\n"
                "示例输出：\n"
                "三亚2026年4月完成订单GMV共16039元，按酒店分组\n"
                "APP渠道高端用户订单数3单、GMV19150元，优于小程序\n"
                "全平台上月取消率15%，环比上升3个百分点"
            )
        },
        {
            "role": "user",
            "content": (
                f"用户问题：{question}\n"
                f"执行的SQL（片段）：{sql_snippet}\n"
                f"分析结论（片段）：{conclusion[:200]}\n\n"
                "请输出本轮分析焦点摘要（一句话）："
            )
        }
    ]
    try:
        return chat(messages, temperature=0.1)
    except Exception:
        # 摘要生成失败不影响主流程，返回原始问题作为兜底
        return question


# ── 数据源识别（v4 新增，多数据源路由核心）────────────────
def detect_sources(question: str, sources: list[dict], history_text: str = "") -> list[str]:
    """
    根据用户问题判断需要查哪些数据源，返回 source_id 列表。
    
    sources 格式（来自 sources.json）：
    [
        {"id": "online", "name": "线上业务库", "description": "...", "keywords": [...]},
        {"id": "offline", "name": "线下门店库", "description": "...", "keywords": [...]},
    ]
    
    返回示例：
    - ["online"]          → 只查线上库
    - ["offline"]         → 只查线下库
    - ["online", "offline"] → 两个库都要查（跨库联合分析）
    
    【判断逻辑】
    1. 先用关键词快速匹配（速度快，不消耗 token）
    2. 关键词匹配不确定时，再用 LLM 判断（准确率高）
    3. 完全不确定时，默认查所有数据源（宁可多查不漏查）
    """
    import json  # 移到函数顶部，避免在 f-string 里引用时 UnboundLocalError

    # ── 第一步：关键词快速匹配 ────────────────────────────
    matched = set()
    q_lower = question.lower()
    for source in sources:
        for kw in source.get("keywords", []):
            if kw in question or kw.lower() in q_lower:
                matched.add(source["id"])
                break

    # 如果关键词匹配到了明确的单个数据源，直接返回（不用 LLM）
    if len(matched) == 1:
        return list(matched)

    # ── 第二步：LLM 判断（关键词匹配不确定时）────────────
    context = f"{history_text}\n\n" if history_text else ""

    # 构建数据源描述
    sources_desc = "\n".join([
        f"- {s['id']}（{s['name']}）：{s['description']}，关键词：{', '.join(s.get('keywords', [])[:5])}"
        for s in sources
    ])
    source_ids = [s["id"] for s in sources]

    system_prompt = f"""你是一个数据路由器。根据用户问题，判断需要查询哪些数据源。

可用数据源：
{sources_desc}

判断规则：
1. 如果问题只涉及线上业务（APP/小程序/在线预订/用户/会员），只返回线上库
2. 如果问题只涉及线下业务（门店/顾问/到店/柜台），只返回线下库
3. 如果问题需要对比线上和线下，或者问题不明确，返回所有数据源
4. 如果问题涉及"整体/全渠道/总体"，返回所有数据源

只输出 JSON 数组，包含需要查询的 source_id，不要有任何解释：
["online"]
["offline"]
{json.dumps(source_ids, ensure_ascii=False)}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{context}用户问题：{question}"}
    ]

    try:
        raw = chat(messages, temperature=0.1)
        start = raw.find('[')
        end = raw.rfind(']') + 1
        if start >= 0 and end > start:
            result = json.loads(raw[start:end])
            # 过滤掉不存在的 source_id
            valid = [r for r in result if r in source_ids]
            if valid:
                return valid
    except Exception:
        pass

    # 兜底：返回所有数据源
    return source_ids
