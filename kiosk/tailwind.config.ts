import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#070b14',
          900: '#0b1120',
          800: '#131c33',
          700: '#1d2a4a',
        },
        cop: {
          blue: '#608bfa',
          teal: '#5fd6bf',
          amber: '#ffb454',
          red: '#ff6b6b',
        },
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      keyframes: {
        kenburns: {
          from: { transform: 'scale(1.02)' },
          to: { transform: 'scale(1.14) translate(-2%, -1.5%)' },
        },
        caret: {
          '0%, 49%': { opacity: '1' },
          '50%, 100%': { opacity: '0' },
        },
        pulsebadge: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.45' },
        },
        fadeup: {
          from: { opacity: '0', transform: 'translateY(10px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        caret: 'caret 1s step-end infinite',
        pulsebadge: 'pulsebadge 1.6s ease-in-out infinite',
        fadeup: 'fadeup 420ms ease-out both',
      },
    },
  },
  plugins: [],
} satisfies Config;
