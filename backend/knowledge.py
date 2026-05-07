# ============================================================
# knowledge.py —— 业务知识库（v2，扩充版）
#
# 为什么需要这个文件？
# LLM 只知道表结构（有哪些字段），但不知道业务规则：
# - "完成订单"到底是哪些 status？
# - 用户说"高端用户"对应数据库里哪个值？
# - "同比"怎么写成 SQL？"环比"呢？
# - "渠道占比"怎么算？
# 把这些业务知识存入 RAG，LLM 写 SQL 时就能参考，准确率大幅提升
#
# v2 新增：
# - 时间口径（同比/环比/近N天/本季度）
# - 渠道分析（OTA/直连/团购/渠道占比）
# - 漏斗分析（曝光→点击→下单→完成转化率）
# - 用户分层（新用户/老用户/流失用户/回流用户）
# - 酒店维度（经济型/中端/高端/RevPAR）
# - 异动归因（标准拆解维度）
# ============================================================


# ── 指标口径定义 ──────────────────────────────────────────
METRIC_DEFINITIONS = [
    # ---- 订单核心指标 ----
    {
        "name": "完成订单数",
        "description": "状态为已完成的订单数量",
        "sql_pattern": "COUNT(order_id) WHERE status = 'completed'",
        "note": "不含取消订单(cancelled)和退款订单(refunded)",
    },
    {
        "name": "完成订单金额 / 实收金额 / GMV",
        "description": "状态为已完成的订单总金额",
        "sql_pattern": "SUM(amount) WHERE status = 'completed'",
        "note": "取消和退款订单不计入GMV，查询时必须加 WHERE status='completed'",
    },
    {
        "name": "取消率",
        "description": "取消订单数占总订单数的比例",
        "sql_pattern": "COUNT(CASE WHEN status='cancelled' THEN 1 END) / COUNT(order_id)",
        "note": "分母是全部订单数，包含完成、取消、退款",
    },
    {
        "name": "退款率",
        "description": "退款订单数占总订单数的比例",
        "sql_pattern": "COUNT(CASE WHEN status='refunded' THEN 1 END) / COUNT(order_id)",
        "note": "退款和取消是两种不同状态，不要混用",
    },
    {
        "name": "客单价 / 平均订单金额",
        "description": "完成订单的平均金额",
        "sql_pattern": "AVG(amount) WHERE status = 'completed'",
        "note": "只统计完成状态的订单",
    },
    {
        "name": "入住天数 / 间夜数",
        "description": "退房日期减去入住日期的天数",
        "sql_pattern": "DATEDIFF(check_out_date, check_in_date)",
        "note": "例如1月10日入住1月12日退房，间夜数=2",
    },
    # ---- 同比环比指标 ----
    {
        "name": "同比 / 同比增长率 / YoY",
        "description": "与去年同一时期相比的增长率",
        "sql_pattern": (
            "同比增长率 = (本期值 - 去年同期值) / 去年同期值 * 100%\n"
            "去年同期条件：YEAR(check_in_date) = YEAR(NOW()) - 1 AND MONTH(check_in_date) = MONTH(NOW())\n"
            "本期条件：YEAR(check_in_date) = YEAR(NOW()) AND MONTH(check_in_date) = MONTH(NOW())"
        ),
        "note": "同比是和去年同月比，不是和上个月比",
    },
    {
        "name": "环比 / 环比增长率 / MoM / 和上个月比",
        "description": "与上一个统计周期（通常是上个月）相比的增长率",
        "sql_pattern": (
            "环比增长率 = (本月值 - 上月值) / 上月值 * 100%\n"
            "本月条件：DATE_FORMAT(check_in_date,'%Y-%m') = DATE_FORMAT(NOW(),'%Y-%m')\n"
            "上月条件：DATE_FORMAT(check_in_date,'%Y-%m') = DATE_FORMAT(DATE_SUB(NOW(), INTERVAL 1 MONTH),'%Y-%m')"
        ),
        "note": "环比是和上个月比，不是和去年同月比",
    },
    {
        "name": "渠道占比 / 各渠道份额",
        "description": "某渠道订单数占总订单数的比例",
        "sql_pattern": (
            "SELECT channel,\n"
            "  COUNT(order_id) AS order_cnt,\n"
            "  COUNT(order_id) / SUM(COUNT(order_id)) OVER() AS channel_ratio\n"
            "FROM orders\n"
            "WHERE status = 'completed'\n"
            "GROUP BY channel"
        ),
        "note": "分母是全部完成订单，用窗口函数 OVER() 计算总量",
    },
    {
        "name": "RevPAR / 每间可售房收入",
        "description": "酒店运营效率指标，等于平均房价乘以入住率",
        "sql_pattern": "SUM(amount) / (酒店总房间数 * 统计天数)",
        "note": "需要知道酒店总房间数，通常从 hotels 表的 room_count 字段获取",
    },
    {
        "name": "转化率 / 下单转化率",
        "description": "从曝光到下单的转化比例",
        "sql_pattern": "COUNT(DISTINCT order_id) / COUNT(DISTINCT user_id) （下单用户数/访问用户数）",
        "note": "转化率分析需要有曝光/点击数据，如果只有订单表则只能算下单完成率",
    },
    {
        "name": "复购率 / 回购率",
        "description": "在统计周期内下过2次及以上订单的用户占比",
        "sql_pattern": (
            "SELECT COUNT(CASE WHEN order_cnt >= 2 THEN 1 END) / COUNT(user_id) AS repurchase_rate\n"
            "FROM (\n"
            "  SELECT user_id, COUNT(order_id) AS order_cnt\n"
            "  FROM orders WHERE status='completed'\n"
            "  GROUP BY user_id\n"
            ") t"
        ),
        "note": "分母是有过下单行为的用户，不是全部注册用户",
    },
]


