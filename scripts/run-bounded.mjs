import { spawn } from "node:child_process";

function durationToMilliseconds(value, label) {
  const match = /^(\d+)(ms|s|m)$/.exec(value);
  if (!match) throw new Error(`${label} must use ms, s, or m (received ${value})`);
  const amount = Number(match[1]);
  const multiplier = match[2] === "m" ? 60_000 : match[2] === "s" ? 1_000 : 1;
  return amount * multiplier;
}

const [timeoutValue, killAfterValue, command, ...args] = process.argv.slice(2);
if (!timeoutValue || !killAfterValue || !command) {
  console.error("usage: run-bounded.mjs <timeout> <kill-after> <command> [args...]");
  process.exit(64);
}

const timeoutMs = durationToMilliseconds(timeoutValue, "timeout");
const killAfterMs = durationToMilliseconds(killAfterValue, "kill-after");
const child = spawn(command, args, {
  env: process.env,
  stdio: "inherit",
  windowsHide: true,
});

let forcedTimer;
const timeoutTimer = setTimeout(() => {
  console.error(`Command exceeded ${timeoutValue}; requesting termination.`);
  child.kill("SIGTERM");
  forcedTimer = setTimeout(() => child.kill("SIGKILL"), killAfterMs);
}, timeoutMs);

child.once("error", (error) => {
  clearTimeout(timeoutTimer);
  if (forcedTimer) clearTimeout(forcedTimer);
  console.error(error);
  process.exitCode = 69;
});

child.once("exit", (code, signal) => {
  clearTimeout(timeoutTimer);
  if (forcedTimer) clearTimeout(forcedTimer);
  if (signal) {
    console.error(`Command exited after signal ${signal}.`);
    process.exitCode = 124;
    return;
  }
  process.exitCode = code ?? 1;
});
