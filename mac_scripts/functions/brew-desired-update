
bud() {
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

  # === Desired casks (read from file) ===
  local casks_file="$func_dir/casks.txt"
  local -a desired=()
  
  if [[ -f "$casks_file" ]]; then
    while IFS= read -r line; do
      # Skip empty lines and comments
      [[ -n "$line" && ! "$line" =~ ^[[:space:]]*# ]] && desired+=("$line")
    done < "$casks_file"
  else
    echo "❌ Casks file not found: $casks_file"
    return 1
  fi
  # ====================================

  # === Desired formulae (read from file) ===
  local formulas_file="$func_dir/formulas.txt"
  local -a desired_formulas=()

  if [[ -f "$formulas_file" ]]; then
    while IFS= read -r line; do
      [[ -n "$line" && ! "$line" =~ ^[[:space:]]*# ]] && desired_formulas+=("$line")
    done < "$formulas_file"
  fi
  # =========================================

  _bud_print_formulas() {
    local col_width="${1:-0}"
    (( ${#desired_formulas[@]} )) || return 0

    local -a installed_formulas raw current_desired=() missing=()
    raw=($(brew list --formula 2>/dev/null))
    installed_formulas=()
    for f in "${raw[@]}"; do installed_formulas+=("${f##*/}"); done

    local -A has_formula
    for f in "${installed_formulas[@]}"; do has_formula[$f]=1; done

    for f in "${desired_formulas[@]}"; do
      if [[ -n ${has_formula[$f]} ]]; then
        current_desired+=("$f")
      else
        missing+=("$f")
      fi
    done

    if (( ! ${#current_desired[@]} && ! ${#missing[@]} )); then
      return 0
    fi

    echo ""

    if (( ${#current_desired[@]} )); then
      echo "✅ Desired formulae (installed):"
      if (( col_width > 0 )); then
        printf "%s\n" "${current_desired[@]}" | column -c "$col_width" | expand | while IFS= read -r line; do
          print -P "%F{cyan}  $line%f"
        done
      else
        for f in "${current_desired[@]}"; do print -P "%F{cyan}$f%f"; done
      fi
    fi

    if (( ${#missing[@]} )); then
      echo ""
      echo "📥 Desired formulae NOT installed:"
      if (( col_width > 0 )); then
        printf "%s\n" "${missing[@]}" | column -c "$col_width" | expand | while IFS= read -r line; do
          print -P "%F{yellow}  $line%f"
        done
      else
        for f in "${missing[@]}"; do print -P "%F{green}$f%f"; done
      fi
    fi
  }

  _bud_print_taps() {
    local -a taps=($(brew tap 2>/dev/null))
    (( ${#taps[@]} )) || return 0

    echo ""
    echo "📦 Installed taps:"
    for t in "${taps[@]}"; do print -P "%F{blue}  $t%f"; done
    echo
  }

  _bud_detect_brew_type() {
    local name="$1"
    local is_cask=0 is_formula=0

    brew info --cask "$name" >/dev/null 2>&1 && is_cask=1
    brew info --formula "$name" >/dev/null 2>&1 && is_formula=1

    if (( is_cask && is_formula )); then
      print -P "%F{yellow}⚠️  '$name' exists as both cask and formula%f"
      print -P "%F{blue}💡 Use 'bud add --cask $name' or 'bud add --formula $name'%f"
      return 1
    elif (( is_cask )); then
      echo cask
    elif (( is_formula )); then
      echo formula
    else
      print -P "%F{yellow}⚠️  '$name' not found in Homebrew%f"
      print -P "%F{blue}💡 Use 'brew search $name' to find similar packages%f"
      return 1
    fi
  }

  _bud_list_file_for_type() {
    [[ "$1" == cask ]] && echo "$casks_file" || echo "$formulas_file"
  }

  _bud_label_for_type() {
    [[ "$1" == cask ]] && echo cask || echo formula
  }

  _bud_git_sync() {
    local action="$1"
    shift
    local -a names=("$@")

    local git_root
    git_root=$(git -C "$func_dir" rev-parse --show-toplevel 2>/dev/null) || return 0
    git -C "$git_root" rev-parse --git-dir >/dev/null 2>&1 || return 0

    git -C "$git_root" add -- "$casks_file" "$formulas_file"

    if git -C "$git_root" diff --cached --quiet -- "$casks_file" "$formulas_file"; then
      return 0
    fi

    local name_list="${(j:,:)names}"
    local commit_msg="chore(bud): ${action} ${name_list}"
    local commit_out first_line

    if ! commit_out=$(git -C "$git_root" commit -m "$commit_msg" 2>&1); then
      print -P "%F{yellow}⚠️  Git commit failed%f"
      return 1
    fi

    first_line="${commit_out%%$'\n'*}"
    print -P "%F{cyan}${first_line}%f"

    if [[ -n "$(git -C "$git_root" remote 2>/dev/null)" ]]; then
      echo ""
      if git -C "$git_root" push --quiet; then
        print -P "%F{green}✅ Pushed to remote%f"
      else
        print -P "%F{yellow}⚠️  Git push failed%f"
        return 1
      fi
    fi
  }
  
  # Parse arguments
  local action=""
  local -a pkg_names=()
  local force_type=""
  
  while [[ $# -gt 0 ]]; do
    case $1 in
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
        echo "Usage: bud [COMMAND] [OPTIONS]"
        echo ""
        echo "Commands:"
        echo "  add <name...>      Add one or more casks/formulae (auto-detected)"
        echo "  remove <name...>   Remove one or more casks/formulae from desired list"
        echo "  ls, list           Show current desired casks and formulae"
        echo ""
        echo "Options:"
        echo "  --cask             Force cask type (for add)"
        echo "  --formula          Force formula type (for add)"
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
        # Check installed casks first (needed for "current desired" = desired ∩ installed)
        local -a installed raw
        raw=($(brew list --cask --full-name 2>/dev/null))
        installed=()
        for c in "${raw[@]}"; do installed+=("${c##*/}"); done

        # Sets
        local -A want has
        for c in "${desired[@]}";  do want[$c]=1; done
        for c in "${installed[@]}"; do has[$c]=1;  done

        # Current desired = desired AND installed
        local -a current_desired=()
        for c in "${desired[@]}"; do
          [[ -n ${has[$c]} ]] && current_desired+=("$c")
        done

        # Compute lists
        local -a missing=() extras=()
        local -A seen
        for c in "${desired[@]}"; do
          if [[ -z ${has[$c]} ]]; then
            missing+=("$c")
          fi
        done
        for c in "${installed[@]}"; do
          [[ -n ${seen[$c]} ]] && continue
          seen[$c]=1
          [[ -z ${want[$c]} ]] && extras+=("$c")
        done

        echo "✅ Desired casks (installed):"
        # Get terminal width, try stty first (more reliable in some shells), then tput, default to 100
        local term_width
        if [[ -t 1 ]]; then
          term_width=$(stty size 2>/dev/null | awk '{print $2}')
        fi
        if [[ -z "$term_width" ]]; then
          term_width=$(tput cols 2>/dev/null)
        fi
        [[ -z "$term_width" || "$term_width" -lt 20 ]] && term_width=100
        
        # Subtract 4 for indentation/margin
        local col_width=$((term_width - 4))

        if [[ ${#current_desired[@]} -gt 0 ]]; then
          printf "%s\n" "${current_desired[@]}" | column -c "$col_width" | expand | while IFS= read -r line; do
            print -P "%F{cyan}  $line%f"
          done
        fi

        if (( ${#missing[@]} )); then
          echo ""
          echo "📥 In desired but NOT installed:"
          printf "%s\n" "${missing[@]}" | column -c "$col_width" | expand | while IFS= read -r line; do
            print -P "%F{yellow}  $line%f"
          done
        fi

        if (( ${#extras[@]} )); then
          echo ""
          echo "ℹ️  Installed but NOT in desired:"
          printf "%s\n" "${extras[@]}" | column -c "$col_width" | expand | while IFS= read -r line; do
            print -P "%F{magenta}  $line%f"
          done
        fi

        _bud_print_formulas "$col_width"
        _bud_print_taps
        
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
            local remove_type="" remove_file=""
            if grep -q "^$pkg_name$" "$casks_file" 2>/dev/null; then
              remove_type="cask"
              remove_file="$casks_file"
            elif grep -q "^$pkg_name$" "$formulas_file" 2>/dev/null; then
              remove_type="formula"
              remove_file="$formulas_file"
            else
              print -P "%F{yellow}⚠️  '$pkg_name' not found in casks or formulae list%f"
              failed=1
              continue
            fi

            sed -i '' "/^$pkg_name$/d" "$remove_file"
            sort -o "$remove_file" "$remove_file"
            succeeded+=("$pkg_name")
            print -P "%F{green}✅ Removed%f %F{cyan}'$pkg_name'%F{green} from $(_bud_label_for_type "$remove_type")s list%f"
            continue
          fi

          local pkg_type="$force_type"
          if [[ -z "$pkg_type" ]]; then
            pkg_type=$(_bud_detect_brew_type "$pkg_name") || { failed=1; continue; }
          elif ! brew info --${pkg_type} "$pkg_name" >/dev/null 2>&1; then
            print -P "%F{yellow}⚠️  '$pkg_name' not found as a Homebrew $(_bud_label_for_type "$pkg_type")%f"
            failed=1
            continue
          fi

          local target_file="$(_bud_list_file_for_type "$pkg_type")"
          local label="$(_bud_label_for_type "$pkg_type")"

          if grep -q "^$pkg_name$" "$target_file" 2>/dev/null; then
            print -P "%F{yellow}⚠️  '$pkg_name' already exists in ${label}s list%f"
            failed=1
            continue
          fi

          if [[ "$pkg_type" == cask ]]; then
            local tap_info
            tap_info=$(brew search --cask "$pkg_name" 2>/dev/null | grep "$pkg_name" | grep "/" | head -1)
            if [[ -n "$tap_info" ]]; then
              local tap_name=$(echo "$tap_info" | cut -d'/' -f1-2 | sed 's/^[[:space:]]*//')
              if [[ -n "$tap_name" && "$tap_name" != "homebrew/cask" && "$tap_name" != "homebrew/cask-fonts" ]]; then
                if ! brew tap | grep -q "^$tap_name$"; then
                  echo "🔧 Adding required tap: $tap_name"
                  brew tap "$tap_name"
                fi
              fi
            fi
          fi

          echo "$pkg_name" >> "$target_file"
          sort -o "$target_file" "$target_file"
          succeeded+=("$pkg_name")
          print -P "%F{green}✅ Added%f %F{cyan}'$pkg_name'%F{green} to ${label}s list%f"
        done

        echo ""
        if (( ${#succeeded[@]} )); then
          echo "💡 Changes take effect immediately"
          echo ""
          _bud_git_sync "$action" "${succeeded[@]}" || failed=1
        fi
        return $failed
        ;;
    esac
  fi

  brew update
  brew upgrade
  brew tap --repair

  # Installed casks (normalize: strip tap prefix)
  local -a installed raw
  raw=($(brew list --cask --full-name 2>/dev/null))
  installed=()
  for c in "${raw[@]}"; do installed+=("${c##*/}"); done

  # Sets
  local -A want has
  for c in "${desired[@]}";  do want[$c]=1; done
  for c in "${installed[@]}"; do has[$c]=1;  done

  # Compute:
  # - missing: in desired but NOT installed
  # - extras:  installed but NOT in desired
  # - to_upgrade: desired ∩ installed
  local -a missing=() extras=() to_upgrade=()
  local -A seen

  for c in "${desired[@]}"; do
    if [[ -n ${has[$c]} ]]; then
      to_upgrade+=("$c")
    else
      missing+=("$c")
    fi
  done

  for c in "${installed[@]}"; do
    [[ -n ${seen[$c]} ]] && continue
    seen[$c]=1
    [[ -z ${want[$c]} ]] && extras+=("$c")
  done

  # Print lists
  if (( ${#missing[@]} )); then
    echo ""
    echo "📥 In desired but NOT installed:"
    for c in "${missing[@]}"; do print -P "%F{green}$c%f"; done
    echo
  fi

  if (( ${#extras[@]} )); then
    echo "ℹ️ Installed but NOT in desired:"
    for c in "${extras[@]}"; do print -P "%F{green}$c%f"; done
    echo
  fi

  _bud_print_formulas
  _bud_print_taps

  # Upgrade only already-installed desired casks (single command)
  if (( ${#to_upgrade[@]} )); then
    echo "🔧 Upgrading installed desired casks:"
    brew upgrade --cask "${to_upgrade[@]}"
  else
    echo "ℹ️ No desired casks are currently installed; skipping upgrade."
  fi

  # Cleanup
  echo ""
  brew cleanup --prune=1 
  #--prune=all - this is too aggressive and will remove too many packages
}

