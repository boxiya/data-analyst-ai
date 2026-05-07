# 美团酒旅数据助手

用自然语言查询 MySQL 数据库，基于 RAG + LLM 实现 Text2SQL。

## 整体架构

```
用户在前端输入问题
       ↓
React 前端发请求给 FastAPI 后端
       ↓
RAG 检索（ChromaDB）：根据问题找到相关的表字段信息
       ↓
LLM（DeepSeek）：拿到字段信息 + 用户问题 → 生成 SQL
       ↓
后端执行 SQL → 从 MySQL 拿到数据
       ↓
LLM：拿到数据 → 生成自然语言结论
       ↓
前端展示：结论 + 数据表格 + SQL
```

## 项目结构

```
data-analyst-ai/
├── backend/
│   ├── main.py            # FastAPI 入口，定义所有接口
│   ├── db_service.py      # MySQL 连接和 SQL 执行
│   ├── rag_service.py     # ChromaDB 向量存储和检索
│   ├── llm_service.py     # DeepSeek LLM 调用
│   ├── .env               # 密钥配置（不要上传 Git）
│   └── vector_store/      # ChromaDB 本地数据（自动生成）
└── frontend/
    └── src/
        ├── App.jsx        # React 主界面
        └── App.css        # 样式
```

---

## 在新电脑复现的完整步骤

### 第一步：安装环境

确保电脑上有：
- Python 3.9+（命令：`python --version`）
- Node.js 18+（命令：`node --version`）
- MySQL 8.0（安装见下方）

### 第二步：安装 MySQL

1. 下载地址：https://dev.mysql.com/downloads/mysql/
2. 选 **Server only**，一路默认安装
3. 安装时设置 root 密码，记住它
4. 安装完测试：`"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p`

### 第三步：建数据库和表

进入 MySQL 后执行：

```sql
CREATE DATABASE meituan_travel;
USE meituan_travel;

CREATE TABLE hotels (
    hotel_id INT PRIMARY KEY AUTO_INCREMENT,
    hotel_name VARCHAR(100) COMMENT '酒店名称',
    city VARCHAR(50) COMMENT '所在城市',
    star_level INT COMMENT '星级 1-5',
    avg_price DECIMAL(10,2) COMMENT '平均房价'
);

CREATE TABLE orders (
    order_id INT PRIMARY KEY AUTO_INCREMENT,
    hotel_id INT COMMENT '酒店ID',
    user_id INT COMMENT '用户ID',
    city VARCHAR(50) COMMENT '城市',
    check_in_date DATE COMMENT '入住日期',
    check_out_date DATE COMMENT '退房日期',
    amount DECIMAL(10,2) COMMENT '订单金额',
    status VARCHAR(20) COMMENT '订单状态:completed/cancelled/refunded',
    channel VARCHAR(20) COMMENT '来源渠道:app/h5/mini'
);

CREATE TABLE users (
    user_id INT PRIMARY KEY AUTO_INCREMENT,
    city VARCHAR(50) COMMENT '用户城市',
    register_date DATE COMMENT '注册日期',
    user_level VARCHAR(20) COMMENT '用户等级:普通/银卡/金卡/钻石'
);

INSERT INTO hotels (hotel_name, city, star_level, avg_price) VALUES
('北京国贸大酒店', '北京', 5, 1200.00),
('上海外滩华尔道夫', '上海', 5, 2500.00),
('广州白天鹅宾馆', '广州', 4, 680.00),
('成都锦江宾馆', '成都', 4, 520.00),
('杭州西湖国宾馆', '杭州', 5, 1800.00),
('三亚亚特兰蒂斯', '三亚', 5, 3200.00),
('西安君乐宝酒店', '西安', 3, 380.00),
('重庆洲际酒店', '重庆', 4, 750.00);

INSERT INTO users (city, register_date, user_level) VALUES
('北京', '2023-01-15', '金卡'),
('上海', '2023-03-22', '钻石'),
('广州', '2023-06-10', '普通'),
('成都', '2022-11-05', '银卡'),
('杭州', '2024-01-08', '普通'),
('北京', '2022-08-19', '钻石'),
('三亚', '2023-09-30', '金卡'),
('上海', '2024-02-14', '普通');

INSERT INTO orders (hotel_id, user_id, city, check_in_date, check_out_date, amount, status, channel) VALUES
(1, 1, '北京', '2024-01-10', '2024-01-12', 2400.00, 'completed', 'app'),
(2, 2, '上海', '2024-01-15', '2024-01-17', 5000.00, 'completed', 'h5'),
(3, 3, '广州', '2024-02-01', '2024-02-03', 1360.00, 'completed', 'app'),
(4, 4, '成都', '2024-02-14', '2024-02-15', 520.00, 'cancelled', 'mini'),
(5, 5, '杭州', '2024-03-05', '2024-03-07', 3600.00, 'completed', 'app'),
(6, 6, '三亚', '2024-03-20', '2024-03-25', 16000.00, 'completed', 'app'),
(1, 7, '北京', '2024-04-01', '2024-04-02', 1200.00, 'completed', 'h5'),
(2, 8, '上海', '2024-04-10', '2024-04-12', 5000.00, 'refunded', 'app'),
(7, 1, '西安', '2024-05-01', '2024-05-03', 760.00, 'completed', 'mini'),
(8, 2, '重庆', '2024-05-15', '2024-05-16', 750.00, 'completed', 'app'),
(3, 3, '广州', '2024-06-01', '2024-06-02', 680.00, 'completed', 'h5'),
(6, 4, '三亚', '2024-06-20', '2024-06-25', 16000.00, 'completed', 'app');
```

### 第四步：配置后端

进入 backend 目录，安装依赖：

```
cd data-analyst-ai/backend
pip install fastapi uvicorn python-multipart pymysql openai python-dotenv chromadb sentence-transformers plotly pandas openpyxl
```

新建 `.env` 文件（和 main.py 同目录），内容：

```
DEEPSEEK_API_KEY=你的DeepSeek API Key
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的MySQL密码
MYSQL_DATABASE=meituan_travel
```

DeepSeek API Key 获取地址：https://platform.deepseek.com → 左侧 API Keys

### 第五步：启动后端

```
uvicorn main:app --reload
```

看到 `Uvicorn running on http://127.0.0.1:8000` 说明成功。

### 第六步：初始化 RAG（只需做一次）

打开浏览器访问：http://127.0.0.1:8000/docs

找到 `POST /init`，点 Try it out → Execute。

返回 `表结构已存入 RAG，共 18 个字段` 说明成功。

> 这一步是把数据库的表结构信息向量化存入 ChromaDB，
> 之后用户提问时系统会从这里检索相关字段，告诉 LLM 有哪些表和字段可以用。

### 第七步：启动前端

新开一个终端：

```
cd data-analyst-ai/frontend
npm install
npm run dev
```

打开浏览器访问：http://localhost:5173

### 第八步：开始使用

在输入框里用自然语言提问，例如：
- 「各城市订单总金额是多少？」
- 「三亚有哪些订单？」
- 「各渠道的订单数量对比」
- 「完成状态的订单总金额」

---

## 常见问题

**Q：问题回答不准确或 SQL 报错？**
检查 `/init` 有没有执行，RAG 里需要有表结构数据。

**Q：sentence-transformers 下载很慢？**
首次运行会下载约 90MB 的模型，需要等待，之后会缓存不再下载。

**Q：MySQL 连接失败？**
检查 `.env` 里的密码是否正确，以及 MySQL 服务是否启动（Windows 服务里找 MySQL80）。
