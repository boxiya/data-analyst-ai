# ============================================================
# seed_data.py —— 重新生成模拟数据（贴近当前时间）
#
# 生成规模：
# - hotels: 20 家酒店（覆盖三亚/北京/上海/成都/杭州，各星级）
# - users:  50 个用户（各等级分布）
# - orders: 300 条订单（覆盖 2025-01 ~ 2026-04，含本月/上月/去年同期）
#
# 运行方式：python seed_data.py
# ============================================================

import os
import random
from datetime import date, timedelta
from decimal import Decimal
import pymysql
from dotenv import load_dotenv

load_dotenv()

conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    port=int(os.getenv("MYSQL_PORT", 3306)),
    user=os.getenv("MYSQL_USER", "root"),
    password=os.getenv("MYSQL_PASSWORD", ""),
    database=os.getenv("MYSQL_DATABASE", ""),
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)

# ── 基础数据定义 ──────────────────────────────────────────

CITIES = ["三亚", "北京", "上海", "成都", "杭州"]

HOTELS = [
    # 三亚
    {"hotel_id": 1,  "hotel_name": "三亚亚特兰蒂斯",   "city": "三亚", "star_level": 5, "avg_price": 2800},
    {"hotel_id": 2,  "hotel_name": "三亚海棠湾万豪",   "city": "三亚", "star_level": 5, "avg_price": 2200},
    {"hotel_id": 3,  "hotel_name": "三亚喜来登度假酒店","city": "三亚", "star_level": 4, "avg_price": 1500},
    {"hotel_id": 4,  "hotel_name": "三亚如家快捷",     "city": "三亚", "star_level": 2, "avg_price": 280},
    # 北京
    {"hotel_id": 5,  "hotel_name": "北京国贸大酒店",   "city": "北京", "star_level": 5, "avg_price": 1800},
    {"hotel_id": 6,  "hotel_name": "北京希尔顿",       "city": "北京", "star_level": 5, "avg_price": 1600},
    {"hotel_id": 7,  "hotel_name": "北京汉庭酒店",     "city": "北京", "star_level": 3, "avg_price": 380},
    {"hotel_id": 8,  "hotel_name": "北京如家精选",     "city": "北京", "star_level": 2, "avg_price": 220},
    # 上海
    {"hotel_id": 9,  "hotel_name": "上海外滩华尔道夫", "city": "上海", "star_level": 5, "avg_price": 3200},
    {"hotel_id": 10, "hotel_name": "上海浦东香格里拉", "city": "上海", "star_level": 5, "avg_price": 2400},
    {"hotel_id": 11, "hotel_name": "上海全季酒店",     "city": "上海", "star_level": 3, "avg_price": 450},
    {"hotel_id": 12, "hotel_name": "上海锦江之星",     "city": "上海", "star_level": 2, "avg_price": 200},
    # 成都
    {"hotel_id": 13, "hotel_name": "成都瑞吉酒店",     "city": "成都", "star_level": 5, "avg_price": 1400},
    {"hotel_id": 14, "hotel_name": "成都香格里拉",     "city": "成都", "star_level": 5, "avg_price": 1200},
    {"hotel_id": 15, "hotel_name": "成都宽窄巷子民宿", "city": "成都", "star_level": 3, "avg_price": 520},
    {"hotel_id": 16, "hotel_name": "成都7天连锁",      "city": "成都", "star_level": 2, "avg_price": 180},
    # 杭州
    {"hotel_id": 17, "hotel_name": "杭州西湖国宾馆",   "city": "杭州", "star_level": 5, "avg_price": 1600},
    {"hotel_id": 18, "hotel_name": "杭州君悦酒店",     "city": "杭州", "star_level": 5, "avg_price": 1400},
    {"hotel_id": 19, "hotel_name": "杭州全季西湖",     "city": "杭州", "star_level": 3, "avg_price": 420},
    {"hotel_id": 20, "hotel_name": "杭州汉庭快捷",     "city": "杭州", "star_level": 2, "avg_price": 210},
]

