export type Headline = { title: string; title_zh?: string | null; url: string; source: string; published_at_utc: string; tags: string[] }
export type Event = { date: string; time_utc: string | null; title: string; title_zh?: string | null; original_title?: string; source: string; url: string; timing_note: string }
export type Quote = { symbol: string; label: string; date: string; fresh: boolean; close: number; return_1d_pct: number; return_5d_pct: number; return_20d_pct: number; distance_ma50_pct: number; distance_ma200_pct: number; drawdown_252_pct: number; change_5d_bp: number | null; source_url: string }
export type Brief = {
  status: string; version: string; market_date: string; market_ready: boolean; generated_at_utc: string; valid_until: string
  assessment: { trend: string; short_term: string; risk: string; summary: string; technology?: string }
  quotes: Quote[]; vix: { close: number; change_5d_pct: number; prior252_percentile: number; source_url: string } | null
  breadth_relative_20d_pp: number | null; calendar_complete: boolean
  news: Headline[]; events: Event[]
  sources: { name: string; kind: string; ok: boolean; message: string | null; url: string; data_date: string | null }[]
}
type Action = { symbol: string; side: string; reason?: string; [key: string]: unknown }
export type Account = {
  data_date: string; initial_usd: number; equity_usd: number; cash_usd: number
  positions: Record<string, { lots: number; profit_pct: number; quantity: number }>
  trades: (Action & { date: string; signal_date: string; price: number; quantity: number })[]
  next_open_plan: { signal_date: string; next_open_date: string; actions: Action[] } | null
}
export const pct = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
export const beijing = (date: string) => new Date(date).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
export const translatedTitle = (item: { title: string; title_zh?: string | null }) => item.title_zh || item.title
export const eventTime = (e: Event) => e.time_utc ? `${beijing(e.time_utc)} 北京` : `${e.date} 美东日期`

export function morningReportText(brief: Brief, account: Account | null, now = Date.now()) {
  const usable = brief.market_ready && now <= Date.parse(brief.valid_until)
  const lines = [
    `市场晨报｜美东 ${brief.market_date} 收盘`,
    `生成时间：${beijing(brief.generated_at_utc)} 北京；复制时间：${beijing(new Date(now).toISOString())} 北京`,
    usable ? `环境：${brief.assessment.trend} / ${brief.assessment.short_term} / 风险${brief.assessment.risk}。${brief.assessment.summary}` : '环境：行情未齐或快照已过期，当前判断暂停；以下为历史快照。',
    ...brief.quotes.map(q => `${q.label}(${q.symbol}) ${q.date}：当日${pct(q.return_1d_pct)}，近5日${pct(q.return_5d_pct)}，距200日线${pct(q.distance_ma200_pct)}${q.fresh ? '' : '［待更新］'}`),
    brief.vix ? `VIX ${brief.vix.close.toFixed(2)}，近5日${pct(brief.vix.change_5d_pct)}，前252日分位${brief.vix.prior252_percentile.toFixed(0)}%。` : 'VIX：待核验',
    '\n重要日程：',
    ...brief.events.map(e => `${eventTime(e)}｜${translatedTitle(e)}｜${e.source} ${e.url}`),
    ...(!brief.calendar_complete ? ['部分官方日历暂不可读，日程不完整。'] : []),
    '\n相关要闻（标题机译，可能有误；新闻可晚于收盘）：',
    ...brief.news.map(n => `${beijing(n.published_at_utc)} 北京｜${translatedTitle(n)}${!n.title_zh ? '［原文，未翻译］' : ''}\n原题：${n.title}\n${n.source} ${n.url}`),
  ]
  const failed = brief.sources.filter(s => !s.ok)
  if (failed.length) lines.push(`数据缺口：${failed.map(s => s.name).join('、')}`)
  if (!account) lines.push('\n模拟账户：读取失败，现金、持仓和操作计划未知。')
  else {
    const money = (n: number) => `$${n.toFixed(2)}`
    lines.push(`\n模拟账户｜截至 ${account.data_date}（非券商真实账户）`,
      `本金 ${money(account.initial_usd)}；净值 ${money(account.equity_usd)}；现金 ${money(account.cash_usd)}；持仓市值 ${money(account.equity_usd - account.cash_usd)}；累计盈亏 ${money(account.equity_usd - account.initial_usd)}`)
    if (account.data_date !== brief.market_date) lines.push('注意：账户与市场行情日期不同。')
    lines.push('持仓：', ...Object.entries(account.positions).map(([symbol, p]) => `${symbol}：${p.lots}笔，${p.quantity.toFixed(4)}模拟份额，持仓盈亏${pct(p.profit_pct)}`))
    if (!Object.keys(account.positions).length) lines.push('空仓')
    const fills = account.trades.filter(t => t.date === account.data_date)
    lines.push(`账户日期当日模拟成交：${fills.length ? '' : '无'}`, ...fills.map(t => `${t.date} ${t.symbol} ${t.side === 'buy' ? '买入' : '卖出'} ${t.quantity.toFixed(4)}份，复权价${money(t.price)}；信号日${t.signal_date}；${t.reason || ''}`))
    const plan = account.next_open_plan
    if (!plan) lines.push('下一次操作计划：未取得。')
    else {
      lines.push(`下一开盘计划（未成交）：信号日${plan.signal_date}，执行日${plan.next_open_date}${now > Date.parse(brief.valid_until) ? '［快照过期，仅供历史核对］' : ''}`)
      lines.push(...(plan.actions.length ? plan.actions.map(a => `${a.symbol} ${a.side === 'buy' ? '拟买入' : '拟卖出'}；${a.reason || '按既定规则'}`) : ['无买卖意向']))
    }
  }
  lines.push('\n请根据以上数据分析今天市场环境、主要风险及模拟操作是否合理；区分事实和推测，指出缺失信息，不把计划当成交。')
  return lines.join('\n')
}
