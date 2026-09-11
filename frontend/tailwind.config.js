/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      // Loosen the default line-height pairing on the sizes used almost
      // everywhere in this app (text-xs/text-sm/text-base). Tailwind's size
      // utilities set line-height directly on the element, so a generic CSS
      // rule (e.g. `body { line-height }`) would get overridden by nearly
      // every text-* class in the codebase — this is the one place that
      // actually reaches them. Elements with an explicit leading-* class
      // (badges, tight labels) are unaffected since that's more specific.
      // Local-only accessibility pass, 2026-09-11.
      fontSize: {
        xs: ["0.75rem", { lineHeight: "1.125rem" }],   // was 1rem
        sm: ["0.875rem", { lineHeight: "1.375rem" }],  // was 1.25rem
        base: ["1rem", { lineHeight: "1.625rem" }],    // was 1.5rem
      },
    },
  },
  plugins: [],
};
