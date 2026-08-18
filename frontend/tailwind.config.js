import typography from "@tailwindcss/typography";

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "-apple-system", "system-ui", "sans-serif"],
        display: ["Poppins", "Inter", "-apple-system", "sans-serif"],
      },
      colors: {
        // Paleta tomada de beacon.tv (variables --c-primary1..12 de su CSS).
        // 1 = más oscuro, 12 = más claro.
        beacon: {
          1: "#0E0616",
          2: "#12081E",
          3: "#190D25",
          4: "#28153B",
          5: "#3A2252",
          6: "#472C63",
          7: "#4D3169",
          8: "#5A3A7A",
          9: "#73499D",
          10: "#BF93EB",
          11: "#E1C3FF",
          12: "#F7EFFF",
        },
      },
    },
  },
  plugins: [typography],
};
