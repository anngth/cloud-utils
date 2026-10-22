# Helper function to extract hostname from PostgreSQL URL
extract_postgres_host() {
    local url="$1"
    # Remove postgresql:// prefix
    local url_no_prefix=$(echo "$url" | sed 's/postgresql:\/\///')
    # Extract host from URL (between @ and :port)
    local host_port=$(echo "$url_no_prefix" | rev | cut -d@ -f1 | rev)
    local host=$(echo "$host_port" | cut -d: -f1)
    echo "$host"
}

# Function to get PostgreSQL source configuration by index
get_postgres_source_config() {
    local index="$1"
    local array_length=${#POSTGRES_SOURCES[@]}

    # Convert to integer for comparison
    index=$((index + 0))

    if [ $index -ge 1 ] && [ $index -le $array_length ]; then
        echo "${POSTGRES_SOURCES[$index]}"
    else
        echo ""
    fi
}

# Function to get PostgreSQL target configuration by index
get_postgres_target_config() {
    local index="$1"
    local array_length=${#POSTGRES_TARGETS[@]}
    
    # Convert to integer for comparison
    index=$((index + 0))
    
    if [ $index -ge 1 ] && [ $index -le $array_length ]; then
        echo "${POSTGRES_TARGETS[$index]}"
    else
        echo ""
    fi
}

# Function to get PostgreSQL database from source by index
get_postgres_source_database() {
    local source_index="$1"
    local source_config=$(get_postgres_source_config "$source_index")

    if [ -z "$source_config" ]; then
        echo ""
        return 1
    fi

    IFS='|' read -r name url database <<< "$source_config"
    echo "${database}"
}

# Helper function to parse PostgreSQL URL components
_parse_postgres_url() {
    local url="$1"
    local prefix="$2"  # 'src' or 'tgt'
    
    # Remove postgresql:// prefix
    local url_no_prefix="${url#postgresql://}"
    
    # Split by the last @ (the one before hostname)
    local auth_part="${url_no_prefix%@*}"
    local host_part="${url_no_prefix##*@}"
    
    # Extract user and password
    local user="${auth_part%%:*}"
    local pass="${auth_part#*:}"
    
    # Extract host, port, and database (handle query params)
    local host="${host_part%%:*}"
    local port_and_db="${host_part#*:}"
    local port="${port_and_db%%[/?]*}"
    
    # Extract database name (after / and before ?)
    local db_part="${port_and_db#*/}"
    local database="${db_part%%[?]*}"
    # If no database in URL, default to postgres
    [[ -z "$database" ]] && database="postgres"

    # Names are chosen by callers. Values are data and must not be evaluated.
    [[ "$prefix" =~ '^[A-Za-z_][A-Za-z0-9_]*$' ]] || return 1
    typeset -g "${prefix}_user"="$user"
    typeset -g "${prefix}_pass"="$pass"
    typeset -g "${prefix}_host"="$host"
    typeset -g "${prefix}_port"="$port"
    typeset -g "${prefix}_database"="$database"
}

# Helper function to execute psql command with optional SSL
_exec_psql() {
    local ssl_mode="$1"
    shift
    
    if [[ "${ssl_mode}" == "require" ]]; then
        env PGSSLMODE=require "$@"
    else
        "$@"
    fi
}

# Helper function to extract PostgreSQL client major version
_get_pg_client_major_version() {
    local pg_bin="$1"
    "$pg_bin" --version 2>/dev/null | sed -nE 's/.*PostgreSQL\)?[[:space:]]+([0-9]+).*/\1/p' | head -1
}

# Function to find PostgreSQL client tools
_find_postgres_client_tools() {
    local psql_bin=""
    local pg_dump_bin=""
    local pg_restore_bin=""
    local pg_version=""
    local source_label=""

    # 1. Prefer explicit PATH. This lets ~/.zshrc choose between libpq and postgresql@18.
    psql_bin=$(command -v psql 2>/dev/null)
    pg_dump_bin=$(command -v pg_dump 2>/dev/null)
    pg_restore_bin=$(command -v pg_restore 2>/dev/null)
    if [[ -n "$psql_bin" && -n "$pg_dump_bin" && -n "$pg_restore_bin" ]]; then
        pg_version=$(_get_pg_client_major_version "$pg_dump_bin")
        source_label="PATH"
        echo "${pg_version:-unknown}|$psql_bin|$pg_dump_bin|$pg_restore_bin|$source_label"
        return 0
    fi

    # 2. Homebrew libpq provides client-only tools without a local PostgreSQL server.
    if [[ -x "/opt/homebrew/opt/libpq/bin/psql" && \
          -x "/opt/homebrew/opt/libpq/bin/pg_dump" && \
          -x "/opt/homebrew/opt/libpq/bin/pg_restore" ]]; then
        psql_bin="/opt/homebrew/opt/libpq/bin/psql"
        pg_dump_bin="/opt/homebrew/opt/libpq/bin/pg_dump"
        pg_restore_bin="/opt/homebrew/opt/libpq/bin/pg_restore"
        pg_version=$(_get_pg_client_major_version "$pg_dump_bin")
        source_label="Homebrew libpq"
        echo "${pg_version:-unknown}|$psql_bin|$pg_dump_bin|$pg_restore_bin|$source_label"
        return 0
    fi

    # 3. Fallback to Homebrew PostgreSQL 18 full formula.
    if [[ -x "/opt/homebrew/opt/postgresql@18/bin/psql" && \
          -x "/opt/homebrew/opt/postgresql@18/bin/pg_dump" && \
          -x "/opt/homebrew/opt/postgresql@18/bin/pg_restore" ]]; then
        psql_bin="/opt/homebrew/opt/postgresql@18/bin/psql"
        pg_dump_bin="/opt/homebrew/opt/postgresql@18/bin/pg_dump"
        pg_restore_bin="/opt/homebrew/opt/postgresql@18/bin/pg_restore"
        pg_version=$(_get_pg_client_major_version "$pg_dump_bin")
        source_label="Homebrew postgresql@18"
        echo "${pg_version:-18}|$psql_bin|$pg_dump_bin|$pg_restore_bin|$source_label"
        return 0
    fi

    echo ""
}

_pg_sql_literal() {
    local value="$1"
    value="${value//\'/\'\'}"
    echo "'${value}'"
}

_pg_identifier() {
    local value="$1"
    value="${value//\"/\"\"}"
    echo "\"${value}\""
}

clear_postgres_backup_databases() {
    local tgt_url="$1"
    local source_db="${2:-}"
    local dry_run="${3:-false}"

    _parse_postgres_url "$tgt_url" "tgt"

    local tgt_sslmode="disable"
    [[ "$tgt_url" =~ sslmode=require ]] && tgt_sslmode="require"

    local pg_info=$(_find_postgres_client_tools)
    if [[ -z "$pg_info" ]]; then
        echo "❌ Error: PostgreSQL client tools not found"
        echo "   Install client-only tools: brew install libpq"
        echo "   Or install PostgreSQL 18: brew install postgresql@18"
        return 1
    fi

    local psql_bin=$(echo "$pg_info" | cut -d'|' -f2)
    local backup_regex_literal=$(_pg_sql_literal '_backup_[0-9]{8}_[0-9]{6}$')
    local backup_prefix=""
    local backup_prefix_literal=""
    local query=""

    query="SELECT datname FROM pg_database WHERE datistemplate = false AND datname ~ ${backup_regex_literal}"

    if [[ -n "$source_db" ]]; then
        backup_prefix="${source_db}_backup_"
        backup_prefix_literal=$(_pg_sql_literal "$backup_prefix")
        query+=" AND left(datname, length(${backup_prefix_literal})) = ${backup_prefix_literal}"
    fi

    query+=" ORDER BY datname;"

    local list_error_file=$(mktemp)
    local backups
    backups=$(
        _exec_psql "$tgt_sslmode" \
            env PGPASSWORD="${tgt_pass}" "$psql_bin" \
            -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d postgres \
            -At -c "$query" \
            2>"$list_error_file"
    )
    local list_result=$?

    if [[ $list_result -ne 0 ]]; then
        echo "❌ Failed to list PostgreSQL backup databases"
        if [[ -s "$list_error_file" ]]; then
            local list_err=$(grep -i "error\|failed\|permission denied\|could not\|fatal" "$list_error_file" | head -n 1)
            [[ -z "$list_err" ]] && list_err=$(grep -v "^$" "$list_error_file" | head -n 1)
            [[ -n "$list_err" ]] && echo "   Error: $list_err"
        fi
        rm -f "$list_error_file"
        return 1
    fi
    rm -f "$list_error_file"

    if [[ -z "$backups" ]]; then
        echo "✅ No PostgreSQL backup databases found"
        return 0
    fi

    echo "Found backup databases:"
    while IFS= read -r backup_db; do
        [[ -n "$backup_db" ]] && echo "  - $backup_db"
    done <<< "$backups"
    echo ""

    if [[ "$dry_run" == "true" ]]; then
        echo "ℹ️  Dry run only; no databases were dropped"
        return 0
    fi

    local dropped=0
    local failed=0

    while IFS= read -r backup_db; do
        [[ -z "$backup_db" ]] && continue

        local backup_db_literal=$(_pg_sql_literal "$backup_db")
        local backup_db_identifier=$(_pg_identifier "$backup_db")

        _exec_psql "$tgt_sslmode" \
            env PGPASSWORD="${tgt_pass}" "$psql_bin" \
            -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d postgres \
            -t -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = ${backup_db_literal} AND pid <> pg_backend_pid();" \
            >/dev/null 2>&1 || true

        if _exec_psql "$tgt_sslmode" \
            env PGPASSWORD="${tgt_pass}" "$psql_bin" \
            -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d postgres \
            -c "DROP DATABASE IF EXISTS ${backup_db_identifier};" \
            >/dev/null 2>&1; then
            echo "✅ Dropped: $backup_db"
            ((dropped++))
        else
            echo "❌ Failed to drop: $backup_db"
            ((failed++))
        fi
    done <<< "$backups"

    echo ""
    echo "Summary: dropped ${dropped}, failed ${failed}"

    [[ "$failed" -eq 0 ]]
}

_dbt_postgres_clear_interrupt_state() {
    unset DBT_PG_BACKUP_DB DBT_PG_TARGET_DB DBT_PG_TARGET_HOST DBT_PG_TARGET_PORT DBT_PG_TARGET_USER
    unset DBT_PG_TARGET_PASS DBT_PG_SSLMODE DBT_PG_PSQL_BIN DBT_PG_RESTORE_BIN DBT_PG_SPINNER_PID
    unset DBT_PG_BACKUP_FILE
}

_restore_postgres_dump_backup() {
    local backup_file="$1"
    local target_db="$2"
    local tgt_sslmode="$3"
    local tgt_pass="$4"
    local pg_restore_bin="$5"
    local psql_bin="$6"
    local tgt_host="$7"
    local tgt_port="$8"
    local tgt_user="$9"
    local restore_error_file="${10}"

    _exec_psql "$tgt_sslmode" \
        env PGPASSWORD="${tgt_pass}" "$psql_bin" \
        -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d "${target_db}" \
        -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO postgres; GRANT ALL ON SCHEMA public TO public;" \
        >/dev/null 2>"$restore_error_file" || return 1

    _exec_psql "$tgt_sslmode" \
        env PGPASSWORD="${tgt_pass}" "$pg_restore_bin" \
        -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d "${target_db}" \
        --clean --if-exists --no-owner --no-acl \
        "$backup_file" \
        >>"$restore_error_file" 2>&1
}

_dbt_postgres_interrupt_restore() {
    trap - INT TERM

    echo ""
    echo "⚠️  PostgreSQL sync interrupted"

    if [[ -n "$DBT_PG_SPINNER_PID" ]]; then
        sync_dots_stop "$DBT_PG_SPINNER_PID"
        echo ""
    fi

    if [[ -n "$DBT_PG_BACKUP_DB" && -n "$DBT_PG_TARGET_DB" ]]; then
        echo "🔄 Restoring backup database '${DBT_PG_BACKUP_DB}'..."
        _exec_psql "$DBT_PG_SSLMODE" \
            env PGPASSWORD="${DBT_PG_TARGET_PASS}" "$DBT_PG_PSQL_BIN" \
            -h "${DBT_PG_TARGET_HOST}" -p "${DBT_PG_TARGET_PORT}" -U "${DBT_PG_TARGET_USER}" -d postgres \
            -c "DROP DATABASE IF EXISTS \"${DBT_PG_TARGET_DB}\";" \
            >/dev/null 2>&1 || true

        local restore_backup_error=$(
            _exec_psql "$DBT_PG_SSLMODE" \
                env PGPASSWORD="${DBT_PG_TARGET_PASS}" "$DBT_PG_PSQL_BIN" \
                -h "${DBT_PG_TARGET_HOST}" -p "${DBT_PG_TARGET_PORT}" -U "${DBT_PG_TARGET_USER}" -d postgres \
                -c "ALTER DATABASE \"${DBT_PG_BACKUP_DB}\" RENAME TO \"${DBT_PG_TARGET_DB}\";" \
                2>&1
        )
        if [ $? -eq 0 ]; then
            echo "✅ Backup database restored successfully"
        else
            echo "⚠️  Failed to restore backup. Original database is available as '${DBT_PG_BACKUP_DB}'"
            if [[ -n "$restore_backup_error" ]]; then
                echo "   Error: $(echo "$restore_backup_error" | head -n 1)"
            fi
        fi
    elif [[ -n "$DBT_PG_BACKUP_FILE" && -f "$DBT_PG_BACKUP_FILE" && -n "$DBT_PG_TARGET_DB" ]]; then
        echo "🔄 Restoring dump backup..."
        local restore_backup_error_file=$(mktemp)
        if _restore_postgres_dump_backup "$DBT_PG_BACKUP_FILE" "$DBT_PG_TARGET_DB" "$DBT_PG_SSLMODE" "$DBT_PG_TARGET_PASS" "$DBT_PG_RESTORE_BIN" "$DBT_PG_PSQL_BIN" "$DBT_PG_TARGET_HOST" "$DBT_PG_TARGET_PORT" "$DBT_PG_TARGET_USER" "$restore_backup_error_file"; then
            echo "✅ Dump backup restored successfully"
            rm -f "$DBT_PG_BACKUP_FILE"
        else
            echo "⚠️  Failed to restore dump backup. Backup file is still available: $DBT_PG_BACKUP_FILE"
            if [[ -s "$restore_backup_error_file" ]]; then
                local restore_err=$(grep -i "error\|failed\|permission denied\|fatal" "$restore_backup_error_file" | head -n 1)
                [[ -n "$restore_err" ]] && echo "   Error: $restore_err"
            fi
        fi
        rm -f "$restore_backup_error_file"
    fi

    _dbt_postgres_clear_interrupt_state
    return 130 2>/dev/null || exit 130
}

# Function to perform PostgreSQL sync
perform_postgres_sync() {
    setopt local_options pipe_fail

    local src_url="$1"
    local src_db="$2"
    local tgt_url="$3"
    local quiet_mode="${4:-false}"   # Optional 4th parameter for quiet mode
    local verbose_mode="${5:-false}" # Optional 5th parameter for verbose mode
    
    # Parse URLs
    _parse_postgres_url "$src_url" "src"
    _parse_postgres_url "$tgt_url" "tgt"
    
    # Determine SSL mode
    local tgt_sslmode="disable"
    [[ "$tgt_url" =~ sslmode=require ]] && tgt_sslmode="require"
    
    # Check if target is Supabase (by hostname or database name)
    local is_supabase=false
    if echo "${tgt_host}" | grep -qiE "(supabase|supabase\.co)"; then
        is_supabase=true
    fi
    
    # Determine target database name
    # For Supabase or if target database is "postgres", use "postgres" as target
    # Otherwise, use source database name
    local tgt_db_name="${src_db}"
    if [[ "$is_supabase" == "true" ]] || [[ "${tgt_database}" == "postgres" ]]; then
        tgt_db_name="postgres"
        if [[ "$quiet_mode" != "true" ]]; then
            echo "ℹ️  Target is Supabase or uses 'postgres' database - will restore to 'postgres'"
        fi
    fi
    
    # Use PostgreSQL client tools from PATH, Homebrew libpq, or Homebrew postgresql@18.
    local pg_info=$(_find_postgres_client_tools)
    
    if [[ -z "$pg_info" ]]; then
        echo "❌ Error: PostgreSQL client tools not found"
        echo "   Install client-only tools: brew install libpq"
        echo "   Or install PostgreSQL 18: brew install postgresql@18"
        echo "   Then ensure psql, pg_dump, and pg_restore are available on PATH"
        return 1
    fi
    
    # Extract version and paths
    local pg_version=$(echo "$pg_info" | cut -d'|' -f1)
    local psql_bin=$(echo "$pg_info" | cut -d'|' -f2)
    local pg_dump_bin=$(echo "$pg_info" | cut -d'|' -f3)
    local pg_restore_bin=$(echo "$pg_info" | cut -d'|' -f4)
    local pg_client_source=$(echo "$pg_info" | cut -d'|' -f5)
    
    # Extra args for verbose mode
    local pg_dump_args=()
    local pg_restore_args=()
    local psql_restore_args=()
    
    if [[ "$verbose_mode" == "true" ]]; then
        pg_dump_args+=("-v")
        pg_restore_args+=("--verbose")
    fi
    
    # Only show header in non-quiet mode
    if [[ "$quiet_mode" != "true" ]]; then
        echo "====================================="
        echo "PostgreSQL Sync: ${src_db}"
        echo "Source: ${src_user}@${src_host}:${src_port}"
        echo "Target: ${tgt_user}@${tgt_host}:${tgt_port}"
        echo "Client: PostgreSQL ${pg_version} (${pg_client_source})"
        echo "-------------------------------------"
    fi
    
    # For Supabase or postgres database, skip drop/create and restore directly
    local backup_db_name=""
    local backup_file=""
    if [[ "$is_supabase" != "true" ]] && [[ "$tgt_db_name" != "postgres" ]]; then
        # Standard PostgreSQL: safely backup existing database before sync
        # Check if target database exists
        local db_exists=$(_exec_psql "$tgt_sslmode" \
            env PGPASSWORD="${tgt_pass}" "$psql_bin" \
            -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d postgres \
            -t -c "SELECT 1 FROM pg_database WHERE datname='${tgt_db_name}';" \
            2>/dev/null | tr -d ' ')
        
        if [[ -n "$db_exists" ]] && [[ "$db_exists" == "1" ]]; then
            # Database exists - rename it to backup
            local timestamp=$(date +%Y%m%d_%H%M%S)
            backup_db_name="${tgt_db_name}_backup_${timestamp}"
            
            if [[ "$quiet_mode" != "true" ]]; then
                echo "💾 Backing up existing database '${tgt_db_name}' to '${backup_db_name}'..."
            fi
            
            # Terminate connections to target database
            _exec_psql "$tgt_sslmode" \
                env PGPASSWORD="${tgt_pass}" "$psql_bin" \
                -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d postgres \
                -t -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${tgt_db_name}' AND pid <> pg_backend_pid();" \
                >/dev/null 2>&1 || true
            
            # Rename existing database to backup
            local rename_error=$(
                _exec_psql "$tgt_sslmode" \
                    env PGPASSWORD="${tgt_pass}" "$psql_bin" \
                    -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d postgres \
                    -c "ALTER DATABASE \"${tgt_db_name}\" RENAME TO \"${backup_db_name}\";" \
                    2>&1
            )
            
            if [ $? -ne 0 ]; then
                echo "❌ Failed to backup existing database '${tgt_db_name}'"
                if [[ -n "$rename_error" ]]; then
                    echo "   Error: $(echo "$rename_error" | head -n 1)"
                fi
                trap - INT TERM
                _dbt_postgres_clear_interrupt_state
                return 1
            fi

            typeset -g DBT_PG_BACKUP_DB="$backup_db_name"
            typeset -g DBT_PG_BACKUP_FILE=""
            typeset -g DBT_PG_TARGET_DB="$tgt_db_name"
            typeset -g DBT_PG_TARGET_HOST="$tgt_host"
            typeset -g DBT_PG_TARGET_PORT="$tgt_port"
            typeset -g DBT_PG_TARGET_USER="$tgt_user"
            typeset -g DBT_PG_TARGET_PASS="$tgt_pass"
            typeset -g DBT_PG_SSLMODE="$tgt_sslmode"
            typeset -g DBT_PG_PSQL_BIN="$psql_bin"
            typeset -g DBT_PG_RESTORE_BIN="$pg_restore_bin"
            typeset -g DBT_PG_SPINNER_PID=""
            trap _dbt_postgres_interrupt_restore INT TERM
        fi
        
        # Create fresh target database
        if [[ "$quiet_mode" != "true" ]]; then
            echo "🆕 Creating fresh target database '${tgt_db_name}'..."
        fi
        local create_error=$(
            _exec_psql "$tgt_sslmode" \
                env PGPASSWORD="${tgt_pass}" "$psql_bin" \
                -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d postgres \
                -c "CREATE DATABASE \"${tgt_db_name}\";" \
                2>&1
        )
        
        if [ $? -ne 0 ]; then
            echo "❌ Failed to create database '${tgt_db_name}'"
            if [[ -n "$create_error" ]]; then
                echo "   Error: $(echo "$create_error" | head -n 1)"
            fi
            # Try to restore backup if creation failed
            if [[ -n "$backup_db_name" ]]; then
                echo "🔄 Attempting to restore backup..."
                _exec_psql "$tgt_sslmode" \
                    env PGPASSWORD="${tgt_pass}" "$psql_bin" \
                    -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d postgres \
                    -c "ALTER DATABASE \"${backup_db_name}\" RENAME TO \"${tgt_db_name}\";" \
                    >/dev/null 2>&1 || true
            fi
            trap - INT TERM
            _dbt_postgres_clear_interrupt_state
            return 1
        fi
    else
        # Supabase or postgres database: just clean existing schema objects
        if [[ "$quiet_mode" != "true" ]]; then
            echo "💾 Backing up target database '${tgt_db_name}' before schema cleanup..."
        fi

        backup_file=$(mktemp)
        local backup_error_file=$(mktemp)
        env PGPASSWORD="${tgt_pass}" "$pg_dump_bin" "${pg_dump_args[@]}" \
            -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d "${tgt_db_name}" \
            -Fc --no-owner --no-acl \
            -f "$backup_file" \
            2>"$backup_error_file"
        local backup_result=$?

        if [[ $backup_result -ne 0 ]]; then
            echo "❌ Failed to backup target database before cleanup"
            if [[ -s "$backup_error_file" ]]; then
                local backup_err=$(grep -i "error\|failed\|permission denied\|fatal" "$backup_error_file" | head -n 1)
                [[ -z "$backup_err" ]] && backup_err=$(grep -v "^$" "$backup_error_file" | head -n 1)
                [[ -n "$backup_err" ]] && echo "   Error: $backup_err"
            fi
            rm -f "$backup_file" "$backup_error_file"
            return 1
        fi
        rm -f "$backup_error_file"

        typeset -g DBT_PG_BACKUP_DB=""
        typeset -g DBT_PG_BACKUP_FILE="$backup_file"
        typeset -g DBT_PG_TARGET_DB="$tgt_db_name"
        typeset -g DBT_PG_TARGET_HOST="$tgt_host"
        typeset -g DBT_PG_TARGET_PORT="$tgt_port"
        typeset -g DBT_PG_TARGET_USER="$tgt_user"
        typeset -g DBT_PG_TARGET_PASS="$tgt_pass"
        typeset -g DBT_PG_SSLMODE="$tgt_sslmode"
        typeset -g DBT_PG_PSQL_BIN="$psql_bin"
        typeset -g DBT_PG_RESTORE_BIN="$pg_restore_bin"
        typeset -g DBT_PG_SPINNER_PID=""
        trap _dbt_postgres_interrupt_restore INT TERM

        if [[ "$quiet_mode" != "true" ]]; then
            echo "✅ Backup file created"
            echo "🧹 Cleaning existing objects in target database (Supabase/postgres)..."
        fi
        # Drop all objects in public schema (Supabase uses public schema)
        local cleanup_error_file=$(mktemp)
        _exec_psql "$tgt_sslmode" \
            env PGPASSWORD="${tgt_pass}" "$psql_bin" \
            -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d "${tgt_db_name}" \
            -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO postgres; GRANT ALL ON SCHEMA public TO public;" \
            >/dev/null 2>"$cleanup_error_file"
        local cleanup_result=$?

        if [[ $cleanup_result -ne 0 ]]; then
            echo "❌ Failed to clean target database before restore"
            if [[ -s "$cleanup_error_file" ]]; then
                local cleanup_err=$(grep -i "error\|failed\|permission denied\|fatal" "$cleanup_error_file" | head -n 1)
                [[ -z "$cleanup_err" ]] && cleanup_err=$(grep -v "^$" "$cleanup_error_file" | head -n 1)
                [[ -n "$cleanup_err" ]] && echo "   Error: $cleanup_err"
            fi
            echo "🔄 Restoring dump backup..."
            local cleanup_restore_error_file=$(mktemp)
            if _restore_postgres_dump_backup "$backup_file" "$tgt_db_name" "$tgt_sslmode" "$tgt_pass" "$pg_restore_bin" "$psql_bin" "$tgt_host" "$tgt_port" "$tgt_user" "$cleanup_restore_error_file"; then
                echo "✅ Dump backup restored successfully"
                rm -f "$backup_file"
            else
                echo "⚠️  Failed to restore dump backup. Backup file is still available: ${backup_file}"
                if [[ -s "$cleanup_restore_error_file" ]]; then
                    local restore_err=$(grep -i "error\|failed\|permission denied\|fatal" "$cleanup_restore_error_file" | head -n 1)
                    [[ -n "$restore_err" ]] && echo "   Error: $restore_err"
                fi
            fi
            rm -f "$cleanup_error_file" "$cleanup_restore_error_file"
            trap - INT TERM
            _dbt_postgres_clear_interrupt_state
            return 1
        fi
        rm -f "$cleanup_error_file"
    fi

    typeset -g DBT_PG_BACKUP_DB="$backup_db_name"
    typeset -g DBT_PG_BACKUP_FILE="$backup_file"
    typeset -g DBT_PG_TARGET_DB="$tgt_db_name"
    typeset -g DBT_PG_TARGET_HOST="$tgt_host"
    typeset -g DBT_PG_TARGET_PORT="$tgt_port"
    typeset -g DBT_PG_TARGET_USER="$tgt_user"
    typeset -g DBT_PG_TARGET_PASS="$tgt_pass"
    typeset -g DBT_PG_SSLMODE="$tgt_sslmode"
    typeset -g DBT_PG_PSQL_BIN="$psql_bin"
    typeset -g DBT_PG_RESTORE_BIN="$pg_restore_bin"
    typeset -g DBT_PG_SPINNER_PID=""
    trap _dbt_postgres_interrupt_restore INT TERM
    
    if [[ "$quiet_mode" != "true" ]]; then
        echo "🔍 Detecting database versions..."
    fi
    
    # Detect source version
    local src_version=$(env PGPASSWORD="${src_pass}" "$psql_bin" -h "${src_host}" -p "${src_port}" -U "${src_user}" -d postgres -t -c "SELECT version();" 2>/dev/null | grep -oE "PostgreSQL [0-9]+" | grep -oE "[0-9]+" | head -1)
    if [[ "$quiet_mode" != "true" ]] && [[ -n "$src_version" ]]; then
        echo "   Source version: PostgreSQL ${src_version}"
    fi
    
    # Detect target version
    local tgt_version=$(env PGPASSWORD="${tgt_pass}" "$psql_bin" -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d postgres -t -c "SELECT version();" 2>/dev/null | grep -oE "PostgreSQL [0-9]+" | grep -oE "[0-9]+" | head -1)
    if [[ "$quiet_mode" != "true" ]] && [[ -n "$tgt_version" ]]; then
        echo "   Target version: PostgreSQL ${tgt_version}"
    fi
    
    local sync_dots_pid=""
    if [[ "$quiet_mode" != "true" ]] && [[ "$verbose_mode" != "true" ]]; then
        sync_dots_start "📤 Syncing database" sync_dots_pid
        DBT_PG_SPINNER_PID="$sync_dots_pid"
    else
        [[ "$quiet_mode" != "true" ]] && echo "📤 Syncing database"
    fi
    
    # Capture errors from both dump and restore
    local dump_error_file=$(mktemp)
    local restore_error_file=$(mktemp)
    local dump_output_file=$(mktemp)
    
    # If target is PostgreSQL 16 or earlier, use SQL format and filter incompatible parameters
    # Otherwise use custom format for better performance
    local dump_result=0
    local restore_result=0
    local sync_result=0
    
    if [[ -n "$tgt_version" ]] && [[ "$tgt_version" -le 16 ]]; then
        # Use SQL format and filter out PostgreSQL 17+ specific parameters
        env PGPASSWORD="${src_pass}" "$pg_dump_bin" "${pg_dump_args[@]}" \
            -h "${src_host}" -p "${src_port}" -U "${src_user}" -d "${src_db}" \
            --no-owner --no-acl --clean --if-exists 2> >(tee "$dump_error_file" >&2) \
        | grep -v "transaction_timeout" \
        | grep -v "ALTER DATABASE.*SET transaction_timeout" \
        > "$dump_output_file"
        dump_result=$?
        
        if [[ $dump_result -eq 0 ]]; then
            # Build psql restore args
            psql_restore_args=( \
                -h "${tgt_host}" \
                -p "${tgt_port}" \
                -U "${tgt_user}" \
                -d "${tgt_db_name}" \
            )
            if [[ "$verbose_mode" != "true" ]]; then
                psql_restore_args+=("-q")
            fi
            _exec_psql "$tgt_sslmode" \
                env PGPASSWORD="${tgt_pass}" "$psql_bin" "${psql_restore_args[@]}" \
                < "$dump_output_file" \
                2> >(tee "$restore_error_file" >&2) >/dev/null
            restore_result=$?
        fi
        sync_result=$((dump_result | restore_result))
    else
        # Use custom format for PostgreSQL 17+ targets
        env PGPASSWORD="${src_pass}" "$pg_dump_bin" "${pg_dump_args[@]}" \
            -h "${src_host}" -p "${src_port}" -U "${src_user}" -d "${src_db}" -Fc \
            -f "$dump_output_file" \
            2> >(tee "$dump_error_file" >&2)
        dump_result=$?
        
        if [[ $dump_result -eq 0 ]]; then
            # Build pg_restore args
            pg_restore_args+=( \
                -h "${tgt_host}" \
                -p "${tgt_port}" \
                -U "${tgt_user}" \
                -d "${tgt_db_name}" \
                --clean --if-exists --no-owner --no-acl \
            )
            _exec_psql "$tgt_sslmode" \
                env PGPASSWORD="${tgt_pass}" "$pg_restore_bin" "${pg_restore_args[@]}" \
                "$dump_output_file" \
                2> >(tee "$restore_error_file" >&2) >/dev/null
            restore_result=$?
        fi
        sync_result=$((dump_result | restore_result))
    fi
    
    # Clean up dump output file
    rm -f "$dump_output_file"
    
    if [[ -n "$sync_dots_pid" ]]; then
        sync_dots_stop "$sync_dots_pid"
        DBT_PG_SPINNER_PID=""
        echo ""
    fi
    trap - INT TERM
    
    # Check for warnings even if exit code is 0 (common with Supabase)
    local has_warnings=false
    if [[ -s "$restore_error_file" ]]; then
        # Check for critical warnings/errors
        if grep -qiE "(error|failed|permission denied|does not exist|cannot)" "$restore_error_file"; then
            has_warnings=true
        fi
    fi
    
    # Verify data was actually restored (important for Supabase)
    local verification_info=""
    if [ $sync_result -eq 0 ]; then
        if [[ "$quiet_mode" != "true" ]]; then
            echo "🔍 Verifying restored data..."
        fi
        
        # Get table count and schemas
        local table_count=$(_exec_psql "$tgt_sslmode" \
            env PGPASSWORD="${tgt_pass}" "$psql_bin" \
            -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d "${tgt_db_name}" \
            -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema');" \
            2>/dev/null | tr -d ' ')
        
        # Get schemas list
        local schemas=$(_exec_psql "$tgt_sslmode" \
            env PGPASSWORD="${tgt_pass}" "$psql_bin" \
            -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d "${tgt_db_name}" \
            -t -c "SELECT string_agg(nspname, ', ' ORDER BY nspname) FROM pg_namespace WHERE nspname NOT IN ('pg_catalog', 'pg_toast', 'information_schema', 'pg_toast_temp_1');" \
            2>/dev/null | tr -d ' ')
        
        # Get total row count (sample from a few tables)
        local sample_tables=$(_exec_psql "$tgt_sslmode" \
            env PGPASSWORD="${tgt_pass}" "$psql_bin" \
            -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d "${tgt_db_name}" \
            -t -c "SELECT schemaname||'.'||tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema') LIMIT 5;" \
            2>/dev/null | tr -d ' ' | head -3)
        
        if [[ -n "$table_count" ]] && [[ "$table_count" != "0" ]]; then
            verification_info="   Tables: ${table_count}"
            [[ -n "$schemas" ]] && verification_info+=" | Schemas: ${schemas}"
        else
            has_warnings=true
            verification_info="   ⚠️  WARNING: No tables found in restored database!"
        fi
    fi
    
    if [ $sync_result -eq 0 ] && [[ "$has_warnings" != "true" ]]; then
        echo "✅ '${src_db}' synced successfully to '${tgt_db_name}'"
        if [[ -n "$verification_info" ]]; then
            echo "$verification_info"
        fi
        
        # Clean up backup database if sync was successful
        if [[ -n "$backup_db_name" ]]; then
            if [[ "$quiet_mode" != "true" ]]; then
                echo "🗑️  Removing backup database '${backup_db_name}'..."
            fi
            _exec_psql "$tgt_sslmode" \
                env PGPASSWORD="${tgt_pass}" "$psql_bin" \
                -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d postgres \
                -c "DROP DATABASE IF EXISTS \"${backup_db_name}\";" \
                >/dev/null 2>&1 || true
        fi
        if [[ -n "$backup_file" ]]; then
            rm -f "$backup_file"
        fi
        
        [[ "$quiet_mode" != "true" ]] && echo "====================================="
        rm -f "$dump_error_file" "$restore_error_file"
        _dbt_postgres_clear_interrupt_state
        return 0
    elif [ $sync_result -eq 0 ] && [[ "$has_warnings" == "true" ]]; then
        echo "⚠️  '${src_db}' sync completed with warnings"
        if [[ -n "$verification_info" ]]; then
            echo "$verification_info"
        fi
        echo "   Data may not be fully synced. Check errors below:"
        
        # Keep backup if sync had warnings
        if [[ -n "$backup_db_name" ]]; then
            echo ""
            echo "💾 Backup database '${backup_db_name}' kept for safety"
        fi
        if [[ -n "$backup_file" ]]; then
            echo ""
            echo "💾 Backup file kept for safety: ${backup_file}"
        fi

        if [[ -s "$restore_error_file" ]]; then
            echo "   Restore warnings/errors:"
            head -n 10 "$restore_error_file" | sed 's/^/      /'
            if [[ $(wc -l < "$restore_error_file") -gt 10 ]]; then
                echo "      ... (more warnings/errors in restore, use --verbose to see all)"
            fi
        fi

        [[ "$quiet_mode" != "true" ]] && echo "====================================="
        rm -f "$dump_error_file" "$restore_error_file"
        _dbt_postgres_clear_interrupt_state
        return 1
    else
        echo "❌ Sync failed for '${src_db}'"
        
        # Restore backup if sync failed
        if [[ -n "$backup_db_name" ]]; then
            echo ""
            echo "🔄 Restoring backup database..."
            # Drop failed database
            _exec_psql "$tgt_sslmode" \
                env PGPASSWORD="${tgt_pass}" "$psql_bin" \
                -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d postgres \
                -c "DROP DATABASE IF EXISTS \"${tgt_db_name}\";" \
                >/dev/null 2>&1 || true
            # Restore backup
            local restore_backup_error=$(
                _exec_psql "$tgt_sslmode" \
                    env PGPASSWORD="${tgt_pass}" "$psql_bin" \
                    -h "${tgt_host}" -p "${tgt_port}" -U "${tgt_user}" -d postgres \
                    -c "ALTER DATABASE \"${backup_db_name}\" RENAME TO \"${tgt_db_name}\";" \
                    2>&1
            )
            if [ $? -eq 0 ]; then
                echo "✅ Backup database restored successfully"
            else
                echo "⚠️  Failed to restore backup. Original database is available as '${backup_db_name}'"
                if [[ -n "$restore_backup_error" ]]; then
                    echo "   Error: $(echo "$restore_backup_error" | head -n 1)"
                fi
            fi
        elif [[ -n "$backup_file" && -f "$backup_file" ]]; then
            echo ""
            echo "🔄 Restoring dump backup..."
            local restore_backup_error_file=$(mktemp)
            if _restore_postgres_dump_backup "$backup_file" "$tgt_db_name" "$tgt_sslmode" "$tgt_pass" "$pg_restore_bin" "$psql_bin" "$tgt_host" "$tgt_port" "$tgt_user" "$restore_backup_error_file"; then
                echo "✅ Dump backup restored successfully"
                rm -f "$backup_file"
            else
                echo "⚠️  Failed to restore dump backup. Backup file is still available: ${backup_file}"
                if [[ -s "$restore_backup_error_file" ]]; then
                    local restore_err=$(grep -i "error\|failed\|permission denied\|fatal" "$restore_backup_error_file" | head -n 1)
                    [[ -n "$restore_err" ]] && echo "   Error: $restore_err"
                fi
            fi
            rm -f "$restore_backup_error_file"
        fi
        
        # Show dump errors if any
        if [[ -s "$dump_error_file" ]]; then
            echo "   Dump errors:"
            # Show first few error lines
            head -n 5 "$dump_error_file" | sed 's/^/      /'
            if [[ $(wc -l < "$dump_error_file") -gt 5 ]]; then
                echo "      ... (more errors in dump)"
            fi
        fi
        
        # Show restore errors if any
        if [[ -s "$restore_error_file" ]]; then
            echo "   Restore errors/warnings:"
            # Show all error lines (not just first 5) to help debug Supabase issues
            if [[ "$verbose_mode" == "true" ]] || [[ "$has_warnings" == "true" ]]; then
                cat "$restore_error_file" | sed 's/^/      /'
            else
                head -n 10 "$restore_error_file" | sed 's/^/      /'
                if [[ $(wc -l < "$restore_error_file") -gt 10 ]]; then
                    echo "      ... (more errors in restore, use --verbose to see all)"
                fi
            fi
        fi
        
        # If no errors in files but sync failed, show exit code
        if [[ ! -s "$dump_error_file" ]] && [[ ! -s "$restore_error_file" ]]; then
            echo "   Exit code: $sync_result"
            echo "   Check connection and permissions"
        fi
        
        [[ "$quiet_mode" != "true" ]] && echo "====================================="
        rm -f "$dump_error_file" "$restore_error_file"
        
        # If sync failed or had warnings, suggest using verbose mode
        if [[ "$verbose_mode" != "true" ]]; then
            echo ""
            echo "💡 Tip: Run with --verbose flag to see detailed output:"
            echo "   dbt sync postgres -s <source> -d <dest> --verbose"
        fi
        
        _dbt_postgres_clear_interrupt_state
        return 1
    fi
}
# Function to test PostgreSQL connection
test_postgres_connection() {
    local url="$1"
    local name="$2"
    local quiet="${3:-false}"
    
    [[ "$quiet" != "true" ]] && echo "🔍 Testing connection to ${name}..."
    
    # Parse URL to extract connection details
    _parse_postgres_url "$url" "test"
    
    # Determine SSL mode
    local ssl_mode="disable"
    [[ "$url" =~ sslmode=require ]] && ssl_mode="require"
    
    # Find PostgreSQL client
    local pg_info=$(_find_postgres_client_tools)
    if [[ -z "$pg_info" ]]; then
        # If PostgreSQL client not found, skip test but return success (can't test)
        [[ "$quiet" != "true" ]] && echo "⚠️  Cannot test connection (PostgreSQL client not found), proceeding anyway..."
        return 0
    fi
    
    local psql_bin=$(echo "$pg_info" | cut -d'|' -f2)
    
    # Test connection by running a simple query
    local conn_error_file=$(mktemp)
    
    if _exec_psql "$ssl_mode" \
        env PGPASSWORD="${test_pass}" "$psql_bin" \
        -h "${test_host}" -p "${test_port}" -U "${test_user}" -d postgres \
        -t -c "SELECT 1;" >/dev/null 2>"$conn_error_file"; then
        [[ "$quiet" != "true" ]] && echo "✅ Connection to ${name} successful"
        rm -f "$conn_error_file"
        return 0
    fi
    
    # Connection failed
    rm -f "$conn_error_file"
    return 1
}
