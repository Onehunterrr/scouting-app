#!/usr/bin/env node
// Context gate for the context-handoff skill.
// Runs as a PostToolUse hook. Reads the session transcript, estimates how many
// tokens are currently in the context window, and — when that crosses the
// handoff threshold — injects a "CONTEXT GATE" reminder back into the model so
// the context-handoff skill fires at the next gate/step boundary.
//
// Thresholds (see .claude/skills or ~/.claude/skills/context-handoff/SKILL.md):
//   < 150k tokens  -> silent, keep working
//   >= 150k tokens -> emit CONTEXT GATE reminder (handoff window is 150k-200k)

import { readFileSync } from 'node:fs';

const GATE_TOKENS = 150_000;

function readStdin() {
  try {
    return readFileSync(0, 'utf8');
  } catch {
    return '';
  }
}

// Best estimate of the prompt size for the most recent turn:
// input + cache-create + cache-read (output tokens are not part of next context).
function usageToTokens(u) {
  if (!u) return null;
  const input = u.input_tokens ?? 0;
  const cacheCreate = u.cache_creation_input_tokens ?? 0;
  const cacheRead = u.cache_read_input_tokens ?? 0;
  const total = input + cacheCreate + cacheRead;
  return total > 0 ? total : null;
}

function latestContextTokens(transcriptPath) {
  let raw;
  try {
    raw = readFileSync(transcriptPath, 'utf8');
  } catch {
    return null;
  }
  const lines = raw.split('\n');
  // Walk backwards to the most recent assistant message carrying usage data.
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line) continue;
    let obj;
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }
    const tokens = usageToTokens(obj?.message?.usage);
    if (tokens != null) return tokens;
  }
  return null;
}

function main() {
  let payload;
  try {
    payload = JSON.parse(readStdin() || '{}');
  } catch {
    process.exit(0); // Malformed input: never block the tool.
  }

  const transcriptPath = payload.transcript_path;
  if (!transcriptPath) process.exit(0);

  const tokens = latestContextTokens(transcriptPath);
  if (tokens == null || tokens < GATE_TOKENS) process.exit(0);

  const k = Math.round(tokens / 1000);
  const additionalContext =
    `CONTEXT GATE: ~${k}k tokens in context (threshold ${GATE_TOKENS / 1000}k). ` +
    `Per the context-handoff skill: stop starting new work — even mid-phase. ` +
    `Finish only the atomic action in flight, then write/refresh handoff.md at ` +
    `the repo root, commit it ("[handoff]: checkpoint at ~${k}k tokens — <state>"), ` +
    `and tell the user to run /clear and resume with "continue from handoff.md".`;

  process.stdout.write(
    JSON.stringify({
      hookSpecificOutput: {
        hookEventName: 'PostToolUse',
        additionalContext,
      },
      suppressOutput: true,
    })
  );
  process.exit(0);
}

main();
