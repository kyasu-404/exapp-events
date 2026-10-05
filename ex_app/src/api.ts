import { getRequestToken } from '@nextcloud/auth'
import { generateUrl } from '@nextcloud/router'

export const base = () => generateUrl('/apps/app_api/proxy/exapp_events')
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`${base()}${path}`, {
    method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', requesttoken: getRequestToken() ?? '' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.message ?? data.detail ?? `Ошибка ${response.status}`)
  return data
}
