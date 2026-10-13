import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import AccountReview from '../src/components/AccountReview'
import { api } from '../src/api'

vi.mock('../src/api', () => ({ api: vi.fn() }))
vi.mock('../src/components/DeskChart', () => ({ default: () => <div>资产曲线</div> }))
afterEach(() => { cleanup(); vi.restoreAllMocks() })

const review = {
  version: 'rank2-account-observation-v1', definition_sha256: '0123456789abcdef', data_date: '2026-10-12',
  activation_date: '2026-10-06', valid_until: '2026-10-13T20:30:00Z', initial_usd: 45000,
  strategy_return_pct: .51, benchmark_return_pct: -10.36, strategy_drawdown_pct: -.92,
  benchmark_drawdown_pct: -10.36, mean_exposure_pct: 24.9, fees_usd: 11.24,
  curve: [{ date: '2026-10-12', equity_usd: 45230, cash_usd: 33750, benchmark_usd: 40335 }],
  by_symbol: [{ symbol: 'FAS', realized_usd: 0, floating_usd: -20, total_usd: -20, buys: 1, sells: 0 }],
  run: { captured_at_utc: '2026-10-13T17:00:00Z', github_run_url: null },
  audit: { first_data_date: '2026-10-12', recorded_sessions: 1, late_signals: 1, missing_sessions: [], unverified_fill_sessions: 1, confirmed_publications: 0, late_publications: 0,
    records: [{ data_date: '2026-10-12', recorded_at_utc: '2026-10-13T17:00:00Z', signal_before_next_open: false,
      fills_match_prior_plan: null, fills_linked_to_prior_plan: false, fill_count: 1, publication: null,
      next_open_plan: { next_open_date: '2026-10-13', actions: [] } }] },
}

it('labels late and unverified fills explicitly and displays the benchmark', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-13T17:00:00Z'))
  vi.mocked(api).mockResolvedValue(review)
  render(<AccountReview revision={0} accountDate="2026-10-12" />)
  await screen.findByText('40/30/30 持有对照')
  expect(screen.getByText('1 笔，缺少及时计划证明')).toBeTruthy()
  expect(screen.getByText('迟到')).toBeTruthy()
  expect(screen.queryByText(/快照已超过/)).toBeNull()
})

it('warns when a previously successful snapshot has expired or belongs to another account date', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-14T10:00:00Z'))
  vi.mocked(api).mockResolvedValue(review)
  render(<AccountReview revision={0} accountDate="2026-10-13" />)
  await screen.findByText(/快照已超过/)
  expect(screen.getByText(/观察数据与账户日期不同/)).toBeTruthy()
})
