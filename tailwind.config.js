// tailwind.config.js
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html", // Scan all HTML files in the templates directory
    "./main.py"              // In case you generate HTML with Tailwind classes in Python (optional)
  ],
  theme: {
    extend: {
      fontFamily: {
        // Example: Add a custom font family if needed
        // sans: ['Inter', 'sans-serif'],
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'), // Optional: for better default form styling
  ],
}