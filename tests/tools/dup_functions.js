// Fails when a script declares the same function name twice. Inside one scope the later
// declaration silently replaces the earlier one everywhere (that's how the Security card once
// showed "updated 20785 d ago"). Usage: node tests/tools/dup_functions.js static/js/*.js
const fs = require("fs");
let bad = 0;
for (const file of process.argv.slice(2)) {
  const seen = new Map();
  fs.readFileSync(file, "utf8").split("\n").forEach((line, i) => {
    const m = line.match(/^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(/);
    if (!m) return;
    if (seen.has(m[1])) { console.error(`${file}:${i + 1}: function ${m[1]} is already declared on line ${seen.get(m[1])}`); bad++; }
    else seen.set(m[1], i + 1);
  });
}
process.exit(bad ? 1 : 0);
