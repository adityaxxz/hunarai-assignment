import fs from "node:fs";
import path from "node:path";

import { Renderer, marked } from "marked";

import "./markdown.css";

/**
 * Renders `docs/ATTENDANCE_DESIGN.md`, assignment item 3.
 *
 * **Read from disk at build time, not fetched at runtime.** This is a server
 * component with no dynamic data, so Next inlines the rendered HTML into a
 * static page. A reviewer opening the deployed link gets the document instantly
 * with the backend asleep — which on Render's free tier it will be, and a
 * 60-second cold start to read a design note would be absurd.
 *
 * The markdown file itself is the single source of truth and is never edited
 * here. Nothing in this page transforms the content; it only decides how it
 * looks.
 */

// Relative to `frontend/`, which is both the local cwd for `next build` and the
// Vercel Root Directory. One literal path, not a list of candidates to try:
// looping over paths makes the read undecidable for the bundler, which then
// warns that it has to trace the whole project into the output.
const DOC_PATH = path.join(process.cwd(), "..", "docs", "ATTENDANCE_DESIGN.md");

function readDesignDoc(): string {
  try {
    return fs.readFileSync(DOC_PATH, "utf8");
  } catch (cause) {
    // Thrown at build time, so the build fails loudly instead of deploying a
    // page that silently renders nothing. The likely cause is named because it
    // is a Vercel setting rather than anything in this repo: with a Root
    // Directory set, "Include source files outside of the Root Directory in the
    // Build Step" has to be on for `../docs` to exist during the build.
    throw new Error(
      `Could not read ${DOC_PATH}. On Vercel, enable "Include source files ` +
        `outside of the Root Directory in the Build Step" so ../docs is present ` +
        `when the frontend builds.`,
      { cause },
    );
  }
}

/** Wraps every table so it can scroll inside the page rather than widening it. */
function renderer(): Renderer {
  const base = new Renderer();
  const table = base.table.bind(base);
  base.table = (token) => `<div class="table-scroll">${table(token)}</div>`;
  return base;
}

export const metadata = {
  title: "Attendance design — Hunar FDE Assignment",
  description:
    "Tracking daily attendance for 1,000 people across 100 locations where workers have no smartphones.",
};

export default function AttendancePage() {
  const html = marked.parse(readDesignDoc(), {
    gfm: true,
    async: false,
    renderer: renderer(),
  });

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-10">
      {/* No PageContainer here: the document opens with its own `# ` heading,
          and a second title above it would read as a mistake. */}
      <article
        className="markdown"
        // The input is a markdown file committed to this repository and read at
        // build time. It is not user input and never crosses a request boundary,
        // so there is no injection surface to sanitise against — adding a
        // sanitiser here would be ceremony, not safety.
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}
