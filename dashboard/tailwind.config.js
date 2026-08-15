/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        'cogrid-bg': '#1a1a2e',
        'cogrid-card': '#16213e',
        'cogrid-accent': '#0f3460',
        'cogrid-pink': '#e94560',
        'cogrid-border': '#1a1a3e',
      },
    },
  },
  plugins: [],
}
