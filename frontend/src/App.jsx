import { useState, useRef, useEffect } from 'react'
import './App.css'

const API = 'http://127.0.0.1:8000'

function App() {
  const [messages, setMessages] = useState([])  // 界面显示的消息列表
  const [history, setHistory] = useState([])    // 发给后端的对话历史
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef(null)

  // 每次消息更新，自动滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendQuestion = async () => {
    if (!input.trim() || loading) return

    const question = input.trim()
    setInput('')
    setLoading(true)

    // 把用户问题加入界面显示
    setMessages(prev => [...prev, { role: 'user', content: question }])

    try {
      const res = await fetch(`${API}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          history,  // 每次把完整历史对话传给后端
        }),
      })
      const data = await res.json()

      const assistantContent = data.conclusion || data.error || '无结果'

      // 把结果加入界面显示
      setMessages(prev => [...prev, {
        role: 'assistant',
        conclusion: data.conclusion,
        data: data.data,
        sql: data.sql,
        error: data.error,
      }])

      // 更新对话历史（只存文字，不存表格数据）
      // 这个 history 下次提问时会带给后端
      setHistory(prev => [
        ...prev,
        { role: 'user', content: question },
        { role: 'assistant', content: assistantContent },
      ])

    } catch (e) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        error: '请求失败，请检查后端是否启动'
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

  // 清空对话
  const clearChat = () => {
    setMessages([])
    setHistory([])
  }

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>美团酒旅数据助手</h1>
          <p>用自然语言查询业务数据，支持追问</p>
        </div>
        {messages.length > 0 && (
          <button className="clear-btn" onClick={clearChat}>清空对话</button>
        )}
      </header>

      <div className="chat-area">
        {messages.length === 0 && (
          <div className="empty-hint">
            <p>试着问我：</p>
            <p>「各城市完成订单的总金额是多少？」</p>
            <p>「高端用户的订单情况」</p>
            <p>「三亚的订单有哪些？」</p>
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
                            {Object.values(row).map((v, vi) => (
                              <td key={vi}>{String(v)}</td>
                            ))}
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

export default App