# ── 业务术语映射 ──────────────────────────────────────────
TERM_MAPPINGS = [
    # ---- 用户等级 ----
    {
        "user_says": "高端用户 / VIP用户 / 优质用户 / 高价值用户",
        "sql_condition": "user_level IN ('金卡', '钻石')",
        "note": "金卡和钻石是高等级用户",
    },
    {
        "user_says": "普通用户 / 低价值用户",
        "sql_condition": "user_level IN ('普通', '银卡')",
        "note": "普通和银卡是低等级用户",
    },
    {
        "user_says": "新用户 / 首次下单用户",
        "sql_condition": (
            "user_id IN (\n"
            "  SELECT user_id FROM orders\n"
            "  GROUP BY user_id HAVING COUNT(order_id) = 1\n"
            ")"
        ),
        "note": "只有一笔订单的用户视为新用户",
    },
    {
        "user_says": "老用户 / 回头客 / 多次购买用户",
        "sql_condition": (
            "user_id IN (\n"
            "  SELECT user_id FROM orders\n"
            "  GROUP BY user_id HAVING COUNT(order_id) >= 2\n"
            ")"
        ),
        "note": "有2笔及以上订单的用户视为老用户",
    },
    {
        "user_says": "流失用户 / 沉默用户",
        "sql_condition": (
            "user_id NOT IN (\n"
            "  SELECT DISTINCT user_id FROM orders\n"
            "  WHERE check_in_date >= DATE_SUB(NOW(), INTERVAL 90 DAY)\n"
            ")"
        ),
        "note": "90天内没有下单的用户视为流失用户",
    },
    # ---- 订单状态 ----
    {
        "user_says": "完成的订单 / 有效订单 / 成功订单",
        "sql_condition": "status = 'completed'",
        "note": "只有completed才是真正完成的订单",
    },
    {
        "user_says": "取消的订单",
        "sql_condition": "status = 'cancelled'",
        "note": "",
    },
    {
        "user_says": "退款的订单",
        "sql_condition": "status = 'refunded'",
        "note": "",
    },
    {
        "user_says": "所有订单 / 全部订单（不区分状态）",
        "sql_condition": "不加 WHERE status 过滤，或 status IN ('completed','cancelled','refunded')",
        "note": "如果用户明确说'所有订单'，不要自动加 status='completed' 过滤",
    },
    # ---- 渠道 ----
    {
        "user_says": "APP渠道 / app下单 / 美团APP",
        "sql_condition": "channel = 'app'",
        "note": "",
    },
    {
        "user_says": "小程序渠道 / 小程序下单 / 微信小程序",
        "sql_condition": "channel = 'mini'",
        "note": "",
    },
    {
        "user_says": "H5渠道 / 网页下单 / 手机网页",
        "sql_condition": "channel = 'h5'",
        "note": "",
    },
    {
        "user_says": "OTA渠道 / 第三方平台 / 携程 / 飞猪",
        "sql_condition": "channel = 'ota'",
        "note": "OTA指第三方在线旅行平台，如携程、飞猪等",
    },
    {
        "user_says": "直连渠道 / 直接预订 / 官网",
        "sql_condition": "channel IN ('app', 'h5', 'mini')",
        "note": "直连是指通过美团自有渠道下单，不经过第三方",
    },
    # ---- 酒店星级 ----
    {
        "user_says": "高星酒店 / 豪华酒店 / 五星酒店 / 高端酒店",
        "sql_condition": "star_level >= 4",
        "note": "4星及以上算高星酒店",
    },
    {
        "user_says": "中端酒店 / 三星酒店",
        "sql_condition": "star_level = 3",
        "note": "3星是中端酒店",
    },
    {
        "user_says": "经济型酒店 / 低端酒店 / 快捷酒店",
        "sql_condition": "star_level <= 2",
        "note": "2星及以下是经济型酒店",
    },
    # ---- 时间范围 ----
    {
        "user_says": "上个月 / 上月",
        "sql_condition": "DATE_FORMAT(check_in_date,'%Y-%m') = DATE_FORMAT(DATE_SUB(NOW(), INTERVAL 1 MONTH),'%Y-%m')",
        "note": "用入住日期判断月份",
    },
    {
        "user_says": "本月 / 这个月 / 当月",
        "sql_condition": "DATE_FORMAT(check_in_date,'%Y-%m') = DATE_FORMAT(NOW(),'%Y-%m')",
        "note": "",
    },
    {
        "user_says": "今年 / 本年 / 今年以来",
        "sql_condition": "YEAR(check_in_date) = YEAR(NOW())",
        "note": "",
    },
    {
        "user_says": "去年 / 上一年",
        "sql_condition": "YEAR(check_in_date) = YEAR(NOW()) - 1",
        "note": "",
    },
    {
        "user_says": "近7天 / 最近一周 / 过去7天",
        "sql_condition": "check_in_date >= DATE_SUB(NOW(), INTERVAL 7 DAY)",
        "note": "",
    },
    {
        "user_says": "近30天 / 最近一个月 / 过去30天",
        "sql_condition": "check_in_date >= DATE_SUB(NOW(), INTERVAL 30 DAY)",
        "note": "",
    },
    {
        "user_says": "本季度 / 这个季度",
        "sql_condition": "QUARTER(check_in_date) = QUARTER(NOW()) AND YEAR(check_in_date) = YEAR(NOW())",
        "note": "季度用 QUARTER() 函数，Q1=1月-3月，Q2=4月-6月，Q3=7月-9月，Q4=10月-12月",
    },
    {
        "user_says": "上个季度 / 上季度",
        "sql_condition": (
            "QUARTER(check_in_date) = QUARTER(DATE_SUB(NOW(), INTERVAL 1 QUARTER))\n"
            "AND YEAR(check_in_date) = YEAR(DATE_SUB(NOW(), INTERVAL 1 QUARTER))"
        ),
        "note": "",
    },
    # ---- 字段获取说明 ----
    {
        "user_says": "酒店名称 / 酒店叫什么 / 哪家酒店",
        "sql_condition": "需要 JOIN hotels ON orders.hotel_id = hotels.hotel_id 才能获取 hotel_name",
        "note": "orders 表没有酒店名称，需要关联 hotels 表",
    },
    {
        "user_says": "用户姓名 / 用户名 / 谁下的单",
        "sql_condition": "需要 JOIN users ON orders.user_id = users.user_id 才能获取 username",
        "note": "orders 表没有用户名，需要关联 users 表",
    },
]


