# Helper function to extract hostname from MongoDB URI
extract_mongo_host() {
    local uri="$1"
    # Handle mongodb:// and mongodb+srv:// formats
    local uri_no_prefix=$(echo "$uri" | sed 's|mongodb+srv://||' | sed 's|mongodb://||')
    # Extract host from URI (between @ and / or ?)
    local host=$(echo "$uri_no_prefix" | cut -d@ -f2 | cut -d/ -f1 | cut -d'?' -f1 | cut -d: -f1)
    echo "$host"
}
# Function to get MongoDB source configuration by index
get_mongodb_source_config() {
    local index="$1"
    local array_length=${#MONGO_SOURCES[@]}
    
    # Convert to integer for comparison
    index=$((index + 0))
    
    if [ $index -ge 1 ] && [ $index -le $array_length ]; then
        echo "${MONGO_SOURCES[$index]}"
    else
        echo ""
    fi
}

# Function to get MongoDB target configuration by index
get_mongodb_target_config() {
    local index="$1"
    local array_length=${#MONGO_TARGETS[@]}
    
    # Convert to integer for comparison
    index=$((index + 0))
    
    if [ $index -ge 1 ] && [ $index -le $array_length ]; then
        echo "${MONGO_TARGETS[$index]}"
    else
        echo ""
    fi
}

# Function to get database from MongoDB source by index
get_mongodb_source_database() {
    local source_index="$1"
    local db_index="$2"
    local source_config=$(get_mongodb_source_config "$source_index")
    
    if [ -z "$source_config" ]; then
        echo ""
        return 1
    fi
    
    IFS='|' read -r name uri databases <<< "$source_config"
    IFS=',' read -A db_array <<< "$databases"
    local array_length=${#db_array[@]}
    
    if [ "$db_index" -ge 1 ] && [ "$db_index" -le "$array_length" ]; then
        echo "${db_array[$db_index]}"
    else
        echo ""
    fi
}

_mongo_client_cmd() {
    if command -v mongosh >/dev/null 2>&1; then
        echo "mongosh"
    elif command -v mongo >/dev/null 2>&1; then
        echo "mongo"
    else
        echo ""
    fi
}

_find_mongo_tools() {
    local mongo_client=$(_mongo_client_cmd)
    local mongodump_bin=$(command -v mongodump 2>/dev/null)
    local mongorestore_bin=$(command -v mongorestore 2>/dev/null)

    if [[ -n "$mongo_client" && -n "$mongodump_bin" && -n "$mongorestore_bin" ]]; then
        echo "$mongo_client|$mongodump_bin|$mongorestore_bin"
        return 0
    fi

    echo ""
}

_mongo_database_exists() {
    local uri="$1"
    local database="$2"
    local mongo_client=$(_mongo_client_cmd)

    if [[ -z "$mongo_client" ]]; then
        return 1
    fi

    DBT_MONGO_DB="$database" "$mongo_client" "$uri" --quiet \
        --eval 'db.getSiblingDB(process.env.DBT_MONGO_DB).getCollectionNames().length > 0' \
        2>/dev/null | tail -1 | grep -q '^true$'
}

_mongo_drop_database() {
    local uri="$1"
    local database="$2"
    local mongo_client=$(_mongo_client_cmd)

    if [[ -z "$mongo_client" ]]; then
        return 1
    fi

    DBT_MONGO_DB="$database" "$mongo_client" "$uri" --quiet \
        --eval 'db.getSiblingDB(process.env.DBT_MONGO_DB).dropDatabase()' >/dev/null 2>&1
}

clear_mongo_backup_databases() {
    local target_uri="$1"
    local dry_run="${2:-false}"
    local mongo_client=$(_mongo_client_cmd)

    if [[ -z "$mongo_client" ]]; then
        echo "❌ Error: mongosh/mongo client not found"
        return 1
    fi

    local list_error_file=$(mktemp)
    local raw_backups
    raw_backups=$("$mongo_client" "$target_uri" --quiet --eval 'db.getMongo().getDBNames().filter((name) => /_backup_[0-9]{8}_[0-9]{6}$/.test(name)).sort().join("\n")' 2>"$list_error_file")
    local list_result=$?

    if [[ $list_result -ne 0 ]]; then
        echo "❌ Failed to list MongoDB backup databases"
        if [[ -s "$list_error_file" ]]; then
            local list_err=$(grep -i "error\|failed\|exception\|not authorized" "$list_error_file" | head -n 1)
            [[ -z "$list_err" ]] && list_err=$(grep -v "^$" "$list_error_file" | head -n 1)
            [[ -n "$list_err" ]] && echo "   Error: $list_err"
        fi
        rm -f "$list_error_file"
        return 1
    fi
    rm -f "$list_error_file"

    local backups=$(echo "$raw_backups" | grep -E '_backup_[0-9]{8}_[0-9]{6}$')

    if [[ -z "$backups" ]]; then
        echo "✅ No MongoDB backup databases found"
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

        if _mongo_drop_database "$target_uri" "$backup_db"; then
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

_mongo_backup_database() {
    setopt local_options pipe_fail

    local target_uri="$1"
    local target_db="$2"
    local backup_db="$3"
    local error_file="$4"
    local verbose_mode="${5:-false}"
    local mongodump_bin="${6:-mongodump}"
    local mongorestore_bin="${7:-mongorestore}"

    if [[ "$verbose_mode" == "true" ]]; then
        "$mongodump_bin" --uri="${target_uri}" --db="${target_db}" --archive --gzip 2> >(tee "$error_file" >&2) \
        | "$mongorestore_bin" --uri="${target_uri}" --archive --gzip --nsFrom="${target_db}.*" --nsTo="${backup_db}.*" 2> >(tee -a "$error_file" >&2)
    else
        "$mongodump_bin" --uri="${target_uri}" --db="${target_db}" --archive --gzip 2>"$error_file" \
        | "$mongorestore_bin" --uri="${target_uri}" --archive --gzip --nsFrom="${target_db}.*" --nsTo="${backup_db}.*" 2>>"$error_file" >/dev/null
    fi
}

_mongo_restore_backup_database() {
    setopt local_options pipe_fail

    local target_uri="$1"
    local target_db="$2"
    local backup_db="$3"
    local error_file="$4"
    local verbose_mode="${5:-false}"
    local mongodump_bin="${6:-mongodump}"
    local mongorestore_bin="${7:-mongorestore}"

    if [[ "$verbose_mode" == "true" ]]; then
        "$mongodump_bin" --uri="${target_uri}" --db="${backup_db}" --archive --gzip 2> >(tee "$error_file" >&2) \
        | "$mongorestore_bin" --uri="${target_uri}" --drop --archive --gzip --nsFrom="${backup_db}.*" --nsTo="${target_db}.*" 2> >(tee -a "$error_file" >&2)
    else
        "$mongodump_bin" --uri="${target_uri}" --db="${backup_db}" --archive --gzip 2>"$error_file" \
        | "$mongorestore_bin" --uri="${target_uri}" --drop --archive --gzip --nsFrom="${backup_db}.*" --nsTo="${target_db}.*" 2>>"$error_file" >/dev/null
    fi
}

_dbt_mongo_clear_interrupt_state() {
    unset DBT_MONGO_TARGET_URI DBT_MONGO_TARGET_DB DBT_MONGO_BACKUP_DB DBT_MONGO_BACKUP_CREATED
    unset DBT_MONGO_DUMP_BIN DBT_MONGO_RESTORE_BIN DBT_MONGO_VERBOSE DBT_MONGO_SPINNER_PID
}

_dbt_mongo_interrupt_restore() {
    trap - INT TERM

    echo ""
    echo "⚠️  MongoDB sync interrupted"

    if [[ -n "$DBT_MONGO_SPINNER_PID" ]]; then
        sync_dots_stop "$DBT_MONGO_SPINNER_PID"
        echo ""
    fi

    if [[ "$DBT_MONGO_BACKUP_CREATED" == "true" && -n "$DBT_MONGO_BACKUP_DB" ]]; then
        echo "🔄 Restoring backup database '${DBT_MONGO_BACKUP_DB}'..."
        local restore_error_file=$(mktemp)
        if _mongo_restore_backup_database "$DBT_MONGO_TARGET_URI" "$DBT_MONGO_TARGET_DB" "$DBT_MONGO_BACKUP_DB" "$restore_error_file" "$DBT_MONGO_VERBOSE" "$DBT_MONGO_DUMP_BIN" "$DBT_MONGO_RESTORE_BIN"; then
            echo "✅ Backup restored successfully"
            if _mongo_drop_database "$DBT_MONGO_TARGET_URI" "$DBT_MONGO_BACKUP_DB"; then
                echo "✅ Backup removed"
            else
                echo "⚠️  Backup restored, but failed to remove '${DBT_MONGO_BACKUP_DB}'"
            fi
        else
            echo "⚠️  Failed to restore backup. Backup database is still available as '${DBT_MONGO_BACKUP_DB}'"
            if [[ -s "$restore_error_file" ]]; then
                local restore_err=$(grep -i "error\|failed\|exception" "$restore_error_file" | head -n 1)
                [[ -n "$restore_err" ]] && echo "   Error: $restore_err"
            fi
        fi
        rm -f "$restore_error_file"
    fi

    _dbt_mongo_clear_interrupt_state
    return 130 2>/dev/null || exit 130
}

# Function to test MongoDB connection
test_mongo_connection() {
    local uri="$1"
    local name="$2"
    local quiet="${3:-false}"
    
    [[ "$quiet" != "true" ]] && echo "🔍 Testing connection to ${name}..."
    
    # Test connection by trying to list databases using mongosh or mongo
    # This doesn't require a specific database to exist
    local conn_error_file=$(mktemp)
    
    if command -v mongosh >/dev/null 2>&1; then
        # Use mongosh if available
        if mongosh "${uri}" --quiet --eval "db.adminCommand('ping')" >/dev/null 2>"$conn_error_file"; then
            [[ "$quiet" != "true" ]] && echo "✅ Connection to ${name} successful"
            rm -f "$conn_error_file"
            return 0
        fi
    elif command -v mongo >/dev/null 2>&1; then
        # Fallback to legacy mongo client
        if mongo "${uri}" --quiet --eval "db.adminCommand('ping')" >/dev/null 2>"$conn_error_file"; then
            [[ "$quiet" != "true" ]] && echo "✅ Connection to ${name} successful"
            rm -f "$conn_error_file"
            return 0
        fi
    else
        # If neither mongosh nor mongo is available, skip connection test
        [[ "$quiet" != "true" ]] && echo "⚠️  Cannot test connection (mongosh/mongo not found), proceeding anyway..."
        rm -f "$conn_error_file"
        return 0
    fi
    
    # Connection failed - show error details
    echo "❌ Failed to connect to ${name}"
    if [[ -s "$conn_error_file" ]]; then
        local conn_err=$(grep -i "error\|failed\|exception\|refused" "$conn_error_file" | head -n 1)
        if [[ -n "$conn_err" ]]; then
            echo "   Error: $conn_err"
        else
            # If no specific error found, show first non-empty line
            local first_line=$(grep -v "^$" "$conn_error_file" | head -n 1)
            [[ -n "$first_line" ]] && echo "   Error: $first_line"
        fi
    fi
    
    rm -f "$conn_error_file"
    return 1
}

# Function to perform actual MongoDB sync
perform_mongo_sync() {
    setopt local_options pipe_fail

    local source_uri="$1"
    local source_db="$2"
    local source_name="$3"
    local target_uri="$4"
    local target_name="$5"
    local quiet_mode="${6:-false}"   # Optional 6th parameter for quiet mode
    local verbose_mode="${7:-false}"  # Optional 7th parameter for verbose mode
    
    # Only show header in non-quiet mode
    if [[ "$quiet_mode" != "true" ]]; then
        echo "====================================="
        echo "MongoDB Sync: ${source_db}"
        echo "Source: ${source_name}"
        echo "Target: ${target_name}"
        echo "-------------------------------------"
    fi
    
    if [[ "$quiet_mode" != "true" ]]; then
        echo "🔍 Testing source connection..."
    fi
    if ! test_mongo_connection "${source_uri}" "${source_name}" "$quiet_mode"; then
        [[ "$quiet_mode" != "true" ]] && echo "❌ Cannot proceed - source connection failed"
        return 1
    fi
    
    if [[ "$quiet_mode" != "true" ]]; then
        echo "🔍 Testing target connection..."
    fi
    if ! test_mongo_connection "${target_uri}" "${target_name}" "$quiet_mode"; then
        [[ "$quiet_mode" != "true" ]] && echo "❌ Cannot proceed - target connection failed"
        return 1
    fi

    local mongo_tools=$(_find_mongo_tools)
    if [[ -z "$mongo_tools" ]]; then
        echo "❌ Error: MongoDB tools not found"
        echo "   Please install mongosh, mongodump, and mongorestore before running MongoDB sync"
        return 1
    fi

    local mongo_client=$(echo "$mongo_tools" | cut -d'|' -f1)
    local mongodump_bin=$(echo "$mongo_tools" | cut -d'|' -f2)
    local mongorestore_bin=$(echo "$mongo_tools" | cut -d'|' -f3)

    local backup_db_name=""
    local backup_created="false"
    if _mongo_database_exists "${target_uri}" "${source_db}"; then
        local timestamp=$(date +%Y%m%d_%H%M%S)
        backup_db_name="${source_db}_backup_${timestamp}"

        [[ "$quiet_mode" != "true" ]] && echo "💾 Backing up target database '${source_db}' to '${backup_db_name}'..."

        local backup_error_file=$(mktemp)
        if _mongo_backup_database "${target_uri}" "${source_db}" "${backup_db_name}" "$backup_error_file" "$verbose_mode" "$mongodump_bin" "$mongorestore_bin"; then
            backup_created="true"
            [[ "$quiet_mode" != "true" ]] && echo "✅ Backup created: ${backup_db_name}"
            rm -f "$backup_error_file"
        else
            [[ "$quiet_mode" != "true" ]] && echo "❌ Failed to backup target database"
            if [[ -s "$backup_error_file" ]]; then
                local backup_err=$(grep -i "error\|failed\|exception" "$backup_error_file" | head -n 1)
                [[ -n "$backup_err" ]] && echo "   Error: $backup_err"
            fi
            rm -f "$backup_error_file"
            return 1
        fi
    else
        [[ "$quiet_mode" != "true" ]] && echo "ℹ️  Target database '${source_db}' does not exist; skipping backup"
    fi

    typeset -g DBT_MONGO_TARGET_URI="$target_uri"
    typeset -g DBT_MONGO_TARGET_DB="$source_db"
    typeset -g DBT_MONGO_BACKUP_DB="$backup_db_name"
    typeset -g DBT_MONGO_BACKUP_CREATED="$backup_created"
    typeset -g DBT_MONGO_DUMP_BIN="$mongodump_bin"
    typeset -g DBT_MONGO_RESTORE_BIN="$mongorestore_bin"
    typeset -g DBT_MONGO_VERBOSE="$verbose_mode"
    typeset -g DBT_MONGO_SPINNER_PID=""
    trap _dbt_mongo_interrupt_restore INT TERM
    
    # Detect MongoDB versions
    if [[ "$quiet_mode" != "true" ]]; then
        echo "🔍 Detecting database versions..."
        
        # Detect source version
        local src_version=$("$mongo_client" "${source_uri}" --quiet --eval "db.version()" 2>/dev/null | tail -1)
        [[ -n "$src_version" ]] && echo "   Source version: MongoDB ${src_version}"
        
        # Detect target version
        local tgt_version=$("$mongo_client" "${target_uri}" --quiet --eval "db.version()" 2>/dev/null | tail -1)
        [[ -n "$tgt_version" ]] && echo "   Target version: MongoDB ${tgt_version}"
    fi
    
    local mongo_dots_pid=""
    if [[ "$quiet_mode" != "true" ]] && [[ "$verbose_mode" != "true" ]]; then
        sync_dots_start "📤 Syncing database" mongo_dots_pid
        DBT_MONGO_SPINNER_PID="$mongo_dots_pid"
    else
        [[ "$quiet_mode" != "true" ]] && echo "📤 Syncing database"
    fi
    
    # Perform the sync (capture errors)
    local mongo_error_file=$(mktemp)
    
    if [[ "$verbose_mode" == "true" ]]; then
        # Verbose mode: show all output
        "$mongodump_bin" --uri="${source_uri}" --db="${source_db}" --archive --gzip 2> >(tee "$mongo_error_file" >&2) \
        | "$mongorestore_bin" --uri="${target_uri}" --drop --archive --gzip --nsFrom="${source_db}.*" --nsTo="${source_db}.*" 2> >(tee -a "$mongo_error_file" >&2)
    else
        # Non-verbose mode: hide output, only capture errors
        "$mongodump_bin" --uri="${source_uri}" --db="${source_db}" --archive --gzip 2>"$mongo_error_file" \
        | "$mongorestore_bin" --uri="${target_uri}" --drop --archive --gzip --nsFrom="${source_db}.*" --nsTo="${source_db}.*" 2>>"$mongo_error_file" >/dev/null
    fi
    
    local sync_result=$?
    
    if [[ -n "$mongo_dots_pid" ]]; then
        sync_dots_stop "$mongo_dots_pid"
        DBT_MONGO_SPINNER_PID=""
        echo ""
    fi
    trap - INT TERM
    
    if [ $sync_result -eq 0 ]; then
        [[ "$quiet_mode" != "true" ]] && echo "✅ MongoDB synced successfully"
        if [[ "$backup_created" == "true" ]]; then
            [[ "$quiet_mode" != "true" ]] && echo "🗑️  Removing backup database '${backup_db_name}'..."
            if _mongo_drop_database "${target_uri}" "${backup_db_name}"; then
                [[ "$quiet_mode" != "true" ]] && echo "✅ Backup removed"
            else
                echo "⚠️  Failed to remove backup database '${backup_db_name}'"
                echo "   You can remove it manually after verifying the sync"
            fi
        fi
        [[ "$quiet_mode" != "true" ]] && echo "====================================="
        rm -f "$mongo_error_file"
        _dbt_mongo_clear_interrupt_state
        return 0
    else
        [[ "$quiet_mode" != "true" ]] && echo "❌ MongoDB sync failed"
        
        # Show error details
        if [[ -s "$mongo_error_file" ]]; then
            local mongo_err=$(grep -i "error\|failed\|exception" "$mongo_error_file" | head -n 1)
            [[ -n "$mongo_err" ]] && echo "   Error: $mongo_err"
        fi

        if [[ "$backup_created" == "true" ]]; then
            echo ""
            echo "🔄 Restoring backup database '${backup_db_name}'..."
            local restore_error_file=$(mktemp)
            if _mongo_restore_backup_database "${target_uri}" "${source_db}" "${backup_db_name}" "$restore_error_file" "$verbose_mode" "$mongodump_bin" "$mongorestore_bin"; then
                echo "✅ Backup restored successfully"
                if _mongo_drop_database "${target_uri}" "${backup_db_name}"; then
                    echo "✅ Backup removed"
                else
                    echo "⚠️  Backup restored, but failed to remove '${backup_db_name}'"
                fi
                rm -f "$restore_error_file"
            else
                echo "⚠️  Failed to restore backup. Backup database is still available as '${backup_db_name}'"
                if [[ -s "$restore_error_file" ]]; then
                    local restore_err=$(grep -i "error\|failed\|exception" "$restore_error_file" | head -n 1)
                    [[ -n "$restore_err" ]] && echo "   Error: $restore_err"
                fi
                rm -f "$restore_error_file"
            fi
        fi
        
        [[ "$quiet_mode" != "true" ]] && echo "====================================="
        rm -f "$mongo_error_file"
        _dbt_mongo_clear_interrupt_state
        return 1
    fi
}
