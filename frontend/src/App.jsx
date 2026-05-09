import { useState, useRef, useEffect } from 'react'
import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts'
import './App.css'

const API = 'http://127.0.0.1:8000'

// 分析类型标签
const ANALYSIS_LABELS = {
  anomaly:      { label: '异动归因', color: '#e53e3e' },
  trend:        { label: '趋势分析', color: '#3182ce' },
  comparison:   { label: '对比分析', color: '#805ad5' },
  distribution: { label: '分布分析', color: '#38a169' },
}

// 图表颜色池
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

// ── 加载气泡 ──────────────────────────────────────────────
function LoadingBubble({ loadingText }) {
  return (
    <div className="message assistant">
      <div className="bubble assistant-bubble loading">
        <span className="loading-dot" />
        {loadingText}
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
  const [token, setToken] = useState(() => localStorage.getItem('da_token') || '')
  const [currentUser, setCurrentUser] = useState(() => localStorage.getItem('da_user') || '')
  const [currentRole, setCurrentRole] = useState(() => localStorage.getItem('da_role') || '')

  const [messages, setMessages] = useState([])
  const [history, setHistory] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingText, setLoadingText] = useState('思考中...')
  const [contextSummary, setContextSummary] = useState('')
  const [showLogs, setShowLogs] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const handleLogin = (t, u, r) => {
    setToken(t); setCurrentUser(u); setCurrentRole(r)
    localStorage.setItem('da_token', t)
    localStorage.setItem('da_user', u)
    localStorage.setItem('da_role', r)
  }

  const handleLogout = () => {
    setToken(''); setCurrentUser(''); setCurrentRole('')
    localStorage.removeItem('da_token')
    localStorage.removeItem('da_user')
    localStorage.removeItem('da_role')
    setMessages([]); setHistory([]); setContextSummary('')
  }

  // 未登录 → 显示登录页
  if (!token) return <LoginPage onLogin={handleLogin} />

  const sendQuestion = async () => {
    if (!input.trim() || loading) return
    const question = input.trim()
    setInput('')
    setLoading(true)
    setLoadingText('理解问题中...')
    setMessages(prev => [...prev, { role: 'user', content: question }])

    const t1 = setTimeout(() => setLoadingText('查询数据中...'), 2000)
    const t2 = setTimeout(() => setLoadingText('生成分析结论...'), 5000)

    try {
      const res = await fetch(`${API}/ask`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ question, history, context_summary: contextSummary }),
      })

      // token 过期
      if (res.status === 401) { handleLogout(); return }

      const data = await res.json()
      clearTimeout(t1); clearTimeout(t2)

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
        rag_sources: data.rag_sources,   // RAG 溯源
        source_ids: data.source_ids,     // 数据源标签
      }])

      if (data.context_summary) setContextSummary(data.context_summary)

      const assistantContent = data.conclusion || data.error || '无结果'
      setHistory(prev => [
        ...prev,
        { role: 'user', content: question },
        { role: 'assistant', content: assistantContent },
      ])
    } catch {
      clearTimeout(t1); clearTimeout(t2)
      setMessages(prev => [...prev, { role: 'assistant', error: '请求失败，请检查后端是否启动' }])
    }
    setLoading(false)
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
          {messages.length > 0 && (
            <button className="clear-btn" onClick={clearChat}>清空对话</button>
          )}
          <button className="logout-btn" onClick={handleLogout}>退出</button>
        </div>
      </header>

      <div className="chat-area">
        {messages.length === 0 && (
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

        {loading && <LoadingBubble loadingText={loadingText} />}
        <div ref={bottomRef} />
      </div>

      {showLogs && <LogModal token={token} onClose={() => setShowLogs(false)} />}

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
