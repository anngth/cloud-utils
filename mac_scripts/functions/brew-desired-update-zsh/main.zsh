
budz() {
  emulate -L zsh
  
  # Ensure Homebrew
  if ! command -v brew >/dev/null 2>&1; then
    if [ -x /opt/homebrew/bin/brew ]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -x /usr/local/bin/brew ]; then
      eval "$(/usr/local/bin/brew shellenv)"
    else
      echo "❌ Homebrew not found."; return 1
    fi
  fi
  
  local func_dir="$(cd "$(dirname "${(%):-%x}")" && pwd)"
  local mac_scripts_dir="${func_dir:h:h}"
  source "${mac_scripts_dir}/lib/cloud-utils-config.zsh"

  local config_dir
  config_dir="$(cloud_utils_config_dir)" || {
    echo "❌ Could not create config directory."
    return 1
  }

  local desired_file="$config_dir/bud/desired.json"
  local example_file="$mac_scripts_dir/functions/brew-desired-update-js/desired.json.example"
  local json_py="$func_dir/desired-json.py"

  if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ python3 is required to read desired.json"
    return 1
  fi

  local bootstrapped=0
  if [[ ! -f "$desired_file" ]]; then
    if [[ ! -f "$example_file" ]]; then
      echo "❌ Example not found: $example_file"
      return 1
    fi
    mkdir -p "${desired_file:h}" || return 1
    cp "$example_file" "$desired_file" || return 1
    bootstrapped=1
    print -P "%F{cyan}◇%f Created desired.json from example → ${desired_file}" >&2
  fi

  local -a desired=() desired_formulas=() desired_taps=()

  _bud_desired_get() {
    python3 "$json_py" get "$desired_file" "$1"
  }

  _bud_desired_write() {
    {
      print -r -- "--formulas--"
      (( ${#desired_formulas[@]} )) && print -l -- "${desired_formulas[@]}"
      print -r -- "--casks--"
      (( ${#desired[@]} )) && print -l -- "${desired[@]}"
      print -r -- "--taps--"
      (( ${#desired_taps[@]} )) && print -l -- "${desired_taps[@]}"
    } | python3 "$json_py" write "$desired_file"
  }

  _bud_load_desired_arrays() {
    local raw
    raw="$(_bud_desired_get formulas)" || return 1
    desired_formulas=()
    [[ -n "$raw" ]] && desired_formulas=("${(@f)raw}")
    raw="$(_bud_desired_get casks)" || return 1
    desired=()
    [[ -n "$raw" ]] && desired=("${(@f)raw}")
    raw="$(_bud_desired_get taps)" || return 1
    desired_taps=()
    [[ -n "$raw" ]] && desired_taps=("${(@f)raw}")
    return 0
  }

  if ! _bud_load_desired_arrays; then
    echo "❌ Invalid desired.json: $desired_file"
    return 1
  fi

  if (( bootstrapped && ${#desired_taps[@]} == 0 )); then
    desired_taps=("${(@f)$(brew tap 2>/dev/null)}")
    desired_taps=("${desired_taps[@]:#}")
    _bud_desired_write || return 1
    _bud_load_desired_arrays || return 1
  fi

  _bud_title() {
    local label="${1:-BREW DESIRED UPDATE}"
    echo
    print -P "  %K{cyan}%F{black} ${label} %f%k"
    echo
  }

  typeset -g _bud_current_step=""
  typeset -g _bud_begin_dirty=0

  _bud_finish_text() {
    local text="$1"
    case "$text" in
      "Loading Homebrew state") print -r -- "Loaded Homebrew state" ;;
      "Updating Homebrew") print -r -- "Updated Homebrew" ;;
      "Upgrading formulae") print -r -- "Upgraded formulae" ;;
      "Repairing taps") print -r -- "Repaired taps" ;;
      "Reloading Homebrew state") print -r -- "Reloaded Homebrew state" ;;
      "Cleaning up Homebrew") print -r -- "Cleaned up Homebrew" ;;
      "Adding required tap:"*) print -r -- "Added required tap:${text#Adding required tap:}" ;;
      "Ensuring taps ·"*) print -r -- "Ensured taps ·${text#Ensuring taps ·}" ;;
      "Upgrading casks ·"*) print -r -- "Upgraded casks ·${text#Upgrading casks ·}" ;;
      *) print -r -- "$text" ;;
    esac
  }

  _bud_begin() {
    _bud_current_step="$1"
    _bud_begin_dirty=0
    echo
    print -P "%F{green}◇%f $1"
  }

  _bud_done() {
    local text="$1"
    if [[ -z "$text" && -n "$_bud_current_step" ]]; then
      text="$(_bud_finish_text "$_bud_current_step")"
    elif [[ -z "$text" ]]; then
      text=""
    fi
    [[ -z "$text" ]] && return 0

    if [[ -t 1 && -n "$_bud_current_step" && $_bud_begin_dirty -eq 0 ]]; then
      printf '\033[1A\033[2K'
      print -P "%F{green}◆ $text%f"
      printf '\033[2A\033[1M\033[1B'
      _bud_current_step=""
      return
    fi

    print -P "%F{green}◆ $text%f"
    _bud_current_step=""
  }

  _bud_info() {
    print -P "%F{240}$1%f"
  }

  _bud_command() {
    local line="$1"
    if [[ "$line" == \$* ]]; then
      print -P "%F{green}\$%f${line#\$}"
    else
      print -r -- "$line"
    fi
  }

  _bud_format_brew_command() {
    local -a quoted=()
    local arg q
    for arg in "$@"; do
      if [[ -z "$arg" ]]; then
        quoted+=("''")
      elif [[ "$arg" == [[:alnum:]_./:=+-]# ]]; then
        quoted+=("$arg")
      else
        q=${arg//\'/\'\\\'\'}
        quoted+=("'$q'")
      fi
    done
    print -r -- "\$ brew ${quoted[*]}"
  }

  _bud_log_brew() {
    _bud_begin_dirty=1
    _bud_command "$(_bud_format_brew_command "$@")"
  }

  _bud_brew() {
    _bud_log_brew "$@"
    command brew "$@"
  }

  _bud_trust_tap() {
    local tap="$1"
    _bud_log_brew trust --help
    command brew trust --help >/dev/null 2>&1 || return 0
    _bud_brew trust --tap "$tap" || return 1
  }

  _bud_ensure_desired_taps() {
    local tap
    local -A has_tap

    for tap in "${_bud_brew_taps[@]}"; do has_tap[$tap]=1; done
    for tap in "${desired_taps[@]}"; do
      if [[ -z ${has_tap[$tap]} ]]; then
        _bud_trust_tap "$tap" || return 1
        _bud_brew tap "$tap" || return 1
      fi
    done
  }

  _bud_list_col_width() {
    local term_width cols_tty cols_out

    if [[ -n "$COLUMNS" && "$COLUMNS" =~ ^[0-9]+$ && "$COLUMNS" -ge 20 ]]; then
      term_width=$COLUMNS
    fi
    if [[ -z "$term_width" && -t 1 ]]; then
      cols_out=$(stty size 2>/dev/null | awk '{print $2}')
      [[ -n "$cols_out" && "$cols_out" =~ ^[0-9]+$ ]] && term_width=$cols_out
    fi
    if [[ -z "$term_width" && -r /dev/tty ]]; then
      cols_tty=$(stty size </dev/tty 2>/dev/null | awk '{print $2}')
      [[ -n "$cols_tty" && "$cols_tty" =~ ^[0-9]+$ ]] && term_width=$cols_tty
    fi
    [[ -z "$term_width" ]] && term_width=$(tput cols 2>/dev/null)
    [[ -z "$term_width" || "$term_width" -lt 20 ]] && term_width=120

    # 2 spaces for "  " item indent
    echo $((term_width - 2))
  }

  # Column count from terminal width and longest name (content-sized cells, not fixed 3).
  _bud_init_grid_layout() {
    local usable="$1"
    shift
    local -a all_items=("$@")
    local max_len=1 item gap=2

    for item in "${all_items[@]}"; do
      (( ${#item} > max_len )) && max_len=${#item}
    done
    (( max_len < 1 )) && max_len=1

    if (( max_len > usable )); then
      _bud_grid_cols=1
      _bud_grid_cell=$usable
      return 0
    fi

    _bud_grid_cell=$max_len
    _bud_grid_cols=$(( (usable + gap) / (max_len + gap) ))
    (( _bud_grid_cols < 1 )) && _bud_grid_cols=1
  }

  _bud_fit_cell() {
    local text="$1"

    if (( ${#text} > _bud_grid_cell )); then
      printf "%-${_bud_grid_cell}.${_bud_grid_cell}s" "$text"
    else
      printf "%-${_bud_grid_cell}s" "$text"
    fi
  }

  # Fill columns top-to-bottom, then left-to-right (column-major order).
  _bud_format_grid() {
    local -a items=("$@")
    local n=${#items[@]} cols=$_bud_grid_cols nrow row col parts line empty
    local bi=-1 last_col=-1

    (( n && ${+_bud_grid_cols} )) || return 0

    nrow=$(( (n + cols - 1) / cols ))
    empty="$(_bud_fit_cell "")"

    row=0
    while (( row < nrow )); do
      parts=()
      col=0
      last_col=-1
      while (( col < cols )); do
        bi=$(( col * nrow + row + 1 ))
        (( bi <= n )) && last_col=$col
        (( col++ ))
      done
      col=0
      while (( col <= last_col )); do
        bi=$(( col * nrow + row + 1 ))
        if (( bi <= n )); then
          parts+=("$(_bud_fit_cell "${items[bi]}")")
        else
          parts+=("$empty")
        fi
        (( col++ ))
      done
      line="${(pj:  :)parts}"
      print -Pn "%F{default}  "
      print -rn -- "$line"
      print -Pn "%f\n"
      (( row++ ))
    done
  }

  _bud_print_section() {
    local title="$1" accent="$2" col_width="$3"
    shift 3
    local -a items=("$@")
    local count=${#items[@]}

    (( count )) || return 0

    echo ""
    print -Pn "%F{${accent}}%B▸ ${title}%b%f %F{240}(${count})%f\n"

    if (( col_width > 0 && ${+_bud_grid_cols} )); then
      _bud_format_grid "${items[@]}"
    else
      for item in "${items[@]}"; do
        print -P "%F{default}  ${item}%f"
      done
    fi
  }

  # Fetch formulae, casks, taps in parallel (cask list is often slow).
  _bud_load_brew_state() {
    local tmp="${TMPDIR:-/tmp}/bud-brew.$$"
    local f_formulae="$tmp.f" f_casks="$tmp.c" f_taps="$tmp.t"
    local -a raw

    _bud_log_brew list --formula
    _bud_log_brew list --cask --full-name
    _bud_log_brew tap
    command brew list --formula 2>/dev/null >|$f_formulae &
    command brew list --cask --full-name 2>/dev/null >|$f_casks &
    command brew tap 2>/dev/null >|$f_taps &
    wait

    raw=("${(@f)$(<"$f_formulae")}")
    _bud_brew_formulas=()
    for f in "${raw[@]}"; do _bud_brew_formulas+=("${f##*/}"); done

    raw=("${(@f)$(<"$f_casks")}")
    _bud_brew_casks=()
    for c in "${raw[@]}"; do _bud_brew_casks+=("${c##*/}"); done

    _bud_brew_taps=("${(@f)$(<"$f_taps")}")

    rm -f "$f_formulae" "$f_casks" "$f_taps"
  }

  # Status grid for `budz list` / `budz ls`.
  _bud_show_desired_status() {
    local do_load="${1:-1}"
    local -a f_installed=() f_missing=() f_extra=()
    local -a t_installed=() t_missing=() t_extra=()
    local -a c_installed=() c_missing=() c_extra=()
    local -A want has seen want_formula has_formula seen_formula want_tap has_tap seen_tap
    local col_width

    if (( do_load )); then
      _bud_begin "Loading Homebrew state"
      _bud_load_brew_state
      _bud_done
    fi

    _bud_done "Desired vs installed · ${#desired_formulas[@]} formulae · ${#desired_taps[@]} taps · ${#desired[@]} casks"

    if (( ${#desired_taps[@]} )); then
      for t in "${desired_taps[@]}"; do want_tap[$t]=1; done
      for t in "${_bud_brew_taps[@]}"; do has_tap[$t]=1; done
      for t in "${desired_taps[@]}"; do
        if [[ -n ${has_tap[$t]} ]]; then
          t_installed+=("$t")
        else
          t_missing+=("$t")
        fi
      done
      for t in "${_bud_brew_taps[@]}"; do
        [[ -n ${seen_tap[$t]} ]] && continue
        seen_tap[$t]=1
        [[ -z ${want_tap[$t]} ]] && t_extra+=("$t")
      done
    fi

    for f in "${desired_formulas[@]}"; do want_formula[$f]=1; done
    for f in "${_bud_brew_formulas[@]}"; do has_formula[$f]=1; done
    for f in "${desired_formulas[@]}"; do
      if [[ -n ${has_formula[$f]} ]]; then
        f_installed+=("$f")
      else
        f_missing+=("$f")
      fi
    done
    for f in "${_bud_brew_formulas[@]}"; do
      [[ -n ${seen_formula[$f]} ]] && continue
      seen_formula[$f]=1
      [[ -z ${want_formula[$f]} ]] && f_extra+=("$f")
    done

    for c in "${desired[@]}"; do want[$c]=1; done
    for c in "${_bud_brew_casks[@]}"; do has[$c]=1; done
    for c in "${desired[@]}"; do
      if [[ -n ${has[$c]} ]]; then
        c_installed+=("$c")
      else
        c_missing+=("$c")
      fi
    done
    for c in "${_bud_brew_casks[@]}"; do
      [[ -n ${seen[$c]} ]] && continue
      seen[$c]=1
      [[ -z ${want[$c]} ]] && c_extra+=("$c")
    done

    col_width=$(_bud_list_col_width)
    _bud_init_grid_layout "$col_width" \
      "${f_installed[@]}" "${f_missing[@]}" "${f_extra[@]}" \
      "${t_installed[@]}" "${t_missing[@]}" "${t_extra[@]}" \
      "${c_installed[@]}" "${c_missing[@]}" "${c_extra[@]}"

    _bud_print_section "Formulae · in list, installed" green "$col_width" "${f_installed[@]}"
    _bud_print_section "Formulae · in list, not installed" yellow "$col_width" "${f_missing[@]}"
    _bud_print_section "Formulae · installed, not in list" blue "$col_width" "${f_extra[@]}"
    _bud_print_section "Taps · in list, tapped" green "$col_width" "${t_installed[@]}"
    _bud_print_section "Taps · in list, not tapped" yellow "$col_width" "${t_missing[@]}"
    _bud_print_section "Taps · tapped, not in list" blue "$col_width" "${t_extra[@]}"
    _bud_print_section "Casks · in list, installed" green "$col_width" "${c_installed[@]}"
    _bud_print_section "Casks · in list, not installed" yellow "$col_width" "${c_missing[@]}"
    _bud_print_section "Casks · installed, not in list" blue "$col_width" "${c_extra[@]}"
  }

  # Desired casks that are already installed (for upgrade), without printing status.
  _bud_collect_casks_to_upgrade() {
    local -a installed=()
    local -A has
    local c

    for c in "${_bud_brew_casks[@]}"; do has[$c]=1; done
    for c in "${desired[@]}"; do
      [[ -n ${has[$c]} ]] && installed+=("$c")
    done
    typeset -g _bud_c_to_upgrade=("${installed[@]}")
  }

  # First line of `brew info` is "==> <token>: ..." — token must match $name (not an old cask alias).
  _bud_brew_token() {
    local brew_type="$1" pkg="$2"
    _bud_log_brew info --${brew_type} "$pkg"
    command brew info --${brew_type} "$pkg" 2>/dev/null | sed -n '1s/^==> \([^: (]*\).*/\1/p'
  }

  # Core tokens match exactly; tap packages match by short name (e.g. acli → atlassian/acli/acli).
  _bud_brew_token_matches_name() {
    local token="$1" name="$2"
    [[ -n "$token" && ( "$token" == "$name" || "${token##*/}" == "$name" ) ]]
  }

  _bud_is_tap_formula_spec() {
    [[ "$1" == */*/* ]]
  }

  _bud_tap_from_formula_spec() {
    echo "${1%/*}"
  }

  _bud_formula_name_from_spec() {
    echo "${1##*/}"
  }

  _bud_tap_for_installed_formula() {
    local name="$1" token
    token=$(_bud_brew_token formula "$name") || return 1
    [[ "$token" == */* ]] || return 1
    echo "${token%/*}"
  }

  _bud_array_has() {
    local needle="$1"
    shift
    local x
    for x in "$@"; do
      [[ "$x" == "$needle" ]] && return 0
    done
    return 1
  }

  _bud_array_remove_value() {
    local -n _arr=$1
    local val="$2"
    local -a _out=()
    local x
    for x in "${_arr[@]}"; do
      [[ "$x" != "$val" ]] && _out+=("$x")
    done
    _arr=("${_out[@]}")
  }

  _bud_detect_brew_type() {
    local name="$1"
    local is_cask=0 is_formula=0
    local cask_token formula_token

    formula_token=$(_bud_brew_token formula "$name")
    _bud_brew_token_matches_name "$formula_token" "$name" && is_formula=1

    cask_token=$(_bud_brew_token cask "$name")
    _bud_brew_token_matches_name "$cask_token" "$name" && is_cask=1

    if (( is_cask && is_formula )); then
      print -P "%F{yellow}⚠️  '$name' exists as both cask and formula%f" >&2
      print -P "%F{blue}💡 Use 'budz add --cask $name' or 'budz add --formula $name'%f" >&2
      return 1
    elif (( is_cask )); then
      echo cask
    elif (( is_formula )); then
      echo formula
    else
      print -P "%F{yellow}⚠️  '$name' not found in Homebrew%f" >&2
      print -P "%F{blue}💡 Use 'brew search $name' to find similar packages%f" >&2
      return 1
    fi
  }

  _bud_label_for_type() {
    case "$1" in
      cask) echo cask ;;
      formula) echo formula ;;
      tap) echo tap ;;
    esac
  }

  _bud_filter_casks_to_upgrade() {
    local -a input=("$@")
    local -a filtered=()
    local -A exclude
    local c

    for c in "${_bud_exclude_casks[@]}"; do exclude[$c]=1; done
    for c in "${input[@]}"; do
      [[ -z ${exclude[$c]} ]] && filtered+=("$c")
    done

    typeset -g _bud_c_to_upgrade=("${filtered[@]}")
  }

  # Parse arguments
  local action=""
  local -a pkg_names=()
  local -a exclude_casks_cli=()
  local force_type=""

  _bud_title
  
  while [[ $# -gt 0 ]]; do
    case $1 in
      -e|--exclude)
        shift
        while [[ $# -gt 0 && "$1" != --* && "$1" != -e && "$1" != add && "$1" != remove && "$1" != list && "$1" != ls && "$1" != -h && "$1" != --help ]]; do
          exclude_casks_cli+=("$1")
          shift
        done
        ;;
      add|remove)
        action="$1"
        shift
        while [[ $# -gt 0 ]]; do
          case $1 in
            --cask)
              force_type="cask"
              shift
              ;;
            --formula)
              force_type="formula"
              shift
              ;;
            --tap)
              force_type="tap"
              shift
              ;;
            *)
              pkg_names+=("$1")
              shift
              ;;
          esac
        done
        ;;
      ls|list)
        action="list"
        shift
        ;;
      -h|--help)
        _bud_done "Usage: budz [command]"
        echo "Usage: budz [COMMAND] [OPTIONS]"
        echo ""
        echo "Commands:"
        echo "  add <name...>      Add casks/formulae/taps (auto-detected; user/repo = tap)"
        echo "  remove <name...>   Remove from desired casks, formulae, or taps list"
        echo "  ls, list           Show desired vs installed"
        echo ""
        echo "Options:"
        echo "  -e, --exclude <name...>  Exclude casks from upgrade this run"
        echo "  --cask             Force cask type (for add)"
        echo "  --formula          Force formula type (for add)"
        echo "  --tap              Force tap type (for add)"
        echo "  -h, --help         Show this help message"
        return 0
        ;;
      *)
        print -P "%F{red}❌ Unknown command: $1%f"
        echo "Use '-h' or '--help' for usage information"
        return 1
        ;;
    esac
  done
  
  # Handle add/remove/list actions
  if [[ -n "$action" ]]; then
    case "$action" in
      list)
        _bud_show_desired_status 1
        return 0
        ;;
      add|remove)
        if (( ${#pkg_names[@]} == 0 )); then
          print -P "%F{red}❌ Package name is required for $action action%f"
          return 1
        fi

        local pkg_name failed=0
        local -a succeeded=()

        for pkg_name in "${pkg_names[@]}"; do
          if [[ "$action" == remove ]]; then
            local remove_type=""
            if _bud_array_has "$pkg_name" "${desired[@]}"; then
              remove_type="cask"
              _bud_array_remove_value desired "$pkg_name"
            elif _bud_array_has "$pkg_name" "${desired_formulas[@]}"; then
              remove_type="formula"
              _bud_array_remove_value desired_formulas "$pkg_name"
            elif _bud_array_has "$pkg_name" "${desired_taps[@]}"; then
              remove_type="tap"
              _bud_array_remove_value desired_taps "$pkg_name"
            else
              print -P "%F{yellow}⚠️  '$pkg_name' not found in casks, formulae, or taps list%f"
              failed=1
              continue
            fi

            _bud_desired_write || { failed=1; continue; }
            succeeded+=("$pkg_name")
            print -P "%F{green}✅ Removed%f %F{cyan}'$pkg_name'%F{green} from $(_bud_label_for_type "$remove_type")s list%f"
            continue
          fi

          local pkg_type="$force_type"
          local list_name="$pkg_name"
          local tap_for_formula=""

          if [[ -z "$pkg_type" ]]; then
            if _bud_is_tap_formula_spec "$pkg_name"; then
              pkg_type=formula
              list_name=$(_bud_formula_name_from_spec "$pkg_name")
              tap_for_formula=$(_bud_tap_from_formula_spec "$pkg_name")
            elif [[ "$pkg_name" == */* ]]; then
              pkg_type=tap
            else
              pkg_type=$(_bud_detect_brew_type "$pkg_name") || { failed=1; continue; }
            fi
          elif [[ "$pkg_type" == tap ]]; then
            if [[ "$pkg_name" != */* ]] || _bud_is_tap_formula_spec "$pkg_name"; then
              print -P "%F{yellow}⚠️  Tap name must be user/repo (e.g. mongodb/brew)%f"
              failed=1
              continue
            fi
          elif [[ "$pkg_type" == formula ]] && _bud_is_tap_formula_spec "$pkg_name"; then
            list_name=$(_bud_formula_name_from_spec "$pkg_name")
            tap_for_formula=$(_bud_tap_from_formula_spec "$pkg_name")
          else
            _bud_log_brew info --${pkg_type} "$pkg_name"
            if ! command brew info --${pkg_type} "$pkg_name" >/dev/null 2>&1; then
              print -P "%F{yellow}⚠️  '$pkg_name' not found as a Homebrew $(_bud_label_for_type "$pkg_type")%f"
              failed=1
              continue
            fi
          fi

          if [[ "$pkg_type" == formula && -z "$tap_for_formula" ]]; then
            tap_for_formula=$(_bud_tap_for_installed_formula "$pkg_name" 2>/dev/null) || true
          fi

          if [[ "$pkg_type" == formula ]]; then
            _bud_log_brew info --formula "$pkg_name"
            if ! command brew info --formula "$pkg_name" >/dev/null 2>&1; then
              print -P "%F{yellow}⚠️  '$pkg_name' not found as a Homebrew formula%f"
              failed=1
              continue
            fi
          fi

          local label="$(_bud_label_for_type "$pkg_type")"
          local already=0
          case "$pkg_type" in
            cask) _bud_array_has "$list_name" "${desired[@]}" && already=1 ;;
            formula) _bud_array_has "$list_name" "${desired_formulas[@]}" && already=1 ;;
            tap) _bud_array_has "$list_name" "${desired_taps[@]}" && already=1 ;;
          esac
          if (( already )); then
            print -P "%F{yellow}⚠️  '$list_name' already exists in ${label}s list%f"
            failed=1
            continue
          fi

          if [[ -n "$tap_for_formula" ]]; then
            if ! _bud_array_has "$tap_for_formula" "${desired_taps[@]}"; then
              desired_taps+=("$tap_for_formula")
              print -P "%F{green}✅ Added%f %F{cyan}'$tap_for_formula'%F{green} to taps list%f"
            fi
          fi

          if [[ "$pkg_type" == cask ]]; then
            local tap_info
            _bud_log_brew search --cask "$pkg_name"
            tap_info=$(command brew search --cask "$pkg_name" 2>/dev/null | grep "$pkg_name" | grep "/" | head -1)
            if [[ -n "$tap_info" ]]; then
              local tap_name=$(echo "$tap_info" | cut -d'/' -f1-2 | sed 's/^[[:space:]]*//')
              if [[ -n "$tap_name" && "$tap_name" != "homebrew/cask" && "$tap_name" != "homebrew/cask-fonts" ]]; then
                _bud_log_brew tap
                if ! command brew tap | grep -q "^$tap_name$"; then
                  _bud_begin "Adding required tap: $tap_name"
                  _bud_trust_tap "$tap_name" || { failed=1; continue; }
                  _bud_brew tap "$tap_name" || { failed=1; continue; }
                  _bud_done
                fi
              fi
            fi
          fi

          case "$pkg_type" in
            cask) desired+=("$list_name") ;;
            formula) desired_formulas+=("$list_name") ;;
            tap) desired_taps+=("$list_name") ;;
          esac
          _bud_desired_write || { failed=1; continue; }
          succeeded+=("$list_name")
          print -P "%F{green}✅ Added%f %F{cyan}'$list_name'%F{green} to ${label}s list%f"
        done

        if (( ${#succeeded[@]} )); then
          echo ""
          echo "💡 Changes saved to $desired_file"
        fi
        return $failed
        ;;
    esac
  fi

  typeset -gUa _bud_exclude_casks=("${exclude_casks_cli[@]}")

  _bud_done "Desired lists: ${#desired_formulas[@]} formulae · ${#desired_taps[@]} taps · ${#desired[@]} casks"

  _bud_begin "Loading Homebrew state"
  _bud_load_brew_state
  _bud_done

  local -a missing_taps=()
  local tap
  local -A has_tap
  for tap in "${_bud_brew_taps[@]}"; do has_tap[$tap]=1; done
  for tap in "${desired_taps[@]}"; do
    [[ -z ${has_tap[$tap]} ]] && missing_taps+=("$tap")
  done
  if (( ${#missing_taps[@]} )); then
    _bud_begin "Ensuring taps · ${#missing_taps[@]} missing: ${missing_taps[*]}"
    _bud_ensure_desired_taps || return 1
    _bud_done
  else
    _bud_done "Ensured taps · all present"
  fi

  _bud_begin "Updating Homebrew"
  _bud_brew update || return 1
  _bud_done

  _bud_begin "Upgrading formulae"
  _bud_brew upgrade --formula -y || return 1
  _bud_done

  _bud_begin "Repairing taps"
  _bud_brew tap --repair || return 1
  _bud_done

  _bud_begin "Reloading Homebrew state"
  _bud_load_brew_state
  _bud_done
  _bud_collect_casks_to_upgrade
  local -i c_eligible=${#_bud_c_to_upgrade[@]}
  _bud_filter_casks_to_upgrade "${_bud_c_to_upgrade[@]}"

  if (( ${#_bud_exclude_casks[@]} )); then
    _bud_info "Excluding ${#_bud_exclude_casks[@]} cask(s): ${_bud_exclude_casks[*]}"
  fi

  if (( ${#_bud_c_to_upgrade[@]} )); then
    _bud_begin "Upgrading casks · ${#_bud_c_to_upgrade[@]} of ${c_eligible} eligible"
    _bud_brew upgrade --cask -y "${_bud_c_to_upgrade[@]}" || return 1
    _bud_done
  elif (( c_eligible && ${#_bud_exclude_casks[@]} )); then
    _bud_info "All eligible casks were excluded; no cask upgrade."
  else
    _bud_info "No casks in list are installed; skipping cask upgrade."
  fi

  _bud_begin "Cleaning up Homebrew"
  _bud_brew cleanup --prune=1 || return 1
  _bud_done
}

