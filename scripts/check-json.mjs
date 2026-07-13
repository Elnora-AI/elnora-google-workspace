#!/usr/bin/env node
// Validate the plugin manifests parse and carry the required fields, and that
// no per-user credential or token file leaked into the repo.

import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const ROOT = process.cwd();
const errors = [];

function loadJson(rel) {
  const path = join(ROOT, rel);
  if (!existsSync(path)) {
    errors.push(`${rel}: missing`);
    return null;
  }
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (err) {
    errors.push(`${rel}: invalid JSON (${err.message})`);
    return null;
  }
}

const plugin = loadJson(".claude-plugin/plugin.json");
if (plugin) {
  for (const key of ["name", "version", "description", "author"]) {
    if (!plugin[key]) errors.push(`.claude-plugin/plugin.json: missing "${key}"`);
  }
  if (plugin.name !== "google-workspace") {
    errors.push(`.claude-plugin/plugin.json: name must be "google-workspace"`);
  }
}

const market = loadJson(".claude-plugin/marketplace.json");
if (market) {
  if (!Array.isArray(market.plugins) || market.plugins.length === 0) {
    errors.push(".claude-plugin/marketplace.json: plugins[] must be non-empty");
  } else {
    const p = market.plugins[0];
    if (p.name !== "google-workspace") errors.push("marketplace.json: plugin name must be google-workspace");
    if (p.source !== "./") errors.push('marketplace.json: plugin source must be "./"');
  }
}

// Per-user credential / token artifacts must never be committed.
for (const leak of ["accounts.json", "credentials.json", "client_secret.json", ".google-token.json"]) {
  if (existsSync(join(ROOT, leak))) errors.push(`${leak}: per-user credential file must not be committed`);
}

if (errors.length > 0) {
  console.error("Manifest validation failed:\n");
  for (const e of errors) console.error(`  - ${e}`);
  process.exit(1);
}
console.log("check-json: manifests valid, no leaked credentials.");
