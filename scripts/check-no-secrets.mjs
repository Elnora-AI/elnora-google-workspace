#!/usr/bin/env node
// Guard: fail if a real credential or a personal/company identity leaks into the
// repo. This plugin must be 100% universal. It complements (does not replace)
// the full-history gitleaks scan in CI.
//
// What it enforces:
//   * Personal/company identity — a base64-obfuscated surname, a personal Slack
//     id shape, personal booking links, and any @elnora.ai address other than the
//     publisher addresses opensource@/security@. Runs over EVERY tracked file.
//   * Credential shapes — AWS/OpenAI/Google/GitHub/private-key patterns. Runs over
//     every file EXCEPT tests/, whose fixtures deliberately contain SYNTHETIC
//     credential-shaped strings to exercise the output scrubber (real-secret
//     detection for those paths is gitleaks' job).
//
// The bare org/repo name ("Elnora AI", "elnora-google-workspace") and the
// opensource@/security@ addresses are legitimate publisher metadata, not leaks.
// `.google-token*.json` is the product's own (gitignored) token-file glob and is
// NOT forbidden — it appears legitimately in code, docs, and .gitignore.
//
// An optional, gitignored denylist (scripts/.no-secrets-denylist.txt — one
// term/regex per line) is also applied when present, so maintainers can scan for
// org-specific terms locally without committing them.
//
// Run:  node scripts/check-no-secrets.mjs   (non-zero exit lists every violation)

import { execSync } from "node:child_process";
import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import { join, relative, basename } from "node:path";

const ROOT = process.cwd();

const SKIP_DIRS = new Set([
  ".git", "node_modules", "dist", "build", "coverage",
  "__pycache__", ".venv", ".ruff_cache", ".pytest_cache",
]);

const TEXT_EXT = new Set([
  ".md", ".mjs", ".js", ".ts", ".json", ".py", ".yml", ".yaml",
  ".txt", ".toml", ".sh", ".ps1", ".cmd", ".cfg", ".ini", ".html", ".css", "",
]);

// This guard necessarily contains the patterns it searches for, and the
// gitignored denylist holds raw forbidden terms — skip both.
const SKIP_FILES = new Set([
  "scripts/check-no-secrets.mjs",
  "scripts/.no-secrets-denylist.txt",
]);

// Emails allowed to appear verbatim. Any other address on a company domain is a
// violation (see COMPANY_EMAIL).
const ALLOWED_EMAIL = /^(opensource@elnora\.ai|security@elnora\.ai|noreply@anthropic\.com)$/i;
// Company-domain email that is a real leak unless allow-listed above.
const COMPANY_EMAIL = /\b[\w.+-]+@(?:[\w-]+\.)*elnora\.ai\b/gi;
// Placeholder home-directory segments that are fine in examples.
const PLACEHOLDER_HOME = /^(yourname|username|user|you|name|jane|janedoe|home|me)$/i;

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Built-in forbidden identity terms, base64-encoded so this guard file never
// contains the plaintext terms it forbids (a personal surname and a personal
// booking-link domain), which would otherwise be leaks in their own right.
const IDENTITY_B64 = ["a2l2aXNpbGQ=", "Y2FsZW5kYXIuYXBwLmdvb2dsZQ=="];
const IDENTITY_TERMS = IDENTITY_B64.map((s) => Buffer.from(s, "base64").toString("utf8"));

