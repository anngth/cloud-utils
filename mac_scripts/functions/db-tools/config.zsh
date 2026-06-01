# =============================================================================
# Database Tool - Unified Database Connection & Sync Manager
# =============================================================================

# Global variables
typeset -g SSH_TIMEOUT=10
typeset -g DBT_SECRETS_LOADED="false"
typeset -g DBT_SECRETS_FILE=""
typeset -g DBT_SECRETS_MTIME=""

local _dbt_func_dir="$(cd "$(dirname "${(%):-%x}")" && pwd)"
local _dbt_mac_scripts_dir="${_dbt_func_dir:h:h}"
source "${_dbt_mac_scripts_dir}/lib/cloud-utils-config.zsh"

_dbt_repo_root() {
  local current_dir="$1" root_dir=""

  while [[ "$current_dir" != "/" ]]; do
    if [[ -f "$current_dir/mac_scripts/functions/db-tools/secrets.example" ]]; then
      root_dir="$current_dir"
      break
    fi
    current_dir="${current_dir:h}"
  done

  [[ -n "$root_dir" ]] && print -r -- "$root_dir"
}

_dbt_ensure_secrets_file() {
  local secrets_file example="$_dbt_func_dir/secrets.example"

  secrets_file="$(cloud_utils_dbt_secrets_file)" || return 1
  [[ -f "$secrets_file" ]] && return 0

  local icloud_legacy="${HOME}/Library/Mobile Documents/com~apple~CloudDocs/Backups/dbt_secrets"
  cloud_utils_bootstrap_file "$secrets_file" "$example" "$icloud_legacy" && return 0

  local repo_root repo_legacy
  repo_root="$(_dbt_repo_root "$_dbt_func_dir")"
  if [[ -n "$repo_root" ]]; then
    repo_legacy="$repo_root/dbt_secrets"
    cloud_utils_bootstrap_file "$secrets_file" "$example" "$repo_legacy" && return 0
  fi

  cloud_utils_bootstrap_file "$secrets_file" "$example" "" && return 0
  return 1
}

# Resolve path to dbt/secrets under CLOUD_UTILS_CONFIG_DIR (optional DBT_SECRETS_FILE override).
find_secrets_file() {
  if [[ -n "$DBT_SECRETS_FILE" && -f "$DBT_SECRETS_FILE" ]]; then
    print -r -- "$DBT_SECRETS_FILE"
    return 0
  fi

  _dbt_ensure_secrets_file || return 1

  local secrets_file
  secrets_file="$(cloud_utils_dbt_secrets_file)" || return 1
  [[ -f "$secrets_file" ]] || return 1
  print -r -- "$secrets_file"
  return 0
}

_dbt_secrets_help_path() {
  local config_dir
  config_dir="$(cloud_utils_config_dir 2>/dev/null)" || config_dir="$(cloud_utils_default_config_dir)"
  print -r -- "$config_dir/dbt/secrets"
}

# Auto-load VPS configurations from secrets file
# This ensures we always use the latest configurations
{
  local secrets_file
  if secrets_file=$(find_secrets_file 2>/dev/null); then
    {
      source "$secrets_file" 2>/dev/null
    } >/dev/null 2>&1

    if [[ -z "${VPS_CONFIGS[@]}" ]]; then
      echo "⚠️  Warning: VPS_CONFIGS not found in secrets file"
      echo "   Please define VPS_CONFIGS array in $secrets_file"
      typeset -a VPS_CONFIGS=()
    fi
  else
    echo "⚠️  Warning: Secrets file not found"
    echo "   Please create $(_dbt_secrets_help_path) (see secrets.example)"
    typeset -a VPS_CONFIGS=()
  fi
}

# =============================================================================
# Shared Utility Functions
# =============================================================================

# Get file modification time (epoch seconds)
_get_file_mtime() {
  local file_path="$1"
  stat -f "%m" "$file_path" 2>/dev/null
}

# Load secrets from secrets file
load_secrets() {
  local secrets_file
  if ! secrets_file=$(find_secrets_file 2>/dev/null); then
    echo "⚠️  Warning: Secrets file not found"
    echo "   Please create $(_dbt_secrets_help_path) (see secrets.example)"
    export VPS_CONFIGS=()
    export POSTGRES_SOURCES=()
    export POSTGRES_TARGETS=()
    export MONGO_SOURCES=()
    export MONGO_TARGETS=()
    DBT_SECRETS_LOADED="false"
    DBT_SECRETS_FILE=""
    DBT_SECRETS_MTIME=""
    return 1
  fi

  local secrets_mtime
  secrets_mtime=$(_get_file_mtime "$secrets_file")
  if [[ "$DBT_SECRETS_LOADED" == "true" ]] && \
     [[ "$DBT_SECRETS_FILE" == "$secrets_file" ]] && \
     [[ -n "$secrets_mtime" ]] && \
     [[ "$DBT_SECRETS_MTIME" == "$secrets_mtime" ]]; then
    return 0
  fi

  {
    source "$secrets_file" 2>/dev/null
  } >/dev/null 2>&1

  if [[ -n "${VPS_CONFIGS[@]}" ]]; then
    export VPS_CONFIGS=("${VPS_CONFIGS[@]}")
  else
    export VPS_CONFIGS=()
  fi

  if [[ -n "${POSTGRES_SOURCES[@]}" ]]; then
    export POSTGRES_SOURCES=("${POSTGRES_SOURCES[@]}")
  else
    export POSTGRES_SOURCES=()
  fi

  if [[ -n "${POSTGRES_TARGETS[@]}" ]]; then
    export POSTGRES_TARGETS=("${POSTGRES_TARGETS[@]}")
  else
    export POSTGRES_TARGETS=()
  fi

  if [[ -n "${MONGO_SOURCES[@]}" ]]; then
    export MONGO_SOURCES=("${MONGO_SOURCES[@]}")
  else
    export MONGO_SOURCES=()
  fi

  if [[ -n "${MONGO_TARGETS[@]}" ]]; then
    export MONGO_TARGETS=("${MONGO_TARGETS[@]}")
  else
    export MONGO_TARGETS=()
  fi

  if [[ ${#VPS_CONFIGS[@]} -eq 0 ]] && [[ ${#POSTGRES_SOURCES[@]} -eq 0 ]] && [[ ${#MONGO_SOURCES[@]} -eq 0 ]]; then
    echo "⚠️  Warning: No configurations found in secrets file"
    echo "   Please define VPS_CONFIGS, POSTGRES_SOURCES, and/or MONGO_SOURCES in $secrets_file"
  fi

  DBT_SECRETS_LOADED="true"
  DBT_SECRETS_FILE="$secrets_file"
  DBT_SECRETS_MTIME="$secrets_mtime"
}

# Log message to stdout
log() {
  local level=$1
  shift
  local message="$@"

  if [[ "$level" == "ERROR" ]]; then
    echo "❌ $message" >&2
  elif [[ "$level" == "WARN" ]]; then
    echo "⚠️  $message"
  elif [[ "$level" == "INFO" ]]; then
    echo "ℹ️  $message"
  fi
}
