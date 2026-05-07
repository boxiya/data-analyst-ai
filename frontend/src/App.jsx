import { useState } from 'react'
import './App.css'

const API = 'http://127.0.0.1:8000'

function App() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)

  const sendQuestion = async () => {
    if (!input.trim() || loading) return

    const question = input.trim()
    setInput('')
    setLoading(true)

    // 先把用户问题加进对话
    setMessages(prev => [...prev, { role: 'user', content: question }])

    try {
      const res = await fetch(`${API}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      })
      const data = await res.json()

      // 把结果加进对话
      setMessages(prev => [...prev, {
        role: 'assistant',
        conclusion: data.conclusion,
        data: data.data,
        sql: data.sql,
        error: data.error,
      }])
    } catch (e) {
      setMessages(prev => [...prev, { role: 'assistant', error: '请求失败，请检查后端是否启动' }])
    }

    setLoading(false)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendQuestion()
    }
  }

  return (
    <div className="app">
      <header className="header">
        <h1>美团酒旅数据助手</h1>
        <p>用自然语言查询业务数据</p>
      </header>

      <div className="chat-area">
        {messages.length === 0 && (
          <div className="empty-hint">
            <p>试着问我：</p>
            <p>「各城市订单总金额是多少？」</p>
            <p>「三亚的订单有哪些？」</p>
            <p>「各渠道的订单数量对比」</p>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`message ${msg.role}`}>
            {msg.role === 'user' && (
              <div className="bubble user-bubble">{msg.content}</div>
            )}

            {msg.role === 'assistant' && (
              <div className="bubble assistant-bubble">
                {msg.error && <p className="error">{msg.error}</p>}

                {msg.conclusion && (
                  <div className="conclusion">{msg.conclusion}</div>
                )}

                {msg.data && msg.data.length > 0 && (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          {Object.keys(msg.data[0]).map(k => <th key={k}>{k}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {msg.data.map((row, ri) => (
                          <tr key={ri}>
                            {Object.values(row).map((v, vi) => <td key={vi}>{String(v)}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {msg.sql && (
                  <details className="sql-detail">
                    <summary>查看 SQL</summary>
                    <pre>{msg.sql}</pre>
                  </details>
                )}
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="message assistant">
            <div className="bubble assistant-bubble loading">思考中...</div>
          </div>
        )}
      </div>

      <div className="input-area">
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入问题，按 Enter 发送..."
          rows={2}
        />
        <button onClick={sendQuestion} disabled={loading}>
          {loading ? '...' : '发送'}
        </button>
      </div>
    </div>
  )
}

export default App