// Identity / structural red flags — checked over EVERY file.
const IDENTITY_BANNED = [
  { name: "absolute macOS home path", re: /\/Users\/([a-z0-9._-]+)\//i, homeGroup: 1 },
  { name: "absolute Linux home path", re: /\/home\/([a-z0-9._-]+)\//i, homeGroup: 1 },
  { name: "personal cloud-storage mount", re: /(GoogleDrive-|CloudStorage\/)[\w.+-]*@/i },
  { name: "personal Slack user id", re: /\bU0[A-Z0-9]{7,}\b/ },
  ...IDENTITY_TERMS.map((t) => ({ name: "forbidden identity", re: new RegExp(escapeRegex(t), "i") })),
];

// Credential shapes — checked over every file EXCEPT tests/ (synthetic fixtures).
const SECRET_BANNED = [
  { name: "AWS access key id", re: /\bAKIA[0-9A-Z]{16}\b/ },
  { name: "OpenAI/Anthropic-style key", re: /\b(sk-ant-|sk-)[A-Za-z0-9_-]{20,}/ },
  { name: "Google API key", re: /\bAIza[0-9A-Za-z_-]{35}\b/ },
  { name: "Google OAuth client secret", re: /\bGOCSPX-[A-Za-z0-9_-]{10,}/ },
  { name: "Google OAuth access token", re: /\bya29\.[A-Za-z0-9._-]{20,}/ },
  { name: "GitHub token", re: /\bgh[pousr]_[A-Za-z0-9]{20,}\b/ },
  { name: "private key block", re: /-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----/ },
];

function loadExtraDenylist() {
  const path = join(ROOT, "scripts", ".no-secrets-denylist.txt");
  if (!existsSync(path)) return [];
  const out = [];
  for (const raw of readFileSync(path, "utf8").split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    try {
      out.push({ name: `denylist: ${line}`, re: new RegExp(line, "i") });
    } catch {
      out.push({ name: `denylist: ${line}`, re: new RegExp(escapeRegex(line), "i") });
    }
  }
  return out;
}

function walk(dir, out) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (!SKIP_DIRS.has(entry)) walk(full, out);
    } else {
      out.push(full);
    }
  }
}

function hasTextExt(path) {
  const b = basename(path);
  const dot = b.lastIndexOf(".");
  return TEXT_EXT.has(dot === -1 ? "" : b.slice(dot).toLowerCase());
}

function listGitFiles() {
  try {
    const out = execSync("git ls-files -z", { cwd: ROOT, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
    return out.split("\0").filter(Boolean).map((p) => join(ROOT, p));
  } catch {
    return null;
  }
}

const extra = loadExtraDenylist();
const files = listGitFiles() ?? (() => { const o = []; walk(ROOT, o); return o; })();
const violations = [];

for (const path of files) {
  if (!hasTextExt(path)) continue;
  const rel = relative(ROOT, path).split("\\").join("/");
  if (SKIP_FILES.has(rel)) continue;
  const isTest = rel.startsWith("tests/") || rel.includes("/tests/") || /(^|\/)test_[^/]*\.py$/.test(rel);
  let content;
  try {
    content = readFileSync(path, "utf8");
  } catch {
    continue;
  }
  const lines = content.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    for (const b of IDENTITY_BANNED) {
      const m = b.re.exec(line);
      if (!m) continue;
      if (b.homeGroup && PLACEHOLDER_HOME.test(m[b.homeGroup])) continue;
      violations.push(`${rel}:${i + 1}  [${b.name}]  ${line.trim().slice(0, 120)}`);
    }

    if (!isTest) {
      for (const b of SECRET_BANNED) {
        if (b.re.test(line)) violations.push(`${rel}:${i + 1}  [${b.name}]  ${line.trim().slice(0, 120)}`);
      }
    }

    for (const d of extra) {
      if (d.re.test(line)) violations.push(`${rel}:${i + 1}  [${d.name}]  ${line.trim().slice(0, 120)}`);
    }

    // Company-domain emails (any @elnora.ai not opensource@/security@).
    for (const em of line.match(COMPANY_EMAIL) || []) {
      if (ALLOWED_EMAIL.test(em)) continue;
      violations.push(`${rel}:${i + 1}  [company-domain email]  ${em}`);
    }
  }
}

if (violations.length > 0) {
  console.error(`Found ${violations.length} disallowed reference(s). This plugin must be 100% universal.\n`);
  for (const v of violations) console.error(`  - ${v}`);
  console.error(
    `\nUse placeholder examples (Acme / Globex / Jane Doe / example.com). "Elnora AI" may appear only as ` +
      `publisher metadata (LICENSE, SECURITY contact, marketplace owner, plugin author, repo URL), and the ` +
      `only allowed company emails are opensource@elnora.ai and security@elnora.ai.`,
  );
  process.exit(1);
}

console.log(`check-no-secrets: scanned ${files.length} files. No disallowed references found.`);
