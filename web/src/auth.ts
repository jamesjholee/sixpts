// Optional Supabase auth. If VITE_SUPABASE_URL is unset, the app runs single-user with the private key.
import { createClient, type SupabaseClient } from '@supabase/supabase-js'
const url = import.meta.env.VITE_SUPABASE_URL, key = import.meta.env.VITE_SUPABASE_ANON_KEY
export const supabase: SupabaseClient | null = url && key ? createClient(url, key) : null
export async function sessionToken(): Promise<string | null> { if (!supabase) return null; const { data } = await supabase.auth.getSession(); return data.session?.access_token ?? null }
export async function signIn(email: string) { if (!supabase) return; await supabase.auth.signInWithOtp({ email, options: { emailRedirectTo: location.origin } }) }
export async function signOut() { await supabase?.auth.signOut() }
