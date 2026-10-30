import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Why the proxy could not reach the backend, for the error the page shows.
const PROXY_FAILURES = {
  ECONNRESET: 'The backend dropped the connection in the middle of this request. It usually restarted (it reloads whenever a backend file changes) or crashed',
  ECONNREFUSED: 'The backend is not accepting connections. It is starting up or stopped',
  ENOTFOUND: 'The backend container is not running',
  EAI_AGAIN: 'The backend container is not running',
  ETIMEDOUT: 'The backend did not answer in time',
}

// When the backend cannot answer, Vite's proxy replies with an empty body, and the page then
// fails with the browser's "Unexpected end of JSON input". Reply with JSON that says what happened.
const answerProxyErrorsWithJson = (proxy) => {
  proxy.on('error', (error, request, response) => {
    if (!response || !('req' in response) || response.headersSent || response.writableEnded) return
    const code = error.code || (error.message === 'socket hang up' ? 'ECONNRESET' : '')
    const reason = PROXY_FAILURES[code] || `The frontend could not reach the backend (${error.message})`
    // A read can simply be repeated; a POST (a purchase, a transfer) may have started before the
    // answer was lost, so it is never "try again" without looking first.
    const advice = ['GET', 'HEAD'].includes(request.method)
      ? 'try again in a few seconds'
      : 'it may have started before the answer was lost: check its log or status before repeating it'
    response.writeHead(502, { 'Content-Type': 'application/json' }).end(JSON.stringify({
      error: `${reason}. Nothing was answered for ${request.method} ${request.url.split('?')[0]}; ${advice}.`,
      unanswered: true,     // not the backend's answer: a page must not read it as a refusal
    }))
  })
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://heimdall-backend:5000',
        changeOrigin: true,
        secure: false,
        configure: answerProxyErrorsWithJson,
      },
    },
    host: true
  },
})
