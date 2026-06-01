# =============================================================================
# Main Function - Command Router
# =============================================================================

# Show usage information
show_db_tools_usage() {
    # ANSI color codes
    local reset='\033[0m'
    local bold='\033[1m'
    local cyan='\033[0;36m'
    local green='\033[0;32m'
    local yellow='\033[1;33m'
    local blue='\033[0;34m'
    local magenta='\033[0;35m'
    local gray='\033[0;90m'
    
    echo -e "${bold}${cyan}🗄️  Database Tool - Unified Database Connection & Sync Manager${reset}"
    echo ""
    echo -e "${bold}This tool provides unified management for:${reset}"
    echo -e "  ${green}•${reset} SSH tunnels to connect to remote database services"
    echo -e "  ${green}•${reset} Synchronizing PostgreSQL and MongoDB databases"
    echo ""
    echo -e "${bold}${yellow}Secrets file:${reset}"
    echo -e "  ${cyan}\$CLOUD_UTILS_CONFIG_DIR/dbt/secrets${reset}"
    echo -e "  ${gray}Default: ~/Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils/dbt/secrets${reset}"
    echo -e "  ${gray}Override path: export DBT_SECRETS_FILE=/path/to/secrets${reset}"
    echo ""
    echo -e "For format, see: ${cyan}mac_scripts/functions/db-tools/secrets.example${reset}"
    echo ""
    echo -e "${bold}${blue}COMMANDS:${reset}"
    echo -e "  ${green}dbt connect${reset} [SERVICE]                    ${gray}# Connect to databases via SSH tunnels${reset}"
    echo -e "                                             ${gray}[SERVICE]: Optional service name or local port to filter${reset}"
    echo -e "  ${green}dbt disconnect${reset} [SERVICE]                 ${gray}# Disconnect SSH tunnels${reset}"
    echo -e "                                             ${gray}[SERVICE]: Optional service name or local port to filter${reset}"
    echo -e "  ${green}dbt ls|list${reset} [--status]                   ${gray}# Show connections${reset}"
    echo -e "  ${green}dbt test${reset}                                 ${gray}# Test SSH connectivity to all VPS${reset}"
    echo -e "  ${green}dbt sync ls|list${reset} [--status]              ${gray}# Show sync configurations${reset}"
    echo -e "                                             ${gray}--status: Check connection status for sync configs${reset}"
    echo -e "  ${green}dbt sync --clear-backups${reset}                 ${gray}# Drop PostgreSQL and MongoDB backup DBs${reset}"
    echo -e "  ${green}dbt sync postgres${reset} -s <idx> -d <idx>      ${gray}# Sync PostgreSQL database${reset}"
    echo -e "  ${green}dbt sync postgres${reset} ... --verbose          ${gray}# PostgreSQL sync with detailed progress${reset}"
    echo -e "  ${green}dbt sync postgres --clear-backups${reset}          ${gray}# Drop leftover PostgreSQL backup DBs${reset}"
    echo -e "  ${green}dbt sync mongodb${reset} -s <idx> -d <idx>       ${gray}# Sync MongoDB database with target backup${reset}"
    echo ""
    echo -e "${bold}${blue}SYNC FLAGS:${reset}"
    echo -e "  ${green}--source, -s${reset} <index>       Source index (1-based)"
    echo -e "  ${green}--database, -db${reset} <index>    Database index (default: 1)"
    echo -e "  ${green}--destination, -d${reset} <index>  Destination index (1-based)"
    echo -e "  ${green}--clear-backups, -c${reset}        Drop backup DBs left by cancelled/failed sync"
    echo -e "  ${green}--dry-run, -n${reset}              List backup DBs without dropping them"
    echo ""
    echo -e "${bold}${blue}EXAMPLES:${reset}"
    echo -e "  ${green}dbt connect${reset}                              ${gray}# Connect to all services${reset}"
    echo -e "  ${green}dbt connect postgres${reset}                     ${gray}# Connect to postgres service only${reset}"
    echo -e "  ${green}dbt connect 5432${reset}                         ${gray}# Connect to service on local port 5432${reset}"
    echo -e "  ${green}dbt disconnect postgres${reset}                  ${gray}# Disconnect postgres service${reset}"
    echo -e "  ${green}dbt ls|list${reset}                              ${gray}# List connections${reset}"
    echo -e "  ${green}dbt sync ls|list${reset}                         ${gray}# List sync configurations${reset}"
    echo -e "  ${green}dbt sync ls|list --status${reset}                ${gray}# List sync configs with status${reset}"
    echo -e "  ${green}dbt sync -c -n${reset}                           ${gray}# Preview backup cleanup for PostgreSQL and MongoDB${reset}"
    echo -e "  ${green}dbt sync postgres -s 1 -d 1${reset}              ${gray}# Sync PostgreSQL${reset}"
    echo -e "  ${green}dbt sync postgres -s 1 -d 1 --verbose${reset}    ${gray}# Sync PostgreSQL with detailed progress${reset}"
    echo -e "  ${green}dbt sync postgres -s 1 -db 1 -d 2${reset}        ${gray}# Sync PostgreSQL with database${reset}"
    echo -e "  ${green}dbt sync postgres -c -n${reset}                  ${gray}# Preview PostgreSQL backup cleanup on all targets${reset}"
    echo -e "  ${green}dbt sync postgres -c -d 1${reset}                ${gray}# Drop all PostgreSQL backup DBs on one target${reset}"
    echo -e "  ${green}dbt sync postgres -c -s 1 -n${reset}             ${gray}# Preview source-scoped backup cleanup${reset}"
    echo -e "  ${green}dbt sync mongodb -s 1 -d 1${reset}               ${gray}# Sync MongoDB${reset}"
    echo ""
}

