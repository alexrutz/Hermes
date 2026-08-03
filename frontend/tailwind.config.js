/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Cool, low-chroma greys. Deliberately not slate-900/black — a hint of
        // blue keeps large dark surfaces from looking flat.
        ink: {
          950: '#0a0c10',
          900: '#0e1116',
          850: '#12161d',
          800: '#171c24',
          750: '#1d232c',
          700: '#242b36',
          600: '#333c4a',
          500: '#4a5568',
          400: '#6b7688',
          300: '#939dae',
          200: '#c2c9d4',
          100: '#e4e8ee',
        },
        accent: {
          DEFAULT: '#5b9cf6',
          soft: '#8fbcfa',
          dim: '#2d4a72',
        },
        cache: {
          l1: '#34d399',
          l2: '#60a5fa',
          l3: '#a78bfa',
          none: '#4a5568',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      animation: {
        'fade-in': 'fadeIn 160ms ease-out',
        'slide-up': 'slideUp 200ms cubic-bezier(0.16, 1, 0.3, 1)',
        shimmer: 'shimmer 1.8s ease-in-out infinite',
      },
      keyframes: {
        fadeIn: { from: { opacity: '0' }, to: { opacity: '1' } },
        slideUp: {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: { '0%, 100%': { opacity: '0.35' }, '50%': { opacity: '1' } },
      },
    },
  },
  plugins: [],
}
