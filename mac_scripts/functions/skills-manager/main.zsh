skm() {
  emulate -L zsh

  local func_dir="$(cd "$(dirname "${(%):-%x}")" && pwd)"
  local skills_file="$func_dir/list.json"

  _skm_usage() {
    _skm_ui_title "SKILLS MANAGER"
    _skm_ui_step "◇" "Usage: skm <command> [args]"
    _skm_ui_active_step "Available commands"
    echo ""
    print -P "%F{cyan}│%f  %F{green}ls%f, %F{green}list%f"
    print -P "%F{cyan}│%f      Show saved sources from list.json"
    print -P "%F{cyan}│%f"
    print -P "%F{cyan}│%f  %F{green}show%f [source]"
    print -P "%F{cyan}│%f      Show available skills; opens a single-select UI without source"
    print -P "%F{cyan}│%f"
    print -P "%F{cyan}│%f  %F{green}add%f <source...>"
    print -P "%F{cyan}│%f      Add or update saved sources"
    print -P "%F{cyan}│%f"
    print -P "%F{cyan}│%f  %F{green}remove%f <source...>"
    print -P "%F{cyan}│%f      Remove saved sources"
    _skm_ui_list_end
  }

  _skm_error() {
    print -P "%F{red}❌ $*%f" >&2
  }

  _skm_warn() {
    print -P "%F{yellow}⚠️  $*%f" >&2
  }

  _skm_ui_title() {
    local title="$1"

    echo ""
    print -P "   %K{cyan}%F{black} ${title} %f%k"
    print -P "%F{cyan}│%f"
  }

  _skm_ui_step() {
    local marker="$1"
    local text="$2"

    print -P "%F{green}${marker}%f  ${text}"
    print -P "%F{cyan}│%f"
  }

  _skm_ui_active_step() {
    local text="$1"

    print -P "%F{cyan}◆%f  ${text}"
  }

  _skm_ui_list_end() {
    print -P "%F{cyan}└%f"
  }

  _skm_ui_cancelled() {
    print -P "%F{cyan}└%f  %F{red}${1:-Operation} cancelled%f"
  }

  _skm_require_tools() {
    if ! command -v node >/dev/null 2>&1; then
      _skm_error "node is required to read and update $skills_file"
      return 1
    fi

    if ! command -v npx >/dev/null 2>&1; then
      _skm_error "npx is required to run 'npx skills add'"
      return 1
    fi
  }

  _skm_normalize_file() {
    if [[ ! -f "$skills_file" ]]; then
      echo "[]" > "$skills_file"
    fi
  }

  _skm_git_sync() {
    local action="$1"
    shift
    local -a names=("$@")

    local git_root
    git_root=$(git -C "$func_dir" rev-parse --show-toplevel 2>/dev/null) || return 0
    git -C "$git_root" rev-parse --git-dir >/dev/null 2>&1 || return 0

    git -C "$git_root" add -- "$skills_file"

    if git -C "$git_root" diff --cached --quiet -- "$skills_file"; then
      return 0
    fi

    local name_list="${(j:,:)names}"
    local commit_msg="chore(skm): ${action} ${name_list}"
    local commit_out first_line

    if ! commit_out=$(git -C "$git_root" commit -m "$commit_msg" 2>&1); then
      print -P "%F{cyan}│%f"
      print -P "%F{cyan}└%f  %F{yellow}⚠️  Git commit failed%f"
      return 1
    fi

    first_line="${commit_out%%$'\n'*}"
    print -P "%F{cyan}│%f"
    print -P "%F{cyan}│%f  %F{green}■%f ${first_line}"

    if [[ -n "$(git -C "$git_root" remote 2>/dev/null)" ]]; then
      if git -C "$git_root" push --quiet; then
        print -P "%F{cyan}└%f  %F{green}✅ Pushed to remote%f"
      else
        print -P "%F{cyan}└%f  %F{yellow}⚠️  Git push failed%f"
        return 1
      fi
    else
      print -P "%F{cyan}└%f"
    fi
  }

  _skm_find_source() {
    local source="$1"

    node -e '
      const fs = require("fs");
      const file = process.argv[1];
      const source = process.argv[2];
      const data = JSON.parse(fs.readFileSync(file, "utf8"));
      const presets = Array.isArray(data) ? data : data.presets || [];
      const preset = presets.find((item) => item.source === source);
      process.exit(preset ? 0 : 1);
    ' "$skills_file" "$source"
  }

  _skm_install_source() {
    local source="$1"

    _skm_require_tools || return 1
    _skm_normalize_file

    if ! _skm_find_source "$source"; then
      _skm_error "Source not found: $source"
      return 1
    fi

    local -a cmd=(npx skills add "$source")

    print -P "%F{cyan}▶ Installing source:%f $source"

    "${cmd[@]}"
  }

  _skm_list_sources() {
    _skm_require_tools || return 1
    _skm_normalize_file

    local sources_output
    sources_output=$(node -e '
      const fs = require("fs");
      const file = process.argv[1];
      const data = JSON.parse(fs.readFileSync(file, "utf8"));
      const presets = Array.isArray(data) ? data : data.presets || [];

      console.log(JSON.stringify(presets.map(p => p.source)));
    ' "$skills_file")

    local -a sources
    sources=($(echo "$sources_output" | node -e '
      const sources = JSON.parse(require("fs").readFileSync(0, "utf8"));
      sources.forEach(s => console.log(s));
    '))

    _skm_ui_title "SKILLS MANAGER"
    _skm_ui_step "◇" "Source list: $skills_file"
    _skm_ui_step "◇" "Found %F{green}${#sources[@]}%f sources"
    _skm_ui_active_step "Saved sources"

    if (( ${#sources[@]} == 0 )); then
      print -P "%F{cyan}│%f  %F{yellow}■%f No sources saved"
    else
      local source
      for source in "${sources[@]}"; do
        print -P "%F{cyan}│%f  %F{green}■%f $source"
      done
    fi

    _skm_ui_list_end
  }

  _skm_show_source() {
    local source="$1"

    _skm_require_tools || return 1

    node -e '
      const cp = require("child_process");
      const source = process.argv[1];
      const result = cp.spawnSync("npx", ["skills", "add", source, "--list"], { stdio: "inherit" });
      process.exit(result.status ?? 1);
    ' "$source"
  }

  _skm_show_selector() {
    _skm_require_tools || return 1
    _skm_normalize_file

    if [[ ! -t 0 || ! -t 1 ]]; then
      _skm_error "skm show requires an interactive terminal when no source is provided"
      echo "Usage: skm show <source>"
      return 1
    fi

    local -a sources=()
    sources=("${(@f)$(node -e '
      const fs = require("fs");
      const file = process.argv[1];
      const data = JSON.parse(fs.readFileSync(file, "utf8"));
      const presets = Array.isArray(data) ? data : data.presets || [];

      for (const preset of presets) {
        console.log(preset.source);
      }
    ' "$skills_file")}")

    local -a filtered_sources=()
    local source
    for source in "${sources[@]}"; do
      [[ -n "$source" ]] && filtered_sources+=("$source")
    done
    sources=("${filtered_sources[@]}")

    if (( ${#sources[@]} == 0 )); then
      _skm_warn "No sources saved. Add one with: skm add <source>"
      return 1
    fi

    local cursor=1 key
    trap 'echo ""; _skm_ui_cancelled "Select source to inspect"; trap - INT; exit 0' INT
    while true; do
      clear
      _skm_ui_title "SKILLS MANAGER"
      _skm_ui_step "◇" "Source list: $skills_file"
      _skm_ui_step "◇" "Found %F{green}${#sources[@]}%f sources"
      _skm_ui_active_step "Select source to inspect %F{white}(enter to show, q to quit)%f"
      echo ""

      local i=1
      while (( i <= ${#sources[@]} )); do
        if [[ "$i" -eq "$cursor" ]]; then
          print -P "%F{cyan}│%f  %F{green}■%f %F{white}${sources[$i]}%f"
        else
          printf "\033[36m│\033[0m  \033[90m□ %s\033[0m\n" "${sources[$i]}"
        fi
        (( i++ ))
      done
      _skm_ui_list_end

      IFS= read -rsk1 key
      case "$key" in
        $'\x1b')
          IFS= read -rsk2 key
          case "$key" in
            "[A") (( cursor > 1 )) && (( cursor-- )) ;;
            "[B") (( cursor < ${#sources[@]} )) && (( cursor++ )) ;;
          esac
          ;;
        "j")
          (( cursor < ${#sources[@]} )) && (( cursor++ ))
          ;;
        "k")
          (( cursor > 1 )) && (( cursor-- ))
          ;;
        $'\n'|$'\r')
          echo ""
          trap - INT
          _skm_show_source "${sources[$cursor]}"
          return $?
          ;;
        "q")
          echo ""
          _skm_ui_cancelled "Select source to inspect"
          trap - INT
          return 0
          ;;
      esac
    done
  }

  _skm_show() {
    if (( $# == 0 )); then
      _skm_show_selector
      return $?
    fi

    if (( $# > 1 )); then
      _skm_error "show accepts at most one source"
      echo "Usage: skm show [source]"
      return 1
    fi

    _skm_show_source "$1"
  }

  _skm_add_sources() {
    if (( $# < 1 )); then
      _skm_error "At least one source is required"
      echo "Usage: skm add <source...>"
      return 1
    fi

    _skm_require_tools || return 1
    _skm_normalize_file

    _skm_ui_title "SKILLS MANAGER"
    _skm_ui_step "◇" "Source list: $skills_file"
    _skm_ui_step "◇" "Add request: $# sources"
    _skm_ui_active_step "Add sources"

    local result
    result=$(node -e '
      const fs = require("fs");

      const [file, ...sources] = process.argv.slice(1);
      const data = JSON.parse(fs.readFileSync(file, "utf8"));
      const presets = Array.isArray(data) ? data : data.presets || [];
      const results = [];

      for (const source of sources) {
        const existing = presets.findIndex((item) => item.source === source);
        const preset = { source };

        if (existing >= 0) {
          results.push({ source, status: "exists" });
        } else {
          presets.push(preset);
          results.push({ source, status: "added" });
        }
      }

      presets.sort((a, b) => a.source.localeCompare(b.source));
      const tmp = `${file}.${process.pid}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify(presets, null, 2) + "\n");
      fs.renameSync(tmp, file);

      console.log(JSON.stringify(results));
    ' "$skills_file" "$@")

    local added=0 skipped=0
    echo "$result" | node -e '
      const results = JSON.parse(require("fs").readFileSync(0, "utf8"));
      for (const { source, status } of results) {
        if (status === "added") {
          console.log(`added|${source}`);
        } else {
          console.log(`exists|${source}`);
        }
      }
    ' | while IFS='|' read -r op_status source; do
      if [[ "$op_status" == "added" ]]; then
        print -P "%F{cyan}│%f  %F{green}■%f Added: $source"
        (( added++ ))
      else
        print -P "%F{cyan}│%f  %F{yellow}■%f Already exists: $source"
        (( skipped++ ))
      fi
    done

    local added_count=$(echo "$result" | node -e '
      const results = JSON.parse(require("fs").readFileSync(0, "utf8"));
      console.log(results.filter(r => r.status === "added").length);
    ')
    local skipped_count=$(echo "$result" | node -e '
      const results = JSON.parse(require("fs").readFileSync(0, "utf8"));
      console.log(results.filter(r => r.status === "exists").length);
    ')

    print -P "%F{cyan}└%f  Added $added_count sources, skipped $skipped_count existing"

    if (( added_count > 0 )); then
      local -a added_sources=()
      echo "$result" | node -e '
        const results = JSON.parse(require("fs").readFileSync(0, "utf8"));
        results.filter(r => r.status === "added").forEach(r => console.log(r.source));
      ' | while IFS= read -r source; do
        added_sources+=("$source")
      done

      if (( ${#added_sources[@]} > 0 )); then
        _skm_git_sync "add" "${added_sources[@]}"
      fi
    fi
  }

  _skm_remove_sources() {
    if (( $# < 1 )); then
      _skm_error "At least one source is required"
      echo "Usage: skm remove <source...>"
      return 1
    fi

    _skm_require_tools || return 1
    _skm_normalize_file

    _skm_ui_title "SKILLS MANAGER"
    _skm_ui_step "◇" "Source list: $skills_file"
    _skm_ui_step "◇" "Remove request: $# sources"
    _skm_ui_active_step "Remove sources"

    local result
    result=$(node -e '
      const fs = require("fs");
      const file = process.argv[1];
      const sources = process.argv.slice(2);
      const data = JSON.parse(fs.readFileSync(file, "utf8"));
      const presets = Array.isArray(data) ? data : data.presets || [];
      const wanted = new Set(sources);
      const existing = new Set(presets.map((item) => item.source));
      const results = [];

      for (const source of sources) {
        if (existing.has(source)) {
          results.push({ source, status: "removed" });
        } else {
          results.push({ source, status: "missing" });
        }
      }

      const next = presets.filter((item) => !wanted.has(item.source));
      const tmp = `${file}.${process.pid}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify(next, null, 2) + "\n");
      fs.renameSync(tmp, file);

      console.log(JSON.stringify(results));
    ' "$skills_file" "$@")

    echo "$result" | node -e '
      const results = JSON.parse(require("fs").readFileSync(0, "utf8"));
      for (const { source, status } of results) {
        if (status === "removed") {
          console.log(`removed|${source}`);
        } else {
          console.log(`missing|${source}`);
        }
      }
    ' | while IFS='|' read -r op_status source; do
      if [[ "$op_status" == "removed" ]]; then
        print -P "%F{cyan}│%f  %F{green}■%f Removed: $source"
      else
        print -P "%F{cyan}│%f  %F{yellow}■%f Not found: $source"
      fi
    done

    local removed_count=$(echo "$result" | node -e '
      const results = JSON.parse(require("fs").readFileSync(0, "utf8"));
      console.log(results.filter(r => r.status === "removed").length);
    ')
    local skipped_count=$(echo "$result" | node -e '
      const results = JSON.parse(require("fs").readFileSync(0, "utf8"));
      console.log(results.filter(r => r.status === "missing").length);
    ')

    print -P "%F{cyan}└%f  Removed $removed_count sources, skipped $skipped_count missing"

    if (( removed_count > 0 )); then
      local -a removed_sources=()
      echo "$result" | node -e '
        const results = JSON.parse(require("fs").readFileSync(0, "utf8"));
        results.filter(r => r.status === "removed").forEach(r => console.log(r.source));
      ' | while IFS= read -r source; do
        removed_sources+=("$source")
      done

      if (( ${#removed_sources[@]} > 0 )); then
        _skm_git_sync "remove" "${removed_sources[@]}"
      fi
    fi
  }

  _skm_install_selector() {
    _skm_require_tools || return 1
    _skm_normalize_file

    if [[ ! -t 0 || ! -t 1 ]]; then
      _skm_error "skm requires an interactive terminal"
      return 1
    fi

    local -a rows=()
    rows=("${(@f)$(node -e '
      const fs = require("fs");
      const file = process.argv[1];
      const data = JSON.parse(fs.readFileSync(file, "utf8"));
      const presets = Array.isArray(data) ? data : data.presets || [];

      for (const preset of presets) {
        console.log(preset.source);
      }
    ' "$skills_file")}")

    local -a filtered_rows=()
    local row
    for row in "${rows[@]}"; do
      [[ -n "$row" ]] && filtered_rows+=("$row")
    done
    rows=("${filtered_rows[@]}")

    if (( ${#rows[@]} == 0 )); then
      _skm_warn "No sources saved. Add one with: skm add <source>"
      return 1
    fi

    local -a sources=()
    for row in "${rows[@]}"; do
      sources+=("$row")
    done

    local -a selected=()
    local i=1
    while (( i <= ${#sources[@]} )); do
      selected[$i]=0
      (( i++ ))
    done

    local cursor=1 key failed=0
    trap '
      clear
      print -P ""
      print -P "   %K{cyan}%F{black} SKILLS MANAGER %f%k"
      print -P "%F{cyan}│%f"
      print -P "%F{green}◇%f  Source list: '"$skills_file"'"
      print -P "%F{cyan}│%f"
      print -P "%F{green}◇%f  Found %F{green}'"${#sources[@]}"'%f sources"
      print -P "%F{cyan}│%f"
      print -P "%F{cyan}◆%f  Select sources to install %F{white}(space to toggle, enter to start, q to quit)%f"
      print -P "%F{cyan}│%f"
      i=1
      while (( i <= '"${#sources[@]}"' )); do
        local checkbox="□"
        local checkbox_color="\033[90m"
        [[ "${selected[$i]}" == "1" ]] && checkbox="■" && checkbox_color="\033[92m"
        printf "\033[36m│\033[0m  %b%s\033[0m \033[90m%s\033[0m\n" "$checkbox_color" "$checkbox" "${sources[$i]}"
        (( i++ ))
      done
      print -P "%F{cyan}│%f"
      print -P "%F{cyan}└%f  %F{red}Select sources to install cancelled%f"
      trap - INT
      exit 0
    ' INT
    while true; do
      clear
      _skm_ui_title "SKILLS MANAGER"
      _skm_ui_step "◇" "Source list: $skills_file"
      _skm_ui_step "◇" "Found %F{green}${#sources[@]}%f sources"
      _skm_ui_active_step "Select sources to install %F{white}(space to toggle, enter to start, q to quit)%f"
      print -P "%F{cyan}│%f"

      i=1
      while (( i <= ${#sources[@]} )); do
        local checkbox="□"
        local checkbox_color="\033[90m"
        local source_color="\033[90m"

        [[ "${selected[$i]}" == "1" ]] && checkbox="■" && checkbox_color="\033[92m"
        [[ "$i" -eq "$cursor" ]] && source_color="\033[97m"

        printf "\033[36m│\033[0m  %b%s\033[0m %b%s\033[0m\n" "$checkbox_color" "$checkbox" "$source_color" "${sources[$i]}"
        (( i++ ))
      done
      _skm_ui_list_end

      IFS= read -rsk1 key
      case "$key" in
        $'\x1b')
          IFS= read -rsk2 key
          case "$key" in
            "[A") (( cursor > 1 )) && (( cursor-- )) ;;
            "[B") (( cursor < ${#sources[@]} )) && (( cursor++ )) ;;
          esac
          ;;
        "j")
          (( cursor < ${#sources[@]} )) && (( cursor++ ))
          ;;
        "k")
          (( cursor > 1 )) && (( cursor-- ))
          ;;
        " ")
          [[ "${selected[$cursor]}" == "1" ]] && selected[$cursor]=0 || selected[$cursor]=1
          ;;
        $'\n'|$'\r')
          break
          ;;
        "q")
          clear
          _skm_ui_title "SKILLS MANAGER"
          _skm_ui_step "◇" "Source list: $skills_file"
          _skm_ui_step "◇" "Found %F{green}${#sources[@]}%f sources"
          _skm_ui_active_step "Select sources to install %F{white}(space to toggle, enter to start, q to quit)%f"
          print -P "%F{cyan}│%f"

          i=1
          while (( i <= ${#sources[@]} )); do
            local checkbox="□"
            local checkbox_color="\033[90m"
            [[ "${selected[$i]}" == "1" ]] && checkbox="■" && checkbox_color="\033[92m"
            printf "\033[36m│\033[0m  %b%s\033[0m \033[90m%s\033[0m\n" "$checkbox_color" "$checkbox" "${sources[$i]}"
            (( i++ ))
          done

          print -P "%F{cyan}│%f"
          _skm_ui_cancelled "Select sources to install"
          trap - INT
          return 0
          ;;
        $'\n'|$'\r')
          break
          ;;
      esac
    done
    trap - INT

    local -a chosen=()
    i=1
    while (( i <= ${#sources[@]} )); do
      [[ "${selected[$i]}" == "1" ]] && chosen+=("${sources[$i]}")
      (( i++ ))
    done

    if (( ${#chosen[@]} == 0 )); then
      _skm_ui_list_end
      _skm_warn "No sources selected"
      return 1
    fi

    _skm_ui_list_end
    echo ""
    local source
    for source in "${chosen[@]}"; do
      _skm_install_source "$source" || failed=1
    done

    return $failed
  }

  if [[ $# -eq 0 ]]; then
    _skm_install_selector
    return $?
  fi

  local action="$1"
  shift

  case "$action" in
    ls|list)
      _skm_list_sources
      ;;
    show|list-available)
      _skm_show "$@"
      ;;
    add)
      _skm_add_sources "$@"
      ;;
    remove|rm)
      _skm_remove_sources "$@"
      ;;
    -h|--help|help)
      _skm_usage
      ;;
    *)
      _skm_error "Unknown command: $action"
      echo "Use 'skm --help' for usage information"
      return 1
      ;;
  esac
}