# Main database tool function
db_tools() {
    # Load secrets first
    load_secrets
    
    local command="$1"
    shift
    
    case "$command" in
        connect)
            db_tools_connect "$@"
            ;;
        disconnect)
            db_tools_disconnect "$@"
            ;;
        list|ls)
            db_tools_list "$@"
            ;;
        test)
            db_tools_test "$@"
            ;;
        sync)
            db_tools_sync "$@"
            ;;
        --help|-h|help|"")
            show_db_tools_usage
            ;;
        *)
            echo "❌ Unknown command: $command"
            echo "💡 Available commands: connect, disconnect, list|ls, test, sync"
            echo "💡 Use 'dbt --help' for usage information"
            return 1
            ;;
    esac
}

# Connection commands
db_tools_connect() {
    local service_filter="$1"
    connect_db "$service_filter"
}

db_tools_disconnect() {
    local service_filter="$1"
    disconnect_db "$service_filter"
}

db_tools_list() {
    # Ensure secrets are loaded
    load_secrets
    
    # Parse flags
    local check_status=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --status)
                check_status=true
                shift
                ;;
            *)
                echo "❌ Unknown option: $1"
                echo "💡 Use: dbt list [--status]"
                return 1
                ;;
        esac
    done
    
    # Show only connections
    echo ""
    echo "🔌 Connections"
    echo "───────────────────────────────────────────────────────────────────────────────────────────────"
    show_forward_port
    echo ""
}

db_tools_test() {
    test_connections
}

# Sync list command - show only sync configurations
db_tools_sync_list() {
    # Ensure secrets are loaded
    load_secrets
    
    # Parse flags
    local check_status=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --status)
                check_status=true
                shift
                ;;
            *)
                echo "❌ Unknown option: $1"
                echo "💡 Use: dbt sync ls|list [--status]"
                return 1
                ;;
        esac
    done
    
    # Show only sync configurations
    echo ""
    echo "🗄️  Sync Configurations"
    echo "───────────────────────────────────────────────────────────────────────────────────────────────"
    list_all_configs "$check_status"
    echo ""
}

# Sync command
db_tools_sync() {
    local sync_type="$1"
    shift
    
    case "$sync_type" in
        --clear-backups|-c)
            db_tools_sync_clear_backups "$@"
            ;;
        postgres|postgresql|pg)
            db_tools_sync_postgres "$@"
            ;;
        mongodb)
            db_tools_sync_mongodb "$@"
            ;;
        ls|list)
            db_tools_sync_list "$@"
            ;;
        *)
            echo "❌ Unknown sync type: $sync_type"
            echo "💡 Use: dbt sync [postgres|mongodb] --source <idx> --destination <idx>"
            echo "💡 Or: dbt sync [postgres|mongodb] -s <idx> -d <idx> (short flags)"
            echo "💡 Or: dbt sync ls|list [--status] to list sync configurations"
            return 1
            ;;
    esac
}

