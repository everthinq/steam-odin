# Heimdall frontend

The steam-odin web app: React 19, react-router-dom 7, Vite 7, Tailwind CSS 4,
`lucide-react` icons.

- In Docker (the normal way) it runs the Vite dev server with hot reload,
  published at http://localhost:3000; `/api` is proxied to the backend.
- `npm run lint` and `npm run build` are the checks (there are no frontend
  tests).
- Screens and how to use them: [the user guide](../../../docs/guide/README.md).
- Routes, conventions, lint rules and known gaps: [CLAUDE.md](CLAUDE.md).
