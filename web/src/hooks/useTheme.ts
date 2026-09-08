import { useCallback, useEffect, useState } from 'react';
import { applyTheme, initialTheme, Theme } from '../lib/theme';

/** Current appearance, and a way to switch it.
 *
 * The class is applied in a layout effect rather than after paint so a reload
 * never shows one theme's colours before settling on the other.
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((current) => (current === 'dark' ? 'light' : 'dark'));
  }, []);

  return { theme, setTheme, toggleTheme };
}
