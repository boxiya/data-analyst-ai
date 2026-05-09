// ============================================================
// App.jsx —— 前端主文件（React + Vite）
//
// 这个文件包含整个前端的所有组件和逻辑，从上到下依次是：
//
//   常量定义
//   ├─ LogModal        操作日志弹窗（admin专属）
//   ├─ SmartChart      智能图表（自动选柱/折/饼图）
//   ├─ DataTable       数据表格
//   ├─ RagSources      RAG溯源标签
//   ├─ SourceBadges    数据源标签（⚡线上库 ⚡线下库）
//   ├─ StreamSteps     SSE流式进度面板（实时展示每步执行状态）★新增
//   ├─ AssistantBubble AI回复气泡（组合上面几个组件）
//   ├─ LoadingBubble   加载中动画
//   ├─ LoginPage       登录页
//   └─ App             主应用（状态管理 + 发请求 + 布局）
//
// 数据流向（SSE版）：
//   用户输入 → App.sendQuestion()
//     → POST /ask/stream（SSE）
//     → 后端每完成一步推送一个事件
//     → StreamSteps 实时渲染进度
//     → 收到 type="result" 事件 → AssistantBubble 渲染最终结果
//
// 状态说明（App组件里的 useState）：
//   token          → JWT token，从 localStorage 读取，登录后写入
//   currentUser    → 当前用户名
//   currentRole    → 当前角色（admin/analyst/viewer），控制UI显示
//   messages       → 所有对话记录，每条是 {role, content/conclusion/data/...}
//   history        → 发给后端的对话历史（只含 role+content 的精简版）
//   contextSummary → 上一轮分析摘要，下次提问时带上
//   showLogs       → 是否显示操作日志弹窗
//   streamSteps    → 当前正在流式接收的进度步骤列表（loading时显示）
// ============================================================

import { useState, useRef, useEffect } from 'react'
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts'
import './App.css'

// 后端地址，开发环境用 127.0.0.1:8000
const API = 'http://127.0.0.1:8000'

// 分析类型 → 中文标签 + 颜色（显示在回复气泡左上角）
// 由后端 detect_intent() 识别后返回，前端根据这个显示对应标签
const ANALYSIS_LABELS = {
  anomaly:      { label: '异动归因', color: '#e53e3e' },
  trend:        { label: '趋势分析', color: '#3182ce' },
  comparison:   { label: '对比分析', color: '#805ad5' },
  distribution: { label: '分布分析', color: '#38a169' },
}

// 图表颜色池，多条数据时依次取色
const CHART_COLORS = ['#ff6900', '#3182ce', '#38a169', '#805ad5', '#e53e3e', '#d69e2e', '#00b5d8']

