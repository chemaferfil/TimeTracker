/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/templates/**/*.html",
    "./src/static/**/*.js"
  ],
  theme: {
    extend: {
      colors: {
        'miss-sushi-pink': '#E85D8A',
        'miss-sushi-grey': '#4A4A4A',
      }
    },
  },
  plugins: [],
}
