# User-local config directory for cloud-utils (bud, skm, dbt config files)

cloud_utils_default_config_dir() {
  print -r -- "${HOME}/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils"
}

cloud_utils_config_dir() {
  local dir="${CLOUD_UTILS_CONFIG_DIR:-$(cloud_utils_default_config_dir)}"
  if ! mkdir -p "$dir"; then
    return 1
  fi
  print -r -- "$dir"
}

# cloud_utils_bootstrap_file <dest> <example> [legacy]
# Creates dest from legacy path in repo, else from example, if dest does not exist.
cloud_utils_bootstrap_file() {
  local dest="$1" example="$2" legacy="$3"

  [[ -f "$dest" ]] && return 0

  if ! mkdir -p "${dest:h}"; then
    return 1
  fi

  if [[ -n "$legacy" && -f "$legacy" ]]; then
    cp "$legacy" "$dest"
    print -P "%F{cyan}◇%f Migrated $(basename "$dest") → ${dest}" >&2
    return 0
  fi

  if [[ -f "$example" ]]; then
    cp "$example" "$dest"
    return 0
  fi

  return 1
}

cloud_utils_dbt_secrets_file() {
  local config_dir
  config_dir="$(cloud_utils_config_dir)" || return 1
  print -r -- "$config_dir/dbt/secrets"
}
