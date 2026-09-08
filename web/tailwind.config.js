/** @type {import('tailwindcss').Config} */

// Every studio colour resolves through a CSS variable so a single class works
// in both themes: `bg-studio-surface` is white on light and #151922 on dark
// without the component knowing which is active. The channels are stored
// space-separated (see index.css) so <alpha-value> keeps working -- that is
// what lets `bg-studio-card/80` and `border-studio-danger/30` still apply.
const withAlpha = (name) => `rgb(var(--studio-${name}) / <alpha-value>)`;

export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        studio: {
          bg: withAlpha('bg'),
          surface: withAlpha('surface'),
          card: withAlpha('card'),
          border: withAlpha('border'),

          // Text, brightest to most muted.
          fg: withAlpha('fg'),
          fg2: withAlpha('fg2'),
          fg3: withAlpha('fg3'),
          fg4: withAlpha('fg4'),

          accent: withAlpha('accent'),
          danger: withAlpha('danger'),
          warning: withAlpha('warning'),
          success: withAlpha('success'),
          cyan: withAlpha('cyan'),
          violet: withAlpha('violet'),

          'on-accent': withAlpha('on-accent'),
        }
      },
      boxShadow: {
        panel: 'var(--studio-shadow)',
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', 'Menlo', 'Monaco', 'Courier New', 'monospace'],
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