USER_LEVELS = ["普通", "银卡", "金卡", "钻石"]
LEVEL_WEIGHTS = [40, 30, 20, 10]  # 普通最多，钻石最少

CHANNELS = ["app", "mini", "h5", "ota"]
CHANNEL_WEIGHTS = [50, 25, 15, 10]  # app 为主

# 订单状态：完成率约 75%，取消 15%，退款 10%
STATUSES = ["completed", "cancelled", "refunded"]
STATUS_WEIGHTS = [75, 15, 10]


def random_date(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def generate_users(n=50):
    users = []
    for i in range(1, n + 1):
        level = random.choices(USER_LEVELS, weights=LEVEL_WEIGHTS)[0]
        # 注册时间：2022-2024 年之间
        reg_date = random_date(date(2022, 1, 1), date(2024, 12, 31))
        users.append({
            "user_id": i,
            "user_level": level,
            "city": random.choice(CITIES),
            "register_date": reg_date,
        })
    return users


def generate_orders(users, n=300):
    """
    生成 300 条订单，时间分布：
    - 2025-01 ~ 2025-12：约 200 条（去年全年，用于同比）
    - 2026-01 ~ 2026-03：约 70 条（今年前几个月，用于环比）
    - 2026-04：约 20 条（上个月，重点测试）
    - 2026-05：约 10 条（本月）
    
    三亚数据特意做成"上个月(2026-04)比去年同期(2025-04)下滑"，
    方便测试异动归因分析。
    """
    orders = []
    order_id = 1

    # ── 2025 全年（去年，用于同比基准）──────────────────
    for month in range(1, 13):
        month_start = date(2025, month, 1)
        if month == 12:
            month_end = date(2025, 12, 31)
        else:
            month_end = date(2025, month + 1, 1) - timedelta(days=1)

        # 每个月每个城市生成 3-5 条
        for city_hotels in [
            [h for h in HOTELS if h["city"] == c] for c in CITIES
        ]:
            cnt = random.randint(3, 5)
            for _ in range(cnt):
                hotel = random.choice(city_hotels)
                user = random.choice(users)
                nights = random.randint(1, 5)
                check_in = random_date(month_start, month_end)
                check_out = check_in + timedelta(days=nights)
                base_price = hotel["avg_price"] * nights
                # 加一点随机浮动 ±20%
                amount = round(base_price * random.uniform(0.8, 1.2), 2)
                status = random.choices(STATUSES, weights=STATUS_WEIGHTS)[0]
                channel = random.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0]

                orders.append({
                    "order_id": order_id,
                    "user_id": user["user_id"],
                    "hotel_id": hotel["hotel_id"],
                    "check_in_date": check_in,
                    "check_out_date": check_out,
                    "amount": amount,
                    "status": status,
                    "channel": channel,
                    "city": hotel["city"],
                })
                order_id += 1

    # ── 2026-01 ~ 2026-03（今年前三个月）────────────────
    for month in range(1, 4):
        month_start = date(2026, month, 1)
        month_end = date(2026, month + 1, 1) - timedelta(days=1)
        for city_hotels in [
            [h for h in HOTELS if h["city"] == c] for c in CITIES
        ]:
            cnt = random.randint(2, 4)
            for _ in range(cnt):
                hotel = random.choice(city_hotels)
                user = random.choice(users)
                nights = random.randint(1, 4)
                check_in = random_date(month_start, month_end)
                check_out = check_in + timedelta(days=nights)
                amount = round(hotel["avg_price"] * nights * random.uniform(0.8, 1.2), 2)
                status = random.choices(STATUSES, weights=STATUS_WEIGHTS)[0]
                channel = random.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0]
                orders.append({
                    "order_id": order_id,
                    "user_id": user["user_id"],
                    "hotel_id": hotel["hotel_id"],
                    "check_in_date": check_in,
                    "check_out_date": check_out,
                    "amount": amount,
                    "status": status,
                    "channel": channel,
                    "city": hotel["city"],
                })
                order_id += 1

    # ── 2026-04（上个月）：三亚特意减少，模拟下滑 ────────
    # 其他城市正常，三亚只生成 2 条（去年同期有 4-5 条）
    for hotel in HOTELS:
        city = hotel["city"]
        # 三亚只生成 2 条，其他城市生成 3-4 条
        cnt = 2 if city == "三亚" else random.randint(3, 4)
        for _ in range(cnt):
            user = random.choice(users)
            nights = random.randint(1, 4)
            check_in = random_date(date(2026, 4, 1), date(2026, 4, 30))
            check_out = check_in + timedelta(days=nights)
            amount = round(hotel["avg_price"] * nights * random.uniform(0.8, 1.2), 2)
            # 三亚上个月取消率更高（模拟异动）
            if city == "三亚":
                status = random.choices(STATUSES, weights=[50, 35, 15])[0]
            else:
                status = random.choices(STATUSES, weights=STATUS_WEIGHTS)[0]
            channel = random.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0]
            orders.append({
                "order_id": order_id,
                "user_id": user["user_id"],
                "hotel_id": hotel["hotel_id"],
                "check_in_date": check_in,
                "check_out_date": check_out,
                "amount": amount,
                "status": status,
                "channel": channel,
                "city": city,
            })
            order_id += 1

    # ── 2026-05（本月）────────────────────────────────────
    today = date(2026, 5, 7)
    for hotel in HOTELS:
        if random.random() < 0.4:  # 本月数据稀疏一些
            user = random.choice(users)
            nights = random.randint(1, 3)
            check_in = random_date(date(2026, 5, 1), today)
            check_out = check_in + timedelta(days=nights)
            amount = round(hotel["avg_price"] * nights * random.uniform(0.8, 1.2), 2)
            status = random.choices(STATUSES, weights=STATUS_WEIGHTS)[0]
            channel = random.choices(CHANNELS, weights=CHANNEL_WEIGHTS)[0]
            orders.append({
                "order_id": order_id,
                "user_id": user["user_id"],
                "hotel_id": hotel["hotel_id"],
                "check_in_date": check_in,
                "check_out_date": check_out,
                "amount": amount,
                "status": status,
                "channel": channel,
                "city": hotel["city"],
            })
            order_id += 1

    return orders


