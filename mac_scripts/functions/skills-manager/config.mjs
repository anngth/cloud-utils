import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { basename, dirname, join } from "node:path";

const CYAN = "\u001b[36m";
const FG_RESET = "\u001b[39m";

export function defaultConfigDir(env = process.env) {
  return `${env.HOME ?? ""}/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils`;
}

export function bootstrapFile({
  dest,
  example,
  legacy = "",
  stderr = process.stderr,
  fs = { copyFileSync, existsSync, mkdirSync },
}) {
  if (fs.existsSync(dest)) return true;
  fs.mkdirSync(dirname(dest), { recursive: true });

  if (legacy && fs.existsSync(legacy)) {
    fs.copyFileSync(legacy, dest);
    stderr.write(`${CYAN}◇${FG_RESET} Migrated ${basename(dest)} → ${dest}\n`);
    return true;
  }

  if (fs.existsSync(example)) {
    fs.copyFileSync(example, dest);
    return true;
  }

  return false;
}

export function initializeConfig({
  env = process.env,
  managerDir,
  stderr = process.stderr,
  fs = { copyFileSync, existsSync, mkdirSync },
}) {
  const configDir = env.CLOUD_UTILS_CONFIG_DIR || defaultConfigDir(env);
  fs.mkdirSync(configDir, { recursive: true });
  const skillsFile = join(configDir, "skm/list.json");
  const example = join(managerDir, "list.json.example");

  try {
    bootstrapFile({
      dest: skillsFile,
      example,
      legacy: join(managerDir, "list.json"),
      stderr,
      fs,
    });
  } catch {}

  if (!fs.existsSync(skillsFile)) {
    try {
      bootstrapFile({
        dest: skillsFile,
        example,
        legacy: join(configDir, "skills/list.json"),
        stderr,
        fs,
      });
    } catch {}
  }

  return { configDir, skillsFile };
}
