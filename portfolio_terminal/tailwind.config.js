import path from "node:path";
import { fileURLToPath } from "node:url";

const configDirectory = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    path.join(configDirectory, "index.html"),
    path.join(configDirectory, "src/**/*.{ts,tsx}"),
  ],
  theme: {
    extend: {
      colors: {
        terminal: {
          bg: "var(--terminal-bg)",
          panel: "var(--terminal-panel)",
          panelAlt: "var(--terminal-panel-alt)",
          selected: "var(--terminal-selected)",
          hover: "var(--terminal-hover)",
          line: "var(--terminal-line)",
          ink: "var(--terminal-ink)",
          muted: "var(--terminal-muted)",
          wait: "var(--terminal-muted)",
          entry: "var(--terminal-entry)",
          watch: "var(--terminal-watch)",
          near: "var(--terminal-near)",
          avoid: "var(--terminal-avoid)"
        }
      }
    },
  },
  plugins: [],
};