def main():
    cursor = conn.cursor()

    print("清空旧数据...")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    cursor.execute("TRUNCATE TABLE orders")
    cursor.execute("TRUNCATE TABLE hotels")
    cursor.execute("TRUNCATE TABLE users")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")

    print("写入酒店数据（20家）...")
    for h in HOTELS:
        cursor.execute(
            "INSERT INTO hotels (hotel_id, hotel_name, city, star_level, avg_price) VALUES (%s, %s, %s, %s, %s)",
            (h["hotel_id"], h["hotel_name"], h["city"], h["star_level"], h["avg_price"])
        )

    print("写入用户数据（50人）...")
    users = generate_users(50)
    for u in users:
        cursor.execute(
            "INSERT INTO users (user_id, user_level, city, register_date) VALUES (%s, %s, %s, %s)",
            (u["user_id"], u["user_level"], u["city"], u["register_date"])
        )

    print("写入订单数据（约300条）...")
    orders = generate_orders(users)
    for o in orders:
        cursor.execute(
            """INSERT INTO orders
               (order_id, user_id, hotel_id, check_in_date, check_out_date, amount, status, channel, city)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (o["order_id"], o["user_id"], o["hotel_id"],
             o["check_in_date"], o["check_out_date"],
             o["amount"], o["status"], o["channel"], o["city"])
        )

    conn.commit()
    cursor.close()
    conn.close()

    print(f"\n完成！共写入：")
    print(f"  酒店：{len(HOTELS)} 家")
    print(f"  用户：{len(users)} 人")
    print(f"  订单：{len(orders)} 条")
    print(f"\n数据时间范围：2025-01 ~ 2026-05")
    print(f"三亚 2026-04 订单特意减少（模拟下滑），可用于测试异动归因分析")


if __name__ == "__main__":
    main()