// ── 操作日志弹窗（仅 admin 可见）────────────────────────────
function LogModal({ token, onClose }) {
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch(`${API}/logs`, {
      headers: { 'Authorization': `Bearer ${token}` }
    })
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data)) setLogs(data)
        else if (data.logs && Array.isArray(data.logs)) setLogs(data.logs)
        else setError(data.detail || '获取日志失败')
        setLoading(false)
      })
      .catch(() => { setError('网络错误'); setLoading(false) })
  }, [])

  return (
    <div className="log-modal-overlay" onClick={onClose}>
      <div className="log-modal" onClick={e => e.stopPropagation()}>
        <div className="log-modal-header">
          <h3>📋 操作日志</h3>
          <button className="log-close-btn" onClick={onClose}>✕</button>
        </div>
        <div className="log-modal-body">
          {loading && <p className="log-loading">加载中...</p>}
          {error && <p className="log-error">{error}</p>}
          {!loading && !error && logs.length === 0 && <p className="log-empty">暂无日志记录</p>}
          {logs.map((log, i) => (
            <div key={i} className="log-item">
              <div className="log-item-header">
                <span className="log-time">{log.time}</span>
                <span className="log-user">{log.username}</span>
                <span className={`log-role log-role-${log.role}`}>{log.role}</span>
                <span className="log-mode">{log.mode}</span>
              </div>
              <div className="log-question">{log.question}</div>
              {log.source_ids && log.source_ids.length > 0 && (
                <div className="log-sources">
                  {log.source_ids.map((s, j) => (
                    <span key={j} className="log-source-tag">{s}</span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── 智能图表组件 ──────────────────────────────────────────
// 自动判断数据形态，选择最合适的图表类型
function SmartChart({ data, analysisType }) {
  if (!data || data.length === 0) return null

  const keys = Object.keys(data[0]).filter(k => !k.startsWith('_')) // 过滤内部字段
  const numericKeys = keys.filter(k => typeof data[0][k] === 'number' || !isNaN(Number(data[0][k])))
  const labelKey = keys.find(k => typeof data[0][k] === 'string' && !k.startsWith('_')) || keys[0]
  const valueKey = numericKeys[0]

  if (!valueKey || data.length < 2) return null // 数据太少不画图

  // 格式化数据（确保数值是 number 类型）
  const chartData = data.map(row => {
    const item = { name: String(row[labelKey] ?? '') }
    numericKeys.forEach(k => { item[k] = Number(row[k]) || 0 })
    return item
  })

  // 判断图表类型：
  // - distribution（分布）或数据 ≤ 6 条 → 饼图
  // - trend（趋势）或 label 含时间关键词 → 折线图
  // - 其他 → 柱状图
  const isDistribution = analysisType === 'distribution' || data.length <= 6
  const isTrend = analysisType === 'trend' ||
    ['month', 'date', 'week', 'year', '月', '日', '周', '年', '时间'].some(w => labelKey.toLowerCase().includes(w))

  const height = 240

  if (isDistribution && data.length <= 8) {
    // 饼图
    return (
      <div className="chart-wrap">
        <p className="chart-title">📊 数据分布图</p>
        <ResponsiveContainer width="100%" height={height}>
          <PieChart>
            <Pie
              data={chartData}
              dataKey={valueKey}
              nameKey="name"
              cx="50%"
              cy="50%"
              outerRadius={90}
              label={({ name, percent }) => `${name} ${(percent * 100).toFixed(1)}%`}
              labelLine={false}
            >
              {chartData.map((_, i) => (
                <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
              ))}
            </Pie>
            <Tooltip formatter={(v) => v.toLocaleString()} />
          </PieChart>
        </ResponsiveContainer>
      </div>
    )
  }

  if (isTrend) {
    // 折线图
    return (
      <div className="chart-wrap">
        <p className="chart-title">📈 趋势折线图</p>
        <ResponsiveContainer width="100%" height={height}>
          <LineChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey="name" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} tickFormatter={v => v >= 10000 ? `${(v/10000).toFixed(1)}万` : v} />
            <Tooltip formatter={(v) => v.toLocaleString()} />
            <Legend />
            {numericKeys.map((k, i) => (
              <Line key={k} type="monotone" dataKey={k} stroke={CHART_COLORS[i % CHART_COLORS.length]}
                strokeWidth={2} dot={{ r: 3 }} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    )
  }

  // 柱状图（默认）
  return (
    <div className="chart-wrap">
      <p className="chart-title">📊 数据柱状图</p>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} tickFormatter={v => v >= 10000 ? `${(v/10000).toFixed(1)}万` : v} />
          <Tooltip formatter={(v) => v.toLocaleString()} />
          <Legend />
          {numericKeys.map((k, i) => (
            <Bar key={k} dataKey={k} fill={CHART_COLORS[i % CHART_COLORS.length]} radius={[3, 3, 0, 0]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── 数据表格 ──────────────────────────────────────────────
function DataTable({ data }) {
  if (!data || data.length === 0) return <p style={{ color: '#bbb', fontSize: 13 }}>无数据</p>
  // 过滤掉内部字段（_source_id 等）
  const displayData = data.map(row =>
    Object.fromEntries(Object.entries(row).filter(([k]) => !k.startsWith('_')))
  )
  if (displayData.length === 0 || Object.keys(displayData[0]).length === 0) return null
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{Object.keys(displayData[0]).map(k => <th key={k}>{k}</th>)}</tr>
        </thead>
        <tbody>
          {displayData.map((row, ri) => (
            <tr key={ri}>
              {Object.values(row).map((v, vi) => <td key={vi}>{String(v ?? '')}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── RAG 溯源展示 ──────────────────────────────────────────
function RagSources({ sources }) {
  const [open, setOpen] = useState(false)
  if (!sources || sources.length === 0) return null
  return (
    <div className="rag-sources">
      <div className="rag-header" onClick={() => setOpen(o => !o)}>
        <span>🔍 参考知识片段（{sources.length} 条）</span>
        <span>{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <ul className="rag-list">
          {sources.map((s, i) => (
            <li key={i} className="rag-item">
              <span className="rag-tag">{s.type || 'knowledge'}</span>
              {s.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ── 数据源标签 ────────────────────────────────────────────
function SourceBadges({ sourceIds }) {
  if (!sourceIds || sourceIds.length === 0) return null
  const labels = { online: { text: '线上库', color: '#3182ce' }, offline: { text: '线下库', color: '#38a169' } }
  return (
    <div className="source-badges">
      {sourceIds.map(id => {
        const l = labels[id] || { text: id, color: '#999' }
        return <span key={id} className="source-badge" style={{ borderColor: l.color, color: l.color }}>⚡ {l.text}</span>
      })}
    </div>
  )
}

// ── SSE 流式进度面板 ★新增 ────────────────────────────────
//
// 【为什么要有这个组件？】
// 后端 /ask/stream 接口每完成一步就推送一个事件，格式如下：
//   {"type":"step","label":"识别数据源","detail":"线上库","status":"done"}
//   {"type":"step","label":"生成SQL","detail":"SELECT...","status":"running"}
//   {"type":"result","data":{...},"status":"done"}
//
// 这个组件负责把这些事件渲染成可视化的进度列表，
// 让用户实时看到"现在后端在干什么"，而不是傻等一个转圈圈。
//
// 每个步骤有三种状态：
//   running → 显示旋转动画 ⏳，表示正在执行
//   done    → 显示绿色对勾 ✅，表示完成
//   error   → 显示红色叉号 ❌，表示出错
//
// props:
//   steps → 步骤数组，每项是 {label, detail, status}
function StreamSteps({ steps }) {
  if (!steps || steps.length === 0) return null

  // 状态 → 图标映射
  const statusIcon = {
    running: <span className="step-icon step-running">⏳</span>,
    done:    <span className="step-icon step-done">✅</span>,
    error:   <span className="step-icon step-error-icon">❌</span>,
  }

  return (
    <div className="stream-steps">
      <p className="stream-steps-title">⚙️ 执行过程</p>
      {steps.map((step, i) => (
        <div key={i} className={`stream-step stream-step-${step.status}`}>
          {statusIcon[step.status] || statusIcon.running}
          <div className="stream-step-content">
            <span className="stream-step-label">{step.label}</span>
            {step.detail && (
              <span className="stream-step-detail">{step.detail}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── 分析步骤卡片 ──────────────────────────────────────────
function AnalysisStep({ step }) {
  const [open, setOpen] = useState(false)
  const hasError = !!step.error
  return (
    <div className={`step-card ${hasError ? 'step-error' : ''}`}>
      <div className="step-header" onClick={() => setOpen(o => !o)}>
        <span className="step-num">第 {step.step} 步</span>
        <span className="step-question">{step.sub_question}</span>
        <span className="step-purpose">{step.purpose}</span>
        {step.source_name && <span className="step-source">{step.source_name}</span>}
        <span className="step-toggle">{open ? '▲' : '▼'}</span>
      </div>
      {open && (
        <div className="step-body">
          {hasError
            ? <p className="error">查询出错：{step.error}</p>
            : <>
                <SmartChart data={step.data} analysisType={null} />
                <DataTable data={step.data} />
              </>
          }
          {step.sql && (
            <details className="sql-detail">
              <summary>查看 SQL</summary>
              <pre>{step.sql}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  )
}

// ── 助手消息气泡 ──────────────────────────────────────────
function AssistantBubble({ msg }) {
  const isAnalysis = msg.mode === 'analysis'
  const label = isAnalysis ? ANALYSIS_LABELS[msg.analysis_type] : null

  return (
    <div className="bubble assistant-bubble">
      {/* 顶部：模式标签 + 数据源标签 */}
      <div className="bubble-tags">
        {isAnalysis && label && (
          <div className="mode-tag" style={{ borderColor: label.color, color: label.color }}>
            ⚡ {label.label}
          </div>
        )}
        <SourceBadges sourceIds={msg.source_ids} />
      </div>

      {msg.error && <p className="error">{msg.error}</p>}

      {/* 分析模式：多步骤 */}
      {isAnalysis && msg.steps && msg.steps.length > 0 && (
        <div className="steps-wrap">
          <p className="steps-title">分析过程（共 {msg.steps.length} 步）</p>
          {msg.steps.map(s => <AnalysisStep key={`${s.step}-${s.source_id}`} step={s} />)}
        </div>
      )}

      {/* 结论 */}
      {msg.conclusion && (
        <div className={`conclusion ${isAnalysis ? 'conclusion-analysis' : ''}`}>
          {isAnalysis && <p className="conclusion-label">📊 综合分析结论</p>}
          <p>{msg.conclusion}</p>
        </div>
      )}

      {/* 简单查询：图表 + 表格 */}
      {!isAnalysis && msg.data && msg.data.length > 0 && (
        <>
          <SmartChart data={msg.data} analysisType={msg.analysis_type} />
          <DataTable data={msg.data} />
        </>
      )}

      {/* 简单查询：SQL */}
      {!isAnalysis && msg.sql && (
        <details className="sql-detail">
          <summary>查看 SQL</summary>
          <pre>{msg.sql}</pre>
        </details>
      )}

      {/* RAG 溯源 */}
      <RagSources sources={msg.rag_sources} />

      {/* 上下文摘要 */}
      {msg.context_summary && (
        <div className="context-summary">
          🧠 已记住：{msg.context_summary}
        </div>
      )}
    </div>
  )
}

// ── 全链路追踪面板 ★新增 ──────────────────────────────────
//
// 【这个组件是干什么的？】
// 用户提问后，后端会把这次请求产生的所有数据打包存起来。
// 点击"查看链路"按钮，这个面板就会拉取 /trace/last 接口，
// 展示从登录到拿到结论的每一步：
//
//   ① 身份层  → 你是谁、用的什么 token、角色是什么
//   ② 意图层  → AI 识别出你在问什么类型的问题、选了哪些数据库
//   ③ RAG层   → 向量库检索到了哪些业务规则（存在 vector_store/）
//   ④ SQL层   → 生成了什么 SQL、执行返回了多少行数据
//   ⑤ 结论层  → AI 生成的分析结论和上下文摘要
//   ⑥ 日志层  → 写入 operation_logs.jsonl 的那条记录长什么样
//   ⑦ 执行步骤→ SSE 推送的每一步进度事件
//
// 每一层都用不同颜色的卡片区分，点击可以展开/收起详情。
function TracePanel({ token, onClose }) {
  const [trace, setTrace] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [openSections, setOpenSections] = useState({ identity: true, intent: true, rag: false, sql: false, conclusion: false, log: false, steps: false })

  useEffect(() => {
    fetch(`${API}/trace/last`, {
      headers: { 'Authorization': `Bearer ${token}` }
    })
      .then(r => r.json())
      .then(data => {
        if (data.trace) setTrace(data.trace)
        else setError(data.message || '暂无追踪数据')
        setLoading(false)
      })
      .catch(() => { setError('网络错误'); setLoading(false) })
  }, [])

  const toggle = (key) => setOpenSections(prev => ({ ...prev, [key]: !prev[key] }))

  // 每个"层"的配置：标题、颜色、图标
  const sections = [
    {
      key: 'identity',
      icon: '🔐',
      title: '① 身份层',
      subtitle: '谁在请求、用的什么 token',
      color: '#805ad5',
      render: (t) => (
        <div className="trace-kv">
          <div><span className="trace-k">用户名</span><span className="trace-v">{t.identity.username}</span></div>
          <div><span className="trace-k">角色</span><span className="trace-v">{t.identity.role}</span></div>
          <div><span className="trace-k">Token</span><span className="trace-v trace-mono">{t.identity.token_info}</span></div>
          <div><span className="trace-k">存储位置</span><span className="trace-v">浏览器 localStorage["da_token"]</span></div>
        </div>
      )
    },
    {
      key: 'intent',
      icon: '🧠',
      title: '② 意图层',
      subtitle: 'AI 识别问题类型 + 选数据源',
      color: '#3182ce',
      render: (t) => (
        <div className="trace-kv">
          <div><span className="trace-k">问题</span><span className="trace-v">{t.intent.question}</span></div>
          <div><span className="trace-k">查询模式</span><span className="trace-v">{t.intent.mode === 'analysis' ? `分析型（${t.intent.analysis_type}）` : '简单查询'}</span></div>
          <div><span className="trace-k">数据源</span><span className="trace-v">{(t.intent.source_ids || []).join('、') || '无'}</span></div>
          {t.intent.detected?.reason && (
            <div><span className="trace-k">判断理由</span><span className="trace-v">{t.intent.detected.reason}</span></div>
          )}
          <div><span className="trace-k">存储位置</span><span className="trace-v">内存（不持久化）</span></div>
        </div>
      )
    },
    {
      key: 'rag',
      icon: '🔍',
      title: '③ RAG层',
      subtitle: '向量检索命中的业务规则',
      color: '#38a169',
      render: (t) => (
        <div className="trace-kv">
          <div><span className="trace-k">命中条数</span><span className="trace-v">{t.rag.knowledge_count} 条</span></div>
          <div><span className="trace-k">存储位置</span><span className="trace-v trace-mono">{t.rag.stored_in}</span></div>
          {(t.rag.knowledge_hits || []).map((h, i) => (
            <div key={i}><span className="trace-k">规则{i + 1}</span><span className="trace-v">[{h.type}] {h.text}</span></div>
          ))}
          {t.rag.knowledge_count === 0 && <div><span className="trace-v" style={{color:'#aaa'}}>本次未命中业务规则</span></div>}
        </div>
      )
    },
    {
      key: 'sql',
      icon: '🗄️',
      title: '④ SQL层',
      subtitle: '生成的 SQL + 执行结果行数',
      color: '#e53e3e',
      render: (t) => (
        <div>
          {(t.sql || []).map((s, i) => (
            <div key={i} className="trace-sql-block">
              {s.step && <div className="trace-sql-step">第 {s.step} 步：{s.sub_question}</div>}
              <div className="trace-kv">
                <div><span className="trace-k">数据源</span><span className="trace-v">{s.source_id}</span></div>
                <div><span className="trace-k">返回行数</span><span className="trace-v">{s.row_count} 行</span></div>
                {s.error && <div><span className="trace-k">错误</span><span className="trace-v" style={{color:'#e53e3e'}}>{s.error}</span></div>}
              </div>
              {s.sql && <pre className="trace-sql-pre">{s.sql}</pre>}
            </div>
          ))}
          <div className="trace-kv" style={{marginTop:8}}>
            <div><span className="trace-k">执行位置</span><span className="trace-v">MySQL（db_service.py → execute_sql）</span></div>
          </div>
        </div>
      )
    },
    {
      key: 'conclusion',
      icon: '📊',
      title: '⑤ 结论层',
      subtitle: 'AI 生成的分析结论',
      color: '#d69e2e',
      render: (t) => (
        <div className="trace-kv">
          <div><span className="trace-k">结论</span><span className="trace-v">{t.conclusion.text || '（无）'}</span></div>
          <div><span className="trace-k">上下文摘要</span><span className="trace-v">{t.conclusion.context_summary || '（无）'}</span></div>
          <div><span className="trace-k">生成方式</span><span className="trace-v">DeepSeek API（llm_service.py → generate_conclusion）</span></div>
        </div>
      )
    },
    {
      key: 'log',
      icon: '📋',
      title: '⑥ 日志层',
      subtitle: '写入 operation_logs.jsonl 的记录',
      color: '#718096',
      render: (t) => (
        <div className="trace-kv">
          <div><span className="trace-k">文件路径</span><span className="trace-v trace-mono">{t.log_record.file}</span></div>
          <div><span className="trace-k">写入格式</span><span className="trace-v">jsonl（每行一条 JSON）</span></div>
          <div><span className="trace-k">写入内容</span></div>
          <pre className="trace-sql-pre">{JSON.stringify(t.log_record.written, null, 2)}</pre>
          <div><span className="trace-k">谁能查看</span><span className="trace-v">仅 admin 角色（/logs 接口）</span></div>
        </div>
      )
    },
    {
      key: 'steps',
      icon: '⚙️',
      title: '⑦ 执行步骤',
      subtitle: 'SSE 推送的每一步进度事件',
      color: '#00b5d8',
      render: (t) => (
        <div>
          {(t.steps || []).map((s, i) => (
            <div key={i} className="trace-step-row">
              <span className={`trace-step-status trace-step-${s.status}`}>
                {s.status === 'done' ? '✅' : s.status === 'error' ? '❌' : '⏳'}
              </span>
              <span className="trace-step-label">{s.label}</span>
              {s.detail && <span className="trace-step-detail">{s.detail}</span>}
            </div>
          ))}
          {(!t.steps || t.steps.length === 0) && <span style={{color:'#aaa',fontSize:13}}>无步骤记录</span>}
        </div>
      )
    },
  ]

  return (
    <div className="trace-overlay" onClick={onClose}>
      <div className="trace-panel" onClick={e => e.stopPropagation()}>
        <div className="trace-header">
          <div>
            <h3>🔗 全链路追踪</h3>
            <p className="trace-header-sub">上一次请求从登录到结论，每一步产生了什么、存在哪里</p>
          </div>
          <button className="log-close-btn" onClick={onClose}>✕</button>
        </div>

        <div className="trace-body">
          {loading && <p className="log-loading">加载中...</p>}
          {error && <p className="log-error">{error}</p>}
          {trace && (
            <>
              <div className="trace-time">请求时间：{trace.time}</div>
              {sections.map(sec => (
                <div key={sec.key} className="trace-section" style={{ borderLeftColor: sec.color }}>
                  <div className="trace-section-header" onClick={() => toggle(sec.key)}>
                    <span className="trace-section-icon">{sec.icon}</span>
                    <div>
                      <span className="trace-section-title" style={{ color: sec.color }}>{sec.title}</span>
                      <span className="trace-section-sub">{sec.subtitle}</span>
                    </div>
                    <span className="trace-toggle">{openSections[sec.key] ? '▲' : '▼'}</span>
                  </div>
                  {openSections[sec.key] && (
                    <div className="trace-section-body">
                      {sec.render(trace)}
                    </div>
                  )}
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── 加载气泡（SSE版：显示实时进度步骤）────────────────────
//
// 【和旧版的区别】
// 旧版：只显示一行文字"查询数据中..."，用户看不到任何进度
// 新版：实时展示 StreamSteps 进度面板，每完成一步立刻更新
//
// streamSteps 由 App.sendQuestion() 通过 SSE 事件实时更新，
// 每收到一个 type="step" 事件就追加/更新一条步骤记录。
function LoadingBubble({ streamSteps }) {
  return (
    <div className="message assistant">
      <div className="bubble assistant-bubble loading">
        {streamSteps && streamSteps.length > 0
          // 有进度步骤时：显示实时进度面板
          ? <StreamSteps steps={streamSteps} />
          // 还没收到第一个步骤时：显示初始等待动画
          : <><span className="loading-dot" /> 连接中...</>
        }
      </div>
    </div>
  )
}

// ── 登录页 ────────────────────────────────────────────────
function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleLogin = async () => {
    if (!username || !password) { setError('请输入用户名和密码'); return }
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await res.json()
      if (res.ok && data.token) {
        onLogin(data.token, data.username, data.role)
      } else {
        setError(data.detail || '用户名或密码错误')
      }
    } catch {
      setError('无法连接服务器，请确认后端已启动')
    }
    setLoading(false)
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-logo">🏨</div>
        <h2 className="login-title">美团酒旅数据助手</h2>
        <p className="login-sub">企业级 AI 数据分析平台</p>
        <div className="login-form">
          <input
            className="login-input"
            placeholder="用户名"
            value={username}
            onChange={e => setUsername(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleLogin()}
          />
          <input
            className="login-input"
            type="password"
            placeholder="密码"
            value={password}
            onChange={e => setPassword(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleLogin()}
          />
          {error && <p className="login-error">{error}</p>}
          <button className="login-btn" onClick={handleLogin} disabled={loading}>
            {loading ? '登录中...' : '登 录'}
          </button>
        </div>
        <p className="login-hint">测试账号：analyst / 123456 &nbsp;|&nbsp; admin / admin888</p>
      </div>
    </div>
  )
}

// ── 主应用 ────────────────────────────────────────────────
export default function App() {
  // ── 登录状态（从 localStorage 初始化，刷新页面不丢失）──
  // () => ... 是惰性初始化，只在组件第一次渲染时执行一次
  const [token, setToken] = useState(() => localStorage.getItem('da_token') || '')
  const [currentUser, setCurrentUser] = useState(() => localStorage.getItem('da_user') || '')
  const [currentRole, setCurrentRole] = useState(() => localStorage.getItem('da_role') || '')

  // ── 对话状态（内存中，刷新页面会清空）──────────────────
  const [messages, setMessages] = useState([])      // 页面上显示的所有消息
  const [history, setHistory] = useState([])        // 发给后端的对话历史（精简版）
  const [input, setInput] = useState('')            // 输入框内容
  const [loading, setLoading] = useState(false)     // 是否正在等待后端响应
  const [contextSummary, setContextSummary] = useState('') // 上一轮分析摘要
  const [showLogs, setShowLogs] = useState(false)   // 是否显示操作日志弹窗
  const [showTrace, setShowTrace] = useState(false) // 是否显示全链路追踪面板

  // ── SSE 流式进度状态 ★新增 ──────────────────────────────
  // streamSteps：当前正在接收的进度步骤列表，loading 期间显示在 LoadingBubble 里
  // 格式：[{label:"识别数据源", detail:"线上库", status:"done"}, ...]
  // 每收到一个 SSE step 事件就更新这个列表，收到 result 事件后清空
  const [streamSteps, setStreamSteps] = useState([])
  // loadingText：进度面板底部的当前步骤文字（"识别数据源：线上库"）
  const [loadingText, setLoadingText] = useState('连接中...')

  // 用于自动滚动到最新消息
  const bottomRef = useRef(null)
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading, streamSteps])

  // ── 登录成功回调 ──────────────────────────────────────
  // LoginPage 组件登录成功后调用这个函数，传入 token/username/role
  // 同时写入 localStorage，下次刷新页面不需要重新登录
  const handleLogin = (t, u, r) => {
    setToken(t); setCurrentUser(u); setCurrentRole(r)
    localStorage.setItem('da_token', t)
    localStorage.setItem('da_user', u)
    localStorage.setItem('da_role', r)
  }

  // ── 退出登录 ──────────────────────────────────────────
  // 清空所有状态和 localStorage，回到登录页
  // token 过期时（后端返回 401）也会自动调用这个函数
  const handleLogout = () => {
    setToken(''); setCurrentUser(''); setCurrentRole('')
    localStorage.removeItem('da_token')
    localStorage.removeItem('da_user')
    localStorage.removeItem('da_role')
    setMessages([]); setHistory([]); setContextSummary('')
    setStreamSteps([])
  }

  // token 为空 → 显示登录页（React 条件渲染）
  if (!token) return <LoginPage onLogin={handleLogin} />

  // ── 发送问题（SSE流式版）★核心改动 ──────────────────────
  //
  // 【和旧版 /ask 的区别】
  // 旧版：fetch POST /ask → 等待全部完成 → 一次性拿到结果
  //   缺点：用户等待 5-10 秒，页面没有任何反馈
  //
  // 新版：fetch POST /ask/stream → 读取 ReadableStream
  //   后端每完成一步推送一个 SSE 事件（"data: {...}\n\n"）
  //   前端实时解析并更新 streamSteps，用户能看到每步进度
  //   最后收到 type="result" 事件，渲染完整结果
  //
  // 【为什么不用 EventSource？】
  // EventSource 只支持 GET 请求，无法发送 POST body（question/history）
  // 所以用 fetch + ReadableStream 手动读取 SSE 流
  //
  // 【SSE 事件格式】
  // 后端推送的每个事件是一行文本：
  //   data: {"type":"step","label":"识别数据源","detail":"线上库","status":"done"}\n\n
  //   data: {"type":"result","data":{...},"status":"done"}\n\n
  const sendQuestion = async () => {
    if (!input.trim() || loading) return
    const question = input.trim()
    setInput('')
    setLoading(true)
    setStreamSteps([])  // 清空上一次的进度步骤

    // 立即把用户消息显示出来，不等后端响应
    setMessages(prev => [...prev, { role: 'user', content: question }])

    try {
      // 发起 SSE 请求（POST + ReadableStream）
      const res = await fetch(`${API}/ask/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ question, history, context_summary: contextSummary }),
      })

      // 后端返回 401 说明 token 过期，自动退出登录
      if (res.status === 401) { handleLogout(); return }

      // 非 200 说明接口不存在或后端出错，降级到普通 /ask 接口
      if (!res.ok || !res.body) {
        console.warn('[SSE] 流式接口不可用，降级到 /ask，状态码:', res.status)
        const fallback = await fetch(`${API}/ask`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
          body: JSON.stringify({ question, history, context_summary: contextSummary }),
        })
        if (fallback.status === 401) { handleLogout(); return }
        const data = await fallback.json()
        if (data.detail) throw new Error(data.detail)
        setMessages(prev => [...prev, {
          role: 'assistant', mode: data.mode, analysis_type: data.analysis_type,
          steps: data.steps, conclusion: data.conclusion, data: data.data,
          sql: data.sql, error: data.error, intent: data.intent,
          context_summary: data.context_summary, rag_sources: data.rag_sources, source_ids: data.source_ids,
        }])
        if (data.context_summary) setContextSummary(data.context_summary)
        setHistory(prev => [...prev, { role: 'user', content: question }, { role: 'assistant', content: data.conclusion || '无结果' }])
        setLoading(false); setStreamSteps([]); return
      }

      // 获取可读流，用于逐块读取 SSE 数据
      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''  // 缓冲区，处理跨块的不完整事件

      // 持续读取流，直到后端关闭连接
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        // 把二进制数据解码成字符串，追加到缓冲区
        buffer += decoder.decode(value, { stream: true })

        // SSE 每个事件以 \n\n 结尾，按此分割
        const parts = buffer.split('\n\n')
        // 最后一段可能不完整，留在缓冲区等下一块
        buffer = parts.pop()

        for (const part of parts) {
          // SSE 格式："data: {...}"，去掉 "data: " 前缀
          const line = part.trim()
          if (!line.startsWith('data:')) continue
          const jsonStr = line.slice(5).trim()
          let event
          try { event = JSON.parse(jsonStr) } catch { continue }

          if (event.type === 'step') {
            // 每收到一个 step 事件，更新进度列表
            // 用 label 作为 key，同一步骤的 running→done 状态更新
            setStreamSteps(prev => {
              const idx = prev.findIndex(s => s.label === event.label)
              if (idx >= 0) {
                // 已存在这个步骤，更新状态
                const next = [...prev]
                next[idx] = event
                return next
              }
              return [...prev, event]  // 新步骤，追加
            })
            // 同步更新 loadingText，显示当前在干什么
            setLoadingText(event.label + (event.detail ? `：${event.detail.slice(0, 30)}` : ''))

          } else if (event.type === 'result') {
            // 收到最终结果，渲染完整回复
            const data = event.data
            setMessages(prev => [...prev, {
              role: 'assistant',
              mode: data.mode,
              analysis_type: data.analysis_type,
              steps: data.steps,
              conclusion: data.conclusion,
              data: data.data,
              sql: data.sql,
              error: data.error,
              intent: data.intent,
              context_summary: data.context_summary,
              rag_sources: data.rag_sources,
              source_ids: data.source_ids,
            }])
            if (data.context_summary) setContextSummary(data.context_summary)
            const assistantContent = data.conclusion || data.error || '无结果'
            setHistory(prev => [
              ...prev,
              { role: 'user', content: question },
              { role: 'assistant', content: assistantContent },
            ])

          } else if (event.type === 'error') {
            // 后端推送了错误事件
            setMessages(prev => [...prev, { role: 'assistant', error: event.detail || '执行出错' }])
          }
        }
      }
    } catch (err) {
      console.error('[sendQuestion] 错误:', err)
      setMessages(prev => [...prev, { role: 'assistant', error: `请求失败：${err.message || '请检查后端是否启动'}` }])
    }

    setLoading(false)
    setStreamSteps([])  // 清空进度步骤，避免下次提问时残留
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendQuestion() }
  }

  const clearChat = () => {
    setMessages([]); setHistory([]); setContextSummary('')
  }

  const roleLabel = { admin: '管理员', analyst: '分析师', viewer: '访客' }

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>美团酒旅数据助手</h1>
          <p>自然语言查询 · 智能分析 · 多数据源</p>
        </div>
        <div className="header-right">
          <span className="user-info">
            👤 {currentUser}
            <span className="role-badge">{roleLabel[currentRole] || currentRole}</span>
          </span>
          {currentRole === 'admin' && (
            <button className="log-btn" onClick={() => setShowLogs(true)}>📋 操作日志</button>
          )}
          {messages.some(m => m.role === 'assistant' && !m.error) && (
            <button className="trace-btn" onClick={() => setShowTrace(true)}>🔗 查看链路</button>
          )}
          {messages.length > 0 && (
            <button className="clear-btn" onClick={clearChat}>清空对话</button>
          )}
          <button className="logout-btn" onClick={handleLogout}>退出</button>
        </div>
      </header>

      <div className="chat-area">
        {messages.length === 0 && !loading && (
          <div className="empty-hint">
            <p className="hint-title">试着问我：</p>
            <div className="hint-chips">
              <span onClick={() => setInput('各城市完成订单的总金额是多少？')}>各城市完成订单的总金额</span>
              <span onClick={() => setInput('三亚的订单为什么下滑？')}>三亚订单为什么下滑</span>
              <span onClick={() => setInput('APP和小程序渠道哪个更好？')}>APP和小程序渠道对比</span>
              <span onClick={() => setInput('各渠道的订单占比分布')}>各渠道订单占比分布</span>
              <span onClick={() => setInput('线下门店三亚的订单数量')}>线下门店三亚订单</span>
              <span onClick={() => setInput('对比线上和线下渠道的订单数量')}>线上线下渠道对比</span>
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`message ${msg.role}`}>
            {msg.role === 'user' && <div className="bubble user-bubble">{msg.content}</div>}
            {msg.role === 'assistant' && <AssistantBubble msg={msg} />}
          </div>
        ))}

        {/* 加载中：先显示实时进度步骤，再显示 loading 气泡 */}
        {loading && (
          <div className="message assistant">
            <div className="bubble assistant-bubble">
              {streamSteps.length > 0 && <StreamSteps steps={streamSteps} />}
              <div className="loading-inline">
                <span className="loading-dot" />
                {loadingText}
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {showLogs && <LogModal token={token} onClose={() => setShowLogs(false)} />}
      {showTrace && <TracePanel token={token} onClose={() => setShowTrace(false)} />}

      <div className="input-area">
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入问题，按 Enter 发送，Shift+Enter 换行..."
          rows={2}
        />
        <button onClick={sendQuestion} disabled={loading}>
          {loading ? '...' : '发送'}
        </button>
      </div>
    </div>
  )
}
