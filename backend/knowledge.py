# ============================================================
# knowledge.py —— 业务知识库
# 作用：存储指标口径定义和业务术语映射
#
# 为什么需要这个文件？
# LLM 只知道表结构（有哪些字段），但不知道业务规则：
# - "完成订单"到底是哪些 status？
# - 用户说"高端用户"对应数据库里哪个值？
# - "上个月"怎么写成 SQL？
# 把这些业务知识存入 RAG，LLM 写 SQL 时就能参考，准确率大幅提升
# ============================================================


# ── 指标口径定义 ──────────────────────────────────────────
# 每个指标说清楚：是什么、怎么算、有什么注意事项
METRIC_DEFINITIONS = [
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
]


# ── 业务术语映射 ──────────────────────────────────────────
# 用户说的话 → 对应的 SQL 条件
# 解决"用户语言"和"数据库字段值"之间的鸿沟
TERM_MAPPINGS = [
    {
        "user_says": "高端用户 / VIP用户 / 优质用户",
        "sql_condition": "user_level IN ('金卡', '钻石')",
        "note": "金卡和钻石是高等级用户",
    },
    {
        "user_says": "普通用户 / 新用户",
        "sql_condition": "user_level IN ('普通', '银卡')",
        "note": "普通和银卡是低等级用户",
    },
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
        "user_says": "APP渠道 / app下单",
        "sql_condition": "channel = 'app'",
        "note": "",
    },
    {
        "user_says": "小程序渠道 / 小程序下单",
        "sql_condition": "channel = 'mini'",
        "note": "",
    },
    {
        "user_says": "H5渠道 / 网页下单",
        "sql_condition": "channel = 'h5'",
        "note": "",
    },
    {
        "user_says": "高星酒店 / 豪华酒店 / 五星酒店",
        "sql_condition": "star_level >= 4",
        "note": "4星及以上算高星酒店",
    },
    {
        "user_says": "上个月",
        "sql_condition": "DATE_FORMAT(check_in_date,'%Y-%m') = DATE_FORMAT(DATE_SUB(NOW(), INTERVAL 1 MONTH),'%Y-%m')",
        "note": "用入住日期判断月份",
    },
    {
        "user_says": "今年 / 本年",
        "sql_condition": "YEAR(check_in_date) = YEAR(NOW())",
        "note": "",
    },
    {
        "user_says": "酒店名称 / 酒店叫什么",
        "sql_condition": "需要 JOIN hotels ON orders.hotel_id = hotels.hotel_id 才能获取 hotel_name",
        "note": "orders 表没有酒店名称，需要关联 hotels 表",
    },
]


# ── 表关联关系 ────────────────────────────────────────────
# 告诉 LLM 哪些表可以 JOIN，怎么 JOIN
TABLE_RELATIONS = [
    {
        "description": "订单关联酒店：查酒店名称、星级、均价时需要关联",
        "sql_pattern": "JOIN hotels ON orders.hotel_id = hotels.hotel_id",
    },
    {
        "description": "订单关联用户：查用户等级、用户城市、注册时间时需要关联",
        "sql_pattern": "JOIN users ON orders.user_id = users.user_id",
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

    return docs
