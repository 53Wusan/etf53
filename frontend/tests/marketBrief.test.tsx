import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import MarketBrief from '../src/components/MarketBrief'
import { api } from '../src/api'
vi.mock('../src/api', () => ({ api: vi.fn() }))
afterEach(() => { cleanup(); vi.restoreAllMocks() })
const brief = {
  status: 'partial', version: 'market-context-v1', market_date: '2026-10-16', market_ready: true,
  generated_at_utc: '2026-10-19T16:00:00Z', valid_until: '2026-10-19T20:30:00Z',
  assessment: { trend: '震荡分化', short_term: '短期回撤', risk: '偏高', summary: '科技回撤较大。' },
  quotes: [], vix: null, breadth_relative_20d_pp: null, calendar_complete: false,
  news: [{ title: 'Tech stocks fall', url: 'https://example.com/news', source: 'test', published_at_utc: '2026-10-19T15:00:00Z', tags: ['科技与AI'] }],
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
  expect(screen.getByRole('link', { name: 'Tech stocks fall' }).getAttribute('href')).toBe('https://example.com/news')
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
