import { useState, useRef, useEffect } from 'react'
import './App.css'

const API = 'http://127.0.0.1:8000'

// 分析类型的中文标签和颜色
const ANALYSIS_LABELS = {
  anomaly:      { label: '异动归因', color: '#e53e3e' },
  trend:        { label: '趋势分析', color: '#3182ce' },
  comparison:   { label: '对比分析', color: '#805ad5' },
  distribution: { label: '分布分析', color: '#38a169' },
}

// 数据表格组件
function DataTable({ data }) {
  if (!data || data.length === 0) return <p style={{ color: '#bbb', fontSize: 13 }}>无数据</p>
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{Object.keys(data[0]).map(k => <th key={k}>{k}</th>)}</tr>
        </thead>
        <tbody>
          {data.map((row, ri) => (
            <tr key={ri}>
              {Object.values(row).map((v, vi) => <td key={vi}>{String(v)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// 单个分析步骤卡片
function AnalysisStep({ step }) {
  const [open, setOpen] = useState(false)
  const hasError = !!step.error

  return (
    <div className={`step-card ${hasError ? 'step-error' : ''}`}>
      <div className="step-header" onClick={() => setOpen(o => !o)}>
        <span className="step-num">第 {step.step} 步</span>
        <span className="step-question">{step.sub_question}</span>
        <span className="step-purpose">{step.purpose}</span>
        <span className="step-toggle">{open ? '▲' : '▼'}</span>
      </div>

      {open && (
        <div className="step-body">
          {hasError
            ? <p className="error">查询出错：{step.error}</p>
            : <DataTable data={step.data} />
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

// 助手消息气泡（区分简单查询和分析模式）
function AssistantBubble({ msg }) {
  const isAnalysis = msg.mode === 'analysis'
  const label = isAnalysis ? ANALYSIS_LABELS[msg.analysis_type] : null

  return (
    <div className="bubble assistant-bubble">
      {/* 模式标签 */}
      {isAnalysis && label && (
        <div className="mode-tag" style={{ borderColor: label.color, color: label.color }}>
          ⚡ {label.label}
        </div>
      )}

      {/* 错误提示 */}
      {msg.error && <p className="error">{msg.error}</p>}

      {/* 分析模式：多步骤展示 */}
      {isAnalysis && msg.steps && msg.steps.length > 0 && (
        <div className="steps-wrap">
          <p className="steps-title">分析过程（共 {msg.steps.length} 步）</p>
          {msg.steps.map(s => <AnalysisStep key={s.step} step={s} />)}
        </div>
      )}

      {/* 结论（两种模式都有） */}
      {msg.conclusion && (
        <div className={`conclusion ${isAnalysis ? 'conclusion-analysis' : ''}`}>
          {isAnalysis && <p className="conclusion-label">📊 综合分析结论</p>}
          <p>{msg.conclusion}</p>
        </div>
      )}

      {/* 简单查询模式：数据表格 */}
      {!isAnalysis && msg.data && msg.data.length > 0 && (
        <DataTable data={msg.data} />
      )}

      {/* 简单查询模式：SQL */}
      {!isAnalysis && msg.sql && (
        <details className="sql-detail">
          <summary>查看 SQL</summary>
          <pre>{msg.sql}</pre>
        </details>
      )}
    </div>
  )
}

// 加载中气泡（显示当前进度）
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

export default function App() {
  const [messages, setMessages] = useState([])
  const [history, setHistory] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingText, setLoadingText] = useState('思考中...')
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const sendQuestion = async () => {
    if (!input.trim() || loading) return

    const question = input.trim()
    setInput('')
    setLoading(true)
    setLoadingText('理解问题中...')

    setMessages(prev => [...prev, { role: 'user', content: question }])

    // 模拟进度提示（实际后端是同步的，这里给用户一些反馈）
    const progressTimer = setTimeout(() => setLoadingText('查询数据中...'), 2000)
    const progressTimer2 = setTimeout(() => setLoadingText('生成分析结论...'), 5000)

    try {
      const res = await fetch(`${API}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, history }),
      })
      const data = await res.json()

      clearTimeout(progressTimer)
      clearTimeout(progressTimer2)

      // 把完整的返回结果存入消息列表
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
      }])

      // 更新对话历史（只存文字，供下次追问使用）
      const assistantContent = data.conclusion || data.error || '无结果'
      setHistory(prev => [
        ...prev,
        { role: 'user', content: question },
        { role: 'assistant', content: assistantContent },
      ])

    } catch (e) {
      clearTimeout(progressTimer)
      clearTimeout(progressTimer2)
      setMessages(prev => [...prev, {
        role: 'assistant',
        error: '请求失败，请检查后端是否启动',
      }])
    }

    setLoading(false)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendQuestion()
    }
  }

  const clearChat = () => {
    setMessages([])
    setHistory([])
  }

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>美团酒旅数据助手</h1>
          <p>自然语言查询 · 智能分析 · 支持追问</p>
        </div>
        {messages.length > 0 && (
          <button className="clear-btn" onClick={clearChat}>清空对话</button>
        )}
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
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`message ${msg.role}`}>
            {msg.role === 'user' && (
              <div className="bubble user-bubble">{msg.content}</div>
            )}
            {msg.role === 'assistant' && (
              <AssistantBubble msg={msg} />
            )}
          </div>
        ))}

        {loading && <LoadingBubble loadingText={loadingText} />}

        <div ref={bottomRef} />
      </div>

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
