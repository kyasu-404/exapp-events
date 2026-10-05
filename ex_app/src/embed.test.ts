import { describe, expect, it } from 'vitest'
import { embed, views } from './embed'
describe('Calendar embed', () => {
  for (const view of views) it(view, () => expect(embed('https://cloud.example/index.php/apps/calendar/p/ABC123', view)).toContain(`/embed/ABC123/${view}/now`))
  it('subdirectory and date', () => expect(embed('https://cloud.example/nc/index.php/apps/calendar/p/ABC', 'listMonth', '2026-10-05')).toContain('/nc/index.php/apps/calendar/embed/ABC/listMonth/2026-10-05'))
  it('rejects markup and invalid schemes', () => {
    for (const input of ['javascript:alert(1)', 'https://cloud.example/apps/calendar/p/abc%22', 'https://user:password@cloud.example/apps/calendar/p/abc']) expect(() => embed(input, 'listMonth')).toThrow()
  })
})
