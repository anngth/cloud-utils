# cloud-utils shell bootstrap: PATH + mac_init/*. (skip this file)
typeset -g CLOUD_UTILS_ROOT="${CLOUD_UTILS_ROOT:-${${(%):-%x}:A:h:h}}"
export CLOUD_UTILS_ROOT
export PATH="$CLOUD_UTILS_ROOT/mac_scripts:$PATH"

for f in "$CLOUD_UTILS_ROOT/mac_init/"^load.zsh(.N); do
  source "$f"
done
