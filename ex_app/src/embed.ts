export const views = ['dayGridMonth', 'timeGridWeek', 'timeGridDay', 'listMonth', 'listWeek', 'listDay']
export function embed(link: string, view: string, date = 'now'): string {
  if (!link) return ''
  const url = new URL(link)
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error('Укажите публичную ссылку Calendar')
  const match = url.pathname.match(/^(.*)\/apps\/calendar\/(?:p|embed)\/([A-Za-z0-9]+)(?:\/.*)?$/)
  if (!match || !views.includes(view) || (date !== 'now' && !/^\d{4}-\d{2}-\d{2}$/.test(date))) throw new Error('Проверьте публичную ссылку, режим и дату')
  const target = `${url.origin}${match[1]}/apps/calendar/embed/${match[2]}/${view}/${date}`
  return `<iframe src="${target}" title="Мероприятия" width="100%" height="720" loading="lazy" style="border:0"></iframe>`
}
