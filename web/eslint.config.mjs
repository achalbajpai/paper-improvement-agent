import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const config = [
  ...coreWebVitals,
  ...typescript,
  {
    rules: {
      "@typescript-eslint/no-non-null-assertion": "error",
      "@typescript-eslint/no-explicit-any": "error",

      "react/no-danger": "error",
    },
  },
  { ignores: [".next/**", "node_modules/**", "lib/api/schema.d.ts"] },
];

export default config;