db_tools_sync_clear_backups() {
    load_secrets

    local dry_run="false"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run|-n)
                dry_run="true"
                shift
                ;;
            *)
                echo "❌ Unknown option: $1"
                echo "💡 Use: dbt sync --clear-backups [--dry-run]"
                echo "💡 Or:  dbt sync -c [-n]"
                return 1
                ;;
        esac
    done

    local cleanup_failed=0

    echo ""
    echo "🧹 Backup Cleanup"
    echo "================="
    echo "  Engines: PostgreSQL, MongoDB"
    echo "  Target: all configured targets"
    echo "  Scope: *_backup_YYYYMMDD_HHMMSS"
    echo ""

    echo "🐘 PostgreSQL"
    local postgres_cleanup_args=("--clear-backups")
    [[ "$dry_run" == "true" ]] && postgres_cleanup_args+=("--dry-run")
    db_tools_sync_postgres "${postgres_cleanup_args[@]}" || cleanup_failed=1

    echo ""
    echo "🍃 MongoDB"
    local target_config=""
    local target_name=""
    local target_uri=""

    if [[ ${#MONGO_TARGETS[@]} -eq 0 ]]; then
        echo "ℹ️  No MongoDB targets configured"
    else
        for target_config in "${MONGO_TARGETS[@]}"; do
            IFS='|' read -r target_name target_uri <<< "$target_config"
            echo "Target: ${target_name}"
            clear_mongo_backup_databases "$target_uri" "$dry_run" || cleanup_failed=1
            echo ""
        done
    fi

    return $cleanup_failed
}

# Parse sync flags (support both long and short versions)
db_tools_sync_postgres() {
    # Load secrets first to ensure configuration arrays are available
    load_secrets
    
    local source_idx=""
    local db_idx=""
    local dest_idx=""
    local verbose_mode="false"
    local clear_backups="false"
    local dry_run="false"
    
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --clear-backups|-c)
                clear_backups="true"
                shift
                ;;
            --dry-run|-n)
                dry_run="true"
                shift
                ;;
            --source|-s)
                source_idx="$2"
                shift 2
                ;;
            --database|-db)
                db_idx="$2"
                shift 2
                ;;
            --destination|-d)
                dest_idx="$2"
                shift 2
                ;;
            --verbose|-v)
                verbose_mode="true"
                shift
                ;;
            *)
                echo "❌ Unknown option: $1"
                echo "💡 Use: dbt sync postgres --source <idx> --destination <idx> [--verbose]"
                echo "💡 Or:  dbt sync postgres --clear-backups [--destination <idx>] [--source <idx>] [--dry-run]"
                return 1
                ;;
        esac
    done
    
    # Validate required flags
    if [[ "$clear_backups" != "true" && -z "$dest_idx" ]]; then
        echo "❌ Error: PostgreSQL sync requires --destination"
        echo "   Use 'dbt list' to list available configurations"
        return 1
    fi

    if [[ "$clear_backups" != "true" && -z "$source_idx" ]]; then
        echo "❌ Error: PostgreSQL sync requires --source"
        echo "   Use 'dbt list' to list available configurations"
        return 1
    fi
    
    # Default database index to 1 if not specified
    db_idx=${db_idx:-1}
    
    # Validate destination
    local dest_config=""
    if [[ -n "$dest_idx" ]]; then
        dest_config=$(get_postgres_target_config "$dest_idx")
        if [[ -z "$dest_config" ]]; then
            echo "❌ Error: Invalid PostgreSQL destination index: $dest_idx"
            echo "   Available destinations: 1-${#POSTGRES_TARGETS[@]}"
            return 1
        fi
    fi

    # Validate source when syncing or when narrowing backup cleanup by source
    local source_config=""
    if [[ "$clear_backups" != "true" ]] || [[ -n "$source_idx" ]]; then
        source_config=$(get_postgres_source_config "$source_idx")
        if [[ -z "$source_config" ]]; then
            echo "❌ Error: Invalid PostgreSQL source index: $source_idx"
            echo "   Available sources: 1-${#POSTGRES_SOURCES[@]}"
            return 1
        fi
    fi
    
    # Get database
    local source_db=""
    if [[ -n "$source_idx" ]]; then
        source_db=$(get_postgres_source_database "$source_idx")
        if [[ -z "$source_db" ]]; then
            echo "❌ Error: Invalid database index: $db_idx"
            return 1
        fi
    fi
    
    # Parse configs
    local source_name=""
    local source_url=""
    local dest_name=""
    local dest_url=""
    [[ -n "$source_config" ]] && IFS='|' read -r source_name source_url _ <<< "$source_config"
    [[ -n "$dest_config" ]] && IFS='|' read -r dest_name dest_url <<< "$dest_config"

    if [[ "$clear_backups" == "true" ]]; then
        echo ""
        echo "🧹 PostgreSQL Backup Cleanup"
        echo "============================"
        if [[ -n "$dest_config" ]]; then
            echo "  Target: ${dest_name}"
        else
            echo "  Target: all PostgreSQL targets (${#POSTGRES_TARGETS[@]})"
        fi
        if [[ -n "$source_db" ]]; then
            echo "  Scope: ${source_db}_backup_*"
        else
            echo "  Scope: *_backup_YYYYMMDD_HHMMSS"
        fi
        echo ""

        if [[ -n "$dest_config" ]]; then
            clear_postgres_backup_databases "$dest_url" "$source_db" "$dry_run"
            return $?
        fi

	    local cleanup_failed=0
	    local target_config=""
	    local target_name=""
	    local target_url=""
	    if [[ ${#POSTGRES_TARGETS[@]} -eq 0 ]]; then
	        echo "ℹ️  No PostgreSQL targets configured"
	        return 1
	    fi

	    for target_config in "${POSTGRES_TARGETS[@]}"; do
	        IFS='|' read -r target_name target_url <<< "$target_config"
	        echo "Target: ${target_name}"
            clear_postgres_backup_databases "$target_url" "$source_db" "$dry_run" || cleanup_failed=1
            echo ""
        done

        return $cleanup_failed
    fi
    
    # Show configuration and sync
    echo ""
    echo "🐘 PostgreSQL Sync"
    echo "=================="
    echo "  From: ${source_name} → Database: ${source_db}"
    echo "  To: ${dest_name}"
    echo ""
    
    perform_postgres_sync "$source_url" "$source_db" "$dest_url" "false" "$verbose_mode"
}

db_tools_sync_mongodb() {
    # Load secrets first to ensure configuration arrays are available
    load_secrets
    
    local source_idx=""
    local db_idx=""
    local dest_idx=""
    local verbose_mode="false"
    
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --source|-s)
                source_idx="$2"
                shift 2
                ;;
            --database|-db)
                db_idx="$2"
                shift 2
                ;;
            --destination|-d)
                dest_idx="$2"
                shift 2
                ;;
            --verbose|-v)
                verbose_mode="true"
                shift
                ;;
            *)
                echo "❌ Unknown option: $1"
                echo "💡 Use: dbt sync mongodb --source <idx> --destination <idx> [--verbose]"
                return 1
                ;;
        esac
    done
    
    # Validate required flags
    if [[ -z "$source_idx" ]] || [[ -z "$dest_idx" ]]; then
        echo "❌ Error: MongoDB sync requires --source and --destination flags"
        echo "   Use 'dbt list' to list available configurations"
        return 1
    fi
    
    # Default database index to 1 if not specified
    db_idx=${db_idx:-1}
    
    # Validate source
    local source_config=$(get_mongodb_source_config "$source_idx")
    if [[ -z "$source_config" ]]; then
        echo "❌ Error: Invalid MongoDB source index: $source_idx"
        echo "   Available sources: 1-${#MONGO_SOURCES[@]}"
        return 1
    fi
    
    # Validate destination
    local dest_config=$(get_mongodb_target_config "$dest_idx")
    if [[ -z "$dest_config" ]]; then
        echo "❌ Error: Invalid MongoDB destination index: $dest_idx"
        echo "   Available destinations: 1-${#MONGO_TARGETS[@]}"
        return 1
    fi
    
    # Get database
    local source_db=$(get_mongodb_source_database "$source_idx" "$db_idx")
    if [[ -z "$source_db" ]]; then
        echo "❌ Error: Invalid database index: $db_idx"
        return 1
    fi
    
    # Parse configs
    IFS='|' read -r source_name source_uri _ <<< "$source_config"
    IFS='|' read -r dest_name dest_uri <<< "$dest_config"
    
    # Show configuration and sync
    echo ""
    echo "🍃 MongoDB Sync"
    echo "==============="
    echo "  From: ${source_name} → Database: ${source_db}"
    echo "  To: ${dest_name} → Database: ${source_db}"
    echo ""
    
    perform_mongo_sync "$source_uri" "$source_db" "${source_name}" "$dest_uri" "${dest_name}" "false" "$verbose_mode"
}

# Filter function to remove debug output lines
filter_debug() {
    grep -Ev '^(vps_name|user|ip|service_name|local_port|target_host|target_port|_vps_name|_user|_ip|_service_name|_local_port|_target_host|_target_port)='
}
