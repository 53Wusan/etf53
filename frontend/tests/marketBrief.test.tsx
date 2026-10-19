import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import MarketBrief from '../src/components/MarketBrief'
import { api } from '../src/api'
vi.mock('../src/api', () => ({ api: vi.fn() }))
afterEach(() => { cleanup(); vi.restoreAllMocks() })
const brief = {
  status: 'partial', version: 'market-context-v1', market_date: '2026-10-16', market_ready: true,
  generated_at_utc: '2026-10-19T16:00:00Z', valid_until: '2026-10-19T20:30:00Z',
  assessment: { trend: '震荡分化', short_term: '短期回撤', risk: '偏高', summary: '科技回撤较大。' },
  quotes: [], vix: null, breadth_relative_20d_pp: null, calendar_complete: false,
  news: [{ title: 'Tech stocks fall', title_zh: '科技股下跌', url: 'https://example.com/news', source: 'test', published_at_utc: '2026-10-19T15:00:00Z', tags: ['科技与AI'] }],
  events: [{ date: '2026-10-28', time_utc: null, title: 'FOMC会议结束日', source: '美联储会议', url: 'https://example.com/calendar', timing_note: '仅日期' }],
  sources: [{ name: 'BLS经济数据', kind: 'calendar', ok: false, message: '403', url: 'https://www.bls.gov/', data_date: null }],
}
it('separates the latest close from fresher news and flags partial event coverage', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-19T17:00:00Z'))
  vi.mocked(api).mockResolvedValue(brief)
  render(<MarketBrief revision={0} />)
  await screen.findByText('震荡分化')
  expect(screen.getByText(/收盘判断 2026-10-16/)).toBeTruthy()
  expect(screen.getByText(/部分官方日历暂不可读/)).toBeTruthy()
  expect(screen.getByText('2026-10-28 美东日期')).toBeTruthy()
  expect(screen.getByRole('link', { name: '科技股下跌' }).getAttribute('href')).toBe('https://example.com/news')
})
it('hides all current state conclusions when the close snapshot expires', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-20T10:00:00Z'))
  vi.mocked(api).mockResolvedValue(brief)
  render(<MarketBrief revision={0} />)
  await screen.findByText(/收盘快照已过期/)
  expect(screen.queryByText('震荡分化')).toBeNull()
  expect(screen.queryByText('科技回撤较大。')).toBeNull()
  expect(screen.getAllByText('待更新').length).toBe(2)
})

it('copies dated market and paper account context without presenting intentions as fills', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-19T17:00:00Z'))
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  vi.mocked(api).mockImplementation(async path => path === '/account' ? {
    data_date: '2026-10-16', initial_usd: 45000, equity_usd: 46000, cash_usd: 30000,
    positions: { FAS: { lots: 1, quantity: 15, profit_pct: 2 } }, trades: [],
    next_open_plan: { signal_date: '2026-10-16', next_open_date: '2026-10-19', actions: [{ symbol: 'SOXL', side: 'buy', reason: '恐惧区' }] },
  } : brief)
  render(<MarketBrief revision={0} />)
  fireEvent.click(await screen.findByRole('button', { name: '一键复制晨报' }))
  await waitFor(() => expect(writeText).toHaveBeenCalledOnce())
  const text = writeText.mock.calls[0][0]
  expect(text).toContain('现金 $30000.00')
  expect(text).toContain('累计盈亏 $1000.00')
  expect(text).toContain('账户日期当日模拟成交：无')
  expect(text).toContain('下一开盘计划（未成交）')
  expect(text).toContain('SOXL 拟买入')
  expect(text).toContain('科技股下跌')
  expect(text).toContain('https://example.com/news')
})
it('offers manual copying and marks unavailable account data instead of inventing zeros', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-20T10:00:00Z'))
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
  vi.mocked(api).mockImplementation(async path => { if (path === '/account') throw new Error('offline'); return brief })
  render(<MarketBrief revision={0} />)
  fireEvent.click(await screen.findByRole('button', { name: '一键复制晨报' }))
  const area = await screen.findByRole('textbox', { name: '晨报复制内容' }) as HTMLTextAreaElement
  expect(area.value).toContain('模拟账户：读取失败')
  expect(area.value).toContain('当前判断暂停')
  expect(area.value).not.toContain('现金 $0.00')
})