# ── 表关联关系 ────────────────────────────────────────────
TABLE_RELATIONS = [
    {
        "description": "订单关联酒店：查酒店名称、星级、均价、城市时需要关联",
        "sql_pattern": "JOIN hotels ON orders.hotel_id = hotels.hotel_id",
    },
    {
        "description": "订单关联用户：查用户等级、用户城市、注册时间、用户名时需要关联",
        "sql_pattern": "JOIN users ON orders.user_id = users.user_id",
    },
    {
        "description": "三表关联：同时需要酒店信息和用户信息时",
        "sql_pattern": (
            "FROM orders\n"
            "JOIN hotels ON orders.hotel_id = hotels.hotel_id\n"
            "JOIN users ON orders.user_id = users.user_id"
        ),
    },
]


# ── 分析方法论 ────────────────────────────────────────────
# 告诉 LLM 面对不同类型的问题，应该用什么分析框架
ANALYSIS_METHODS = [
    {
        "scenario": "异动归因 / 为什么下滑 / 为什么增长 / 原因分析",
        "method": "三层拆解法",
        "steps": (
            "第一层：总量变化（整体订单数/GMV同比环比是多少）\n"
            "第二层：结构变化（哪个城市/渠道/酒店星级的占比变了）\n"
            "第三层：个体变化（具体是哪几个酒店或城市拖累了整体）\n"
            "结论格式：总量下滑X%，主要由[城市/渠道/星级]结构变化驱动，其中[具体项]贡献了Y%的降幅"
        ),
    },
    {
        "scenario": "同比环比分析 / 趋势分析 / 增长分析",
        "method": "趋势三要素",
        "steps": (
            "方向：是增长还是下滑\n"
            "幅度：增长/下滑了多少（绝对值和百分比）\n"
            "拐点：从哪个时间点开始变化的\n"
            "结论格式：[指标]同比[增长/下滑]X%，环比[增长/下滑]Y%，[时间点]出现明显拐点"
        ),
    },
    {
        "scenario": "对比分析 / A和B哪个好 / 城市对比 / 渠道对比",
        "method": "三维对比法",
        "steps": (
            "绝对差：A比B多/少多少（数量）\n"
            "相对差：A比B高/低多少百分比\n"
            "原因：为什么会有这个差距（结构差异还是效率差异）\n"
            "结论格式：A的[指标]为X，B为Y，A高出Z%，差距主要来自[原因]"
        ),
    },
    {
        "scenario": "分布分析 / 各城市分布 / 各渠道分布 / 占比分析",
        "method": "二八分析",
        "steps": (
            "找出贡献最大的前20%的项目\n"
            "计算头部集中度（TOP3/TOP5占总量的比例）\n"
            "结论格式：TOP3[城市/渠道]贡献了X%的[指标]，集中度[高/低]"
        ),
    },
    {
        "scenario": "用户分析 / 用户画像 / 用户行为",
        "method": "RFM分层",
        "steps": (
            "R（Recency）：最近一次下单距今多久\n"
            "F（Frequency）：下单频次\n"
            "M（Monetary）：消费金额\n"
            "结论格式：高价值用户（高F高M）占X%，贡献Y%的GMV"
        ),
    },
]


