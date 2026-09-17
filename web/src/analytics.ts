// Vercel Web Analytics (free on Vercel; inert elsewhere). Custom events go through track().
import { inject, track as vtrack } from '@vercel/analytics'
let on = false
export function initAnalytics() { try { inject(); on = true } catch { on = false } }
export function track(name: string, props?: Record<string, string | number | boolean>) { try { if (on) vtrack(name, props) } catch {} }
