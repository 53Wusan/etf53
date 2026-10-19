import { useEffect, useState } from 'react'
import { api } from '../api'
import MarketContext from './MarketContext'
import { beijing, eventTime, morningReportText, pct, translatedTitle, type Account, type Brief } from '../marketBriefText'
const tone = (n: number) => n < 0 ? 'negative' : 'positive'

export default function MarketBrief({ revision }: { revision: number }) {
  const [brief, setBrief] = useState<Brief | null>(null)
  const [error, setError] = useState('')
  const [copyState, setCopyState] = useState('')
  const [manualText, setManualText] = useState('')
  const [copying, setCopying] = useState(false)
  const [checkedAt, setCheckedAt] = useState(() => Date.now())
  useEffect(() => {
    let active = true
    api<Brief>('/market/brief').then(value => { if (active) { setBrief(value); setError(''); setCheckedAt(Date.now()) } }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [revision])
  if (error) return <section className="desk-panel market-brief"><h2>市场晨报</h2><p role="alert">晨报暂未读取成功，市场状态待核验。</p></section>
  if (!brief || brief.status === 'not_started') return <section className="desk-panel market-brief"><h2>市场晨报</h2><p>等待最新市场快照…</p></section>
  const expired = checkedAt > Date.parse(brief.valid_until)
  const usable = brief.market_ready && !expired
  const failed = brief.sources.filter(s => !s.ok)
  async function copyReport() {
    if (!brief) return
    setCopying(true)
    setCopyState('')
    let account: Account | null = null
    try { account = await api<Account>('/account') } catch { /* Explicitly marked as unavailable in copied text. */ }
    const text = morningReportText(brief, account)
    try {
      await navigator.clipboard.writeText(text)
      setCopyState('已复制，直接粘贴给凌分析')
      setManualText('')
    } catch {
      setManualText(text)
      setCopyState('自动复制不可用，请复制下方已选中的晨报')
    } finally { setCopying(false) }
  }
  return <section className="desk-panel market-brief">
    <div className="desk-section-heading"><div><h2>市场晨报</h2><p>收盘判断 {brief.market_date}（美东） · 更新 {beijing(brief.generated_at_utc)}（北京）</p></div><button className="desk-button" type="button" disabled={copying} onClick={copyReport}>{copying ? '正在整理…' : '一键复制晨报'}</button></div>
    {copyState && <p role="status">{copyState}</p>}
    {manualText && <textarea aria-label="晨报复制内容" rows={8} value={manualText} readOnly style={{ width: '100%', boxSizing: 'border-box' }} ref={element => { element?.focus(); element?.select() }} />}
    {!usable && <div className="desk-alert" role="alert">{expired ? '收盘快照已过期，暂停展示当前环境结论。' : '主要行情未到齐，暂停环境判断。'} 下方保留的数字仅供核对日期。</div>}
    <div className="market-state-grid">
      <article><span>中期趋势</span><strong>{usable ? brief.assessment.trend : '待更新'}</strong></article>
      <article><span>短期状态</span><strong>{usable ? brief.assessment.short_term : '待更新'}</strong></article>
      <article className={usable && ['偏高','很高'].includes(brief.assessment.risk) ? 'market-risk-high' : ''}><span>波动与急跌风险</span><strong>{usable ? brief.assessment.risk : '待核验'}</strong></article>
    </div>
    {usable && <p className="market-verdict">{brief.assessment.summary}</p>}
    <div className="market-quotes">{['SPY','QQQ','IWM','SOXX'].map(symbol => {
      const q = brief.quotes.find(x => x.symbol === symbol)
      return <article key={symbol}><div><b>{q?.label ?? symbol}</b><small>{symbol}</small></div>{q ? <><strong className={tone(q.return_1d_pct)}>{pct(q.return_1d_pct)}</strong><span>近5日 <b className={tone(q.return_5d_pct)}>{pct(q.return_5d_pct)}</b></span></> : <p>行情待核验</p>}</article>
    })}</div>
    <MarketContext brief={brief} usable={usable} />
    <div className="market-reading-grid">
      <div><h3>重要日程</h3>{!brief.calendar_complete && <p className="market-source-warning">部分官方日历暂不可读，日程可能不全。</p>}{brief.events.length ? <ul className="market-events">{brief.events.map((e,i) => <li key={`${e.date}-${i}`}><time>{eventTime(e)}</time><a href={e.url} target="_blank" rel="noreferrer">{translatedTitle(e)}</a></li>)}</ul> : <p>暂无已核验的重要日程。</p>}</div>
      <div><h3>核心要闻</h3><small>近72小时 · 标题机译</small>{brief.news.length ? <ul className="market-news">{brief.news.slice(0, 5).map(n => <li key={n.url}><a href={n.url} target="_blank" rel="noreferrer" title={n.title}>{translatedTitle(n)}{!n.title_zh && <small>（原文）</small>}</a><small>{n.source} · {beijing(n.published_at_utc)} 北京</small></li>)}</ul> : <p>暂无已核验的相关标题。</p>}</div>
    </div>
    <details className="desk-rule-details"><summary>判断依据与来源状态{failed.length ? ` · ${failed.length}项待核验` : ''}</summary><p className="desk-footnote">均线判断中期，近5日涨跌判断短期，VIX及急跌幅度提示风险。仅描述市场状态，未接入买卖规则。标题为机器翻译，悬停可看原题；翻译失败保留原文。规则 {brief.version}。</p><div className="market-sources">{brief.sources.map(s => <div key={s.name}><a href={s.url} target="_blank" rel="noreferrer">{s.name}</a><span>{s.ok ? s.data_date ? `已核验 · ${s.data_date}` : '已读取' : '暂未读取成功'}</span>{!s.ok && <small>{s.message}</small>}</div>)}</div></details>
  </section>
}
