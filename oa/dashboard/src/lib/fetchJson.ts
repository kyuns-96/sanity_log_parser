export type ResponseFormat = boolean | 'blob'

export interface FetchJsonOptions<E extends Error> {
  body?: unknown
  rawBody?: boolean
  headers?: Record<string, string>
  signal?: AbortSignal
  redirect?: RequestRedirect
  plain?: ResponseFormat
  createError: (code: string, status: number) => E
  networkErrorCode?: string
  missingErrorCode?: (status: number) => string
  nonJsonErrorCode?: (status: number) => string
}

export async function parseApiResponse<T, E extends Error>(
  response: Response,
  options: Pick<FetchJsonOptions<E>, 'plain' | 'createError' | 'signal' | 'missingErrorCode' | 'nonJsonErrorCode'>,
): Promise<T> {
  const { plain = false, createError, signal } = options
  if (!response.ok) {
    let parsed: unknown
    let isJson = false
    try {
      parsed = await response.json()
      isJson = true
    } catch (error) {
      signal?.throwIfAborted()
      if (error instanceof DOMException && error.name === 'AbortError') throw error
    }
    signal?.throwIfAborted()
    if (!isJson) {
      const code = options.nonJsonErrorCode ? options.nonJsonErrorCode(response.status) : `HTTP_${response.status}`
      throw createError(code, response.status)
    }
    const rawCode = parsed && typeof parsed === 'object' && 'code' in parsed && typeof parsed.code === 'string'
      ? parsed.code.slice(0, 100)
      : ''
    const code = rawCode || (options.missingErrorCode ? options.missingErrorCode(response.status) : `HTTP_${response.status}`)
    throw createError(code, response.status)
  }
  if (plain === 'blob') return await response.blob() as T
  if (plain) return await response.text() as T
  let value: unknown
  try {
    value = await response.json()
  } catch (error) {
    signal?.throwIfAborted()
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw createError('INVALID_RESPONSE', response.status)
  }
  signal?.throwIfAborted()
  return value as T
}

export async function fetchJson<T, E extends Error>(url: string, options: FetchJsonOptions<E>): Promise<T> {
  const hasBody = options.body !== undefined
  const headers: Record<string, string> = {
    ...(hasBody ? { 'Content-Type': 'application/json' } : {}),
    ...(options.headers || {}),
  }
  let response: Response
  try {
    response = await fetch(url, {
      method: hasBody ? 'POST' : 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      ...(options.redirect ? { redirect: options.redirect } : {}),
      ...(Object.keys(headers).length ? { headers } : {}),
      ...(hasBody
        ? { body: options.rawBody && typeof options.body === 'string' ? options.body : JSON.stringify(options.body) }
        : {}),
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    if (options.networkErrorCode) throw options.createError(options.networkErrorCode, 0)
    throw error
  }
  return parseApiResponse<T, E>(response, options)
}
