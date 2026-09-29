import { useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

const KEY = 'aae-theme'

/**
 * Light or dark, remembered.
 *
 * Defaults to light rather than to the system preference: this is a
 * document about numbers and it should read like one by default. A visitor
 * who wants dark says so once and is not asked again.
 */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = typeof localStorage !== 'undefined' ? localStorage.getItem(KEY) : null
    return stored === 'dark' ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem(KEY, theme)
    } catch {
      // Private browsing. The theme still applies for this page.
    }
  }, [theme])

  return [theme, () => setTheme((t) => (t === 'light' ? 'dark' : 'light'))]
}

export function ThemeToggle({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  const next = theme === 'light' ? 'dark' : 'light'
  return (
    <button
      className="btn ghost small theme-toggle"
      onClick={onToggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
    >
      {theme === 'light' ? '◑' : '◐'} {next === 'dark' ? 'Dark' : 'Light'}
    </button>
  )
}
