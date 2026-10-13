import { lazy, Suspense, useEffect, useState } from 'react'
import { api } from '../api'
const DeskChart = lazy(() => import('./DeskChart'))
type Review = {
  state?: string; version: string; definition_sha256: string; data_date: string; valid_until: string
  initial_usd: number; activation_date: string; strategy_return_pct: number; benchmark_return_pct: number
  strategy_drawdown_pct: number; benchmark_drawdown_pct: number; mean_exposure_pct: number; fees_usd: number
  curve: { date: string; equity_usd: number; benchmark_usd: number; cash_usd: number }[]
  by_symbol: { symbol: string; realized_usd: number; floating_usd: number; total_usd: number; buys: number; sells: number }[]
  audit: { first_data_date: string; recorded_sessions: number; late_signals: number; missing_sessions: string[]; unverified_fill_sessions: number; confirmed_publications: number; late_publications: number
    records: { data_date: string; recorded_at_utc: string; signal_before_next_open: boolean; fills_linked_to_prior_plan: boolean; fills_match_prior_plan: boolean | null; fill_count: number; publication: { published_at_utc: string; before_next_open: boolean } | null; next_open_plan: { next_open_date: string; actions: { symbol: string; side: string }[] } }[] }
  run: { captured_at_utc: string; github_run_url: string | null }
}
const money = (n: number) => `${n < 0 ? '−' : ''}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const pct = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
const tone = (n: number) => n < 0 ? 'negative' : 'positive'
export default function AccountReview({ revision, accountDate }: { revision: number; accountDate: string }) {
  const [review, setReview] = useState<Review | null>(null)
  const [error, setError] = useState('')
  const [checkedAt, setCheckedAt] = useState(() => Date.now())
  useEffect(() => {
    let active = true
    api<Review>('/account/observation').then(value => { if (active) { setReview(value); setError(''); setCheckedAt(Date.now()) } }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [revision])
  if (error) return <section className="desk-panel"><h2>运行与复盘</h2><p role="alert">观察记录暂未读取成功：{error}</p></section>
  if (!review || review.state === 'not_started') return <section className="desk-panel"><h2>运行与复盘</h2><p>等待首次每日观察记录。</p></section>
  const audit = review.audit
  return <>
    <section className="desk-panel">
      <h2>运行状态与每日留档</h2>
      <p>方案② · {review.version} · 数据截至 {review.data_date}</p>
      {review.data_date !== accountDate && <p className="desk-alert" role="alert">观察数据与账户日期不同，以下仅展示旧日期结果，等待更新。</p>}
      {checkedAt > Date.parse(review.valid_until) && <p className="desk-alert" role="alert">快照已超过下一交易日收盘后的更新时间，等待新数据。请核对 GitHub 最近运行结果。</p>}
      <p className="desk-footnote">最近计算与留档成功：{new Date(review.run.captured_at_utc).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}（北京时间）{review.run.github_run_url && <> · <a href={review.run.github_run_url} target="_blank" rel="noreferrer">查看 GitHub 运行结果</a></>}<br />版本校验码 {review.definition_sha256.slice(0, 12)} · 规则或计算代码变化将阻止该版本继续记账。</p>
      <p>从 {audit.first_data_date} 行情日起保存 {audit.recorded_sessions} 个交易日的观察。迟于下一开盘登记 {audit.late_signals} 日；缺失 {audit.missing_sessions.length} 日；有成交但未关联及时计划 {audit.unverified_fill_sessions} 日。</p>
      <p>已核验网页发布 {audit.confirmed_publications} 日，其中晚于开盘 {audit.late_publications} 日。本次发布回执在部署成功后保存，于下一次更新中展示。</p>
      <p className="desk-footnote">首次留档以前的曲线为历史回放，不代表当时已登记信号。迟到和缺失记录保留原状态，不能补成完整前瞻样本。成交核对品种、方向与原因；数量和价格以模拟开盘成交为准。</p>
      {audit.missing_sessions.length > 0 && <p className="desk-alert">缺失日期：{audit.missing_sessions.join('、')}</p>}
      <details className="desk-rule-details"><summary>每日计划与成交核对</summary><div className="desk-table-scroll"><table className="desk-table"><thead><tr><th>行情日</th><th>登记时间（北京）</th><th>下一开盘前登记</th><th>网页发布核验</th><th>当日成交核对</th><th>下一开盘计划</th></tr></thead><tbody>{audit.records.slice().reverse().map(r => <tr key={r.data_date}><td>{r.data_date}</td><td>{new Date(r.recorded_at_utc).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}</td><td>{r.signal_before_next_open ? '及时' : '迟到'}</td><td>{r.publication ? `${new Date(r.publication.published_at_utc).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })} · ${r.publication.before_next_open ? '及时' : '迟到'}` : '待下次更新核验'}</td><td>{r.fills_match_prior_plan === true && r.fills_linked_to_prior_plan ? `${r.fill_count} 笔，符合及时计划` : r.fill_count ? `${r.fill_count} 笔，缺少及时计划证明` : '无成交'}</td><td>{r.next_open_plan.next_open_date} · {r.next_open_plan.actions.length ? r.next_open_plan.actions.map(t => `${t.symbol}${t.side === 'buy' ? '买' : '卖'}`).join('、') : '无操作'}</td></tr>)}</tbody></table></div></details>
    </section>
    <section className="desk-panel">
      <h2>同本金对照与收益来源</h2><p>{review.activation_date} → {review.data_date} · 两组均从 {money(review.initial_usd)} 开始 · 单边费用 0.1%</p>
      <div className="desk-summary"><article><span>方案②累计收益</span><strong className={tone(review.strategy_return_pct)}>{pct(review.strategy_return_pct)}</strong><small>期间最大回撤 {pct(review.strategy_drawdown_pct)}</small></article><article><span>40/30/30 持有对照</span><strong className={tone(review.benchmark_return_pct)}>{pct(review.benchmark_return_pct)}</strong><small>期间最大回撤 {pct(review.benchmark_drawdown_pct)}</small></article><article><span>平均持仓比例</span><strong>{review.mean_exposure_pct.toFixed(1)}%</strong><small>按每日收盘持仓市值／总资产计算</small></article><article><span>累计模拟费用</span><strong>{money(review.fees_usd)}</strong><small>已包含在策略盈亏中</small></article></div>
      <Suspense fallback={<p>正在绘制资产曲线…</p>}><DeskChart height={280} option={{ animation: false, tooltip: { trigger: 'axis', valueFormatter: (value: number) => money(value) }, legend: { data: ['方案②', '40/30/30 持有'] }, grid: { left: 75, right: 20, top: 45, bottom: 40 }, xAxis: { type: 'category', data: review.curve.map(p => p.date) }, yAxis: { type: 'value', scale: true, axisLabel: { formatter: (v: number) => `$${v.toLocaleString('en-US')}` } }, series: [{ name: '方案②', type: 'line', showSymbol: false, data: review.curve.map(p => p.equity_usd), lineStyle: { color: '#7756df' } }, { name: '40/30/30 持有', type: 'line', showSymbol: false, data: review.curve.map(p => p.benchmark_usd), lineStyle: { color: '#218667' } }] }} /></Suspense>
      <p className="desk-footnote">对照在首日开盘按 40% QLD、30% TQQQ、30% SOXL 买入并持有，不再平衡。两组均使用复权小数份额、费用包含在初始预算中，现金利息与税费未计。两组市场敞口不同；短期收益差不能单独证明策略优劣。</p>
      <div className="desk-table-scroll"><table className="desk-table"><thead><tr><th>品种</th><th>已实现盈亏</th><th>持仓浮盈亏</th><th>合计贡献</th><th>买／卖次数</th></tr></thead><tbody>{review.by_symbol.map(p => <tr key={p.symbol}><td>{p.symbol}</td><td className={tone(p.realized_usd)}>{money(p.realized_usd)}</td><td className={tone(p.floating_usd)}>{money(p.floating_usd)}</td><td className={tone(p.total_usd)}>{money(p.total_usd)}</td><td>{p.buys}／{p.sells}</td></tr>)}</tbody></table></div>
    </section>
  </>
}
