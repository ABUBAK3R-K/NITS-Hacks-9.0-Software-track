// Copies ../contracts/types.ts and ../contracts/fixtures/*.json into lib/contracts/.
// Runs on predev/prebuild. If ../contracts is not visible (e.g. a build that only
// sees frontend/), it keeps the committed copy and exits 0.
import { cpSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "contracts");
const dst = join(here, "..", "lib", "contracts");

if (!existsSync(src)) {
  console.log("[sync-contracts] ../contracts not found, using committed copy");
  process.exit(0);
}
mkdirSync(join(dst, "fixtures"), { recursive: true });
cpSync(join(src, "types.ts"), join(dst, "types.ts"));
for (const f of readdirSync(join(src, "fixtures"))) {
  // the raw event streams are large and only needed by backend tests
  if (f.startsWith("events_")) continue;
  cpSync(join(src, "fixtures", f), join(dst, "fixtures", f));
}
console.log("[sync-contracts] synced types.ts + fixtures");