def get_all_knowledge() -> list[dict]:
    """
    把所有业务知识整合成统一格式，供 rag_service 存入向量库
    每条知识都转成一段自然语言描述，方便语义检索
    """
    docs = []

    # 指标口径
    for m in METRIC_DEFINITIONS:
        text = (
            f"指标名称：{m['name']}\n"
            f"定义：{m['description']}\n"
            f"SQL写法：{m['sql_pattern']}\n"
            f"注意事项：{m['note']}"
        )
        docs.append({"type": "metric", "text": text})

    # 业务术语映射
    for t in TERM_MAPPINGS:
        user_says = t['user_says']
        sql_condition = t['sql_condition']
        note = t['note']
        text = (
            f"用户说「{user_says}」时，对应的SQL条件是：{sql_condition}\n"
            f"备注：{note}"
        )
        docs.append({"type": "term", "text": text})

    # 表关联关系
    for r in TABLE_RELATIONS:
        text = (
            f"表关联说明：{r['description']}\n"
            f"SQL写法：{r['sql_pattern']}"
        )
        docs.append({"type": "relation", "text": text})

    # 分析方法论
    for a in ANALYSIS_METHODS:
        text = (
            f"分析场景：{a['scenario']}\n"
            f"推荐方法：{a['method']}\n"
            f"分析步骤：{a['steps']}"
        )
        docs.append({"type": "method", "text": text})

    return docs
