import { useEffect, useState } from 'react'
import { api } from '../api'

type Quote = { symbol: string; label: string; date: string; fresh: boolean; close: number; return_1d_pct: number; return_5d_pct: number; return_20d_pct: number; distance_ma50_pct: number; distance_ma200_pct: number; drawdown_252_pct: number; change_5d_bp: number | null; source_url: string }
type Brief = {
  status: string; version: string; market_date: string; market_ready: boolean; generated_at_utc: string; valid_until: string
  assessment: { trend: string; short_term: string; risk: string; summary: string; technology?: string }
  quotes: Quote[]; vix: { close: number; change_5d_pct: number; prior252_percentile: number; source_url: string } | null
  breadth_relative_20d_pp: number | null; calendar_complete: boolean
  news: { title: string; url: string; source: string; published_at_utc: string; tags: string[] }[]
  events: { date: string; time_utc: string | null; title: string; original_title?: string; source: string; url: string; timing_note: string }[]
  sources: { name: string; kind: string; ok: boolean; message: string | null; url: string; data_date: string | null }[]
}
const pct = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
const tone = (n: number) => n < 0 ? 'negative' : 'positive'
const beijing = (date: string) => new Date(date).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })

export default function MarketBrief({ revision }: { revision: number }) {
  const [brief, setBrief] = useState<Brief | null>(null)
  const [error, setError] = useState('')
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
  const vix = brief.vix
  const failed = brief.sources.filter(s => !s.ok)
  return <section className="desk-panel market-brief">
    <div className="desk-section-heading"><div><span className="desk-eyebrow">放下单只盈亏 · 先看整个市场</span><h2>市场晨报</h2><p>收盘判断 {brief.market_date}（美东） · 新闻核查 {beijing(brief.generated_at_utc)}（北京）</p></div><span className="market-rule-badge">规则计算 · 无 AI 调用</span></div>
    {!usable && <div className="desk-alert" role="alert">{expired ? '收盘快照已过期，暂停展示当前环境结论。' : '主要行情未到齐，暂停环境判断。'} 下方保留的数字仅供核对日期。</div>}
    <div className="market-state-grid">
      <article><span>中期趋势</span><strong>{usable ? brief.assessment.trend : '待更新'}</strong><small>标普、纳指、小盘与50／200日均线</small></article>
      <article><span>短期状态</span><strong>{usable ? brief.assessment.short_term : '待更新'}</strong><small>三个市场近5个交易日的走势</small></article>
      <article className={usable && ['偏高','很高'].includes(brief.assessment.risk) ? 'market-risk-high' : ''}><span>波动与急跌风险</span><strong>{usable ? brief.assessment.risk : '待核验'}</strong><small>VIX与大盘近期跌幅；不是亏损概率</small></article>
    </div>
    {usable && <p className="market-verdict">{brief.assessment.summary}</p>}
    <div className="market-quotes">{['SPY','QQQ','IWM','SOXX'].map(symbol => {
      const q = brief.quotes.find(x => x.symbol === symbol)
      return <article key={symbol}><div><b>{q?.label ?? symbol}</b><small>{symbol}{q ? ` · $${q.close.toFixed(2)}` : ''}</small></div>{q ? <><strong className={tone(q.return_1d_pct)}>{pct(q.return_1d_pct)}</strong><span>近5日 <b className={tone(q.return_5d_pct)}>{pct(q.return_5d_pct)}</b></span><small>近20日 {pct(q.return_20d_pct)}<br />距50日线 {pct(q.distance_ma50_pct)} · 距200日线 {pct(q.distance_ma200_pct)}</small></> : <p>行情待核验</p>}</article>
    })}</div>
    <p className="desk-footnote">上方使用未加杠杆ETF作为市场代理；大字为当日涨跌，价格为ETF原始收盘价，涨跌与均线距离按复权价格计算。</p>
    <div className="market-context-strip"><span>VIX <b>{vix ? vix.close.toFixed(2) : '待核验'}</b>{vix && <> · 自身前252日分位 {vix.prior252_percentile.toFixed(0)}%</>}</span><span>等权相对大盘近20日 <b>{brief.breadth_relative_20d_pp == null ? '待核验' : `${brief.breadth_relative_20d_pp >= 0 ? '+' : ''}${brief.breadth_relative_20d_pp.toFixed(2)}个百分点`}</b></span>{['^TNX','UUP','GLD'].map(symbol => { const q = brief.quotes.find(x => x.symbol === symbol); const label = { '^TNX': '美债10年收益率', UUP: '美元ETF', GLD: '黄金ETF' }[symbol]; return <span key={symbol}>{label} <b>{q ? symbol === '^TNX' ? `${q.close.toFixed(2)}%` : pct(q.return_5d_pct) : '待核验'}</b>{q && <small> {symbol === '^TNX' ? `近5日${q.change_5d_bp! >= 0 ? '+' : ''}${q.change_5d_bp!.toFixed(0)}基点` : '近5日'}</small>}</span> })}</div>
    <div className="market-reading-grid">
      <div><h3>未来14天 · 重要日程</h3>{!brief.calendar_complete && <p className="market-source-warning">部分官方日历暂不可读；以下仅为已核验日程，不代表没有其他重要事件。</p>}{brief.events.length ? <ul className="market-events">{brief.events.map((e,i) => <li key={`${e.date}-${i}`}><time>{e.time_utc ? `${beijing(e.time_utc)} 北京` : `${e.date} 美东日期`}</time><a href={e.url} target="_blank" rel="noreferrer">{e.title}</a><small>{e.source} · {e.timing_note}</small></li>)}</ul> : <p>当前可读取来源未发现未来14天的匹配事件；请结合官方日历核对。</p>}</div>
      <div><h3>近72小时 · 相关标题</h3><p className="desk-footnote">按主题筛选、按时间排列；保留原标题，不自动认定利好利空。新闻可能比左上方收盘行情更新。</p>{brief.news.length ? <ul className="market-news">{brief.news.map(n => <li key={n.url}><a href={n.url} target="_blank" rel="noreferrer">{n.title}</a><small>{n.source} · {beijing(n.published_at_utc)} 北京 · {n.tags.join('／')}</small></li>)}</ul> : <p>可读取来源暂无近72小时内的匹配标题。</p>}</div>
    </div>
    <details className="desk-rule-details"><summary>判断依据与来源状态{failed.length ? ` · ${failed.length}项待核验` : ''}</summary><p className="desk-footnote">三个大盘至少两个同时位于50日和200日均线上方为中期上行，均在下方为中期下行，其余为分化。近5日平均涨跌达到±1%且至少两个同向，分别标为短期回升／回撤。VIX≥25、VIX近5日升幅≥25%、QQQ近5日≤−5%、SPY≤−3%任一触发风险偏高；VIX≥35、QQQ≤−10%、SPY≤−6%任一触发很高。VIX缺失时不判常态。阈值为固定观察规则，尚未验证预测能力。等权强度是RSP与SPY收益差，不是上涨股票家数。UUP、GLD分别为美元与黄金的ETF代理。原始指数点位不参与跨市场直接比较。<br />规则版本 {brief.version} · 这些信息用于理解环境，尚未接入买卖规则。</p><div className="market-sources">{brief.sources.map(s => <div key={s.name}><a href={s.url} target="_blank" rel="noreferrer">{s.name}</a><span>{s.ok ? s.data_date ? `已核验 · ${s.data_date}` : '已读取' : '暂未读取成功'}</span>{!s.ok && <small>{s.message}</small>}</div>)}</div></details>
  </section>
}
