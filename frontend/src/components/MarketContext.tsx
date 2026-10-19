import { pct, type Brief } from '../marketBriefText'

export default function MarketContext({ brief, usable }: { brief: Brief; usable: boolean }) {
  const quote = (symbol: string) => brief.quotes.find(q => q.symbol === symbol && q.fresh)
  const vix = brief.vix
  const yieldQuote = quote('^TNX')
  const dollar = quote('UUP')
  const gold = quote('GLD')
  const direction = (value: number | null | undefined, up: string, down: string) =>
    !usable ? '历史快照 · 待更新' : value == null ? '待核验' : value > 0 ? up : value < 0 ? down : '近5日基本持平'
  const items = [
    {
      label: 'VIX 波动预期', value: vix?.close.toFixed(2) ?? '待核验',
      change: vix ? `近5日 ${pct(vix.change_5d_pct)}` : '',
      hint: direction(vix?.change_5d_pct, '波动预期升温', '波动预期降温'),
      explanation: '衡量市场对标普500未来30天波动的预期。上涨通常表示对冲需求、紧张情绪升温；下跌通常表示情绪缓和。例如VIX为20、近5日+30%：前者是指数水平，后者是它近5日的变化，不是股市下跌概率，也不直接预测方向。',
      url: 'https://www.cboe.com/tradable-products/volatility-trading/',
    },
    {
      label: '美债10年收益率', value: yieldQuote ? `${yieldQuote.close.toFixed(2)}%` : '待核验',
      change: yieldQuote?.change_5d_bp == null ? '' : `近5日 ${yieldQuote.change_5d_bp >= 0 ? '+' : ''}${yieldQuote.change_5d_bp.toFixed(0)}基点`,
      hint: direction(yieldQuote?.change_5d_bp, '利率压力上升', '利率压力缓和'),
      explanation: '这是美国10年期国债的市场收益率，不是美联储政策利率。上升通常抬高融资和估值压力，科技成长股较敏感；下降通常缓和这类压力，但也可能反映经济担忧。收益率与债券价格通常反向。1基点＝0.01个百分点；下降15基点即下降0.15个百分点。',
      url: 'https://www.investor.gov/introduction-investing/investing-basics/glossary/bond-prices-interest-rates',
    },
    {
      label: '美元ETF · UUP', value: dollar ? pct(dollar.return_5d_pct) : '待核验', change: '近5日涨跌',
      hint: direction(dollar?.return_5d_pct, '美元走强', '美元走弱'),
      explanation: 'UUP是美元相对一篮子主要货币走势的ETF代理，不是美元兑人民币。上涨通常对应美元走强，可能增加美元融资和非美资产压力；下跌对应美元走弱，可能缓和压力。它与科技股、黄金并非每天都反向。',
      url: 'https://www.invesco.com/us-rest/contentdetail?contentId=a65d31ae-7cc2-47c9-96f1-78b47809af40&dnsName=us',
    },
    {
      label: '黄金ETF · GLD', value: gold ? pct(gold.return_5d_pct) : '待核验', change: '近5日涨跌',
      hint: direction(gold?.return_5d_pct, '黄金走强', '黄金回落'),
      explanation: 'GLD主要跟踪黄金价格。上涨可能来自避险、实际利率下降或美元走弱；下跌可能来自相反因素或获利回吐。单看黄金涨跌，无法判断股市涨跌；黄金与金矿股的走势也可能不同。',
      url: 'https://www.ssga.com/us/en/institutional/etfs/spdr-gold-shares-gld',
    },
  ]
  return <div className="market-context-strip">{items.map(item => <details className="market-context-item" key={item.label}>
    <summary><span>{item.label} <b>{item.value}</b> <span className="market-context-help" aria-label="点击查看指标说明">ⓘ</span></span><small>{item.change}</small><strong>{item.hint}</strong></summary>
    <p>{item.explanation}</p><a href={item.url} target="_blank" rel="noreferrer">指标资料 ↗</a>
  </details>)}</div>
}
