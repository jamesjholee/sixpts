// API base URL. Empty in dev (Vite proxies /api to :8000); set VITE_API=https://your-api.onrender.com in production.
export const API = (import.meta.env.VITE_API as string | undefined)?.replace(/\/$/, '') || ''
