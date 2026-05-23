# =============================================================================
# Connection Module - Utility Functions
# =============================================================================

# Parse config string into variables
# Returns 0 on success, 1 on failure
parse_config() {
    local config=$1
    local vps_name_var=$2
    local user_var=$3
    local ip_var=$4
    local service_name_var=$5
    local local_port_var=$6
    local target_host_var=$7
    local target_port_var=$8
    
    IFS='|' read -r vps_name user ip service_name local_port target_host target_port <<< "$config"
    
    # Validate config format (should have 7 fields)
    local field_count=$(echo "$config" | tr '|' '\n' | wc -l | tr -d ' ')
    if [[ $field_count -ne 7 ]]; then
        log "ERROR" "Invalid config format (expected 7 fields, got $field_count): $config"
        return 1
    fi
    
    # Validate port numbers
    if ! [[ "$local_port" =~ ^[0-9]+$ ]] || ! [[ "$target_port" =~ ^[0-9]+$ ]]; then
        log "ERROR" "Invalid port number in config: $config"
        return 1
    fi
    
    # Validate port range
    if [[ $local_port -lt 1 ]] || [[ $local_port -gt 65535 ]] || \
       [[ $target_port -lt 1 ]] || [[ $target_port -gt 65535 ]]; then
        log "ERROR" "Port out of range (1-65535) in config: $config"
        return 1
    fi
    
    # Set variables using eval (safe here as we control the variable names)
    eval "${vps_name_var}='${vps_name}'"
    eval "${user_var}='${user}'"
    eval "${ip_var}='${ip}'"
    eval "${service_name_var}='${service_name}'"
    eval "${local_port_var}='${local_port}'"
    eval "${target_host_var}='${target_host}'"
    eval "${target_port_var}='${target_port}'"
    
    return 0
}

# Check if a port is in use
is_port_in_use() {
    local port=$1
    if lsof -iTCP:${port} -sTCP:LISTEN -t > /dev/null 2>&1; then
        return 0  # Port is in use
    else
        return 1  # Port is free
    fi
}

# Get process info for a port
get_port_process_info() {
    local port=$1
    local pid=$(lsof -iTCP:${port} -sTCP:LISTEN -t 2>/dev/null | head -n1)
    
    if [[ -n "$pid" ]]; then
        local cmd=$(ps -p "$pid" -o command= 2>/dev/null | head -n1)
        echo "PID: $pid, CMD: ${cmd:0:60}"
    else
        echo "No process found"
    fi
}

# Check if port is used by our SSH tunnel
is_our_ssh_tunnel() {
    local port=$1
    local pid=$(lsof -iTCP:${port} -sTCP:LISTEN -t 2>/dev/null | head -n1)
    
    if [[ -n "$pid" ]]; then
        local cmd=$(ps -p "$pid" -o command= 2>/dev/null)
        if echo "$cmd" | grep -q "ssh.*-L.*${port}"; then
            return 0  # It's our tunnel
        fi
    fi
    return 1  # Not our tunnel
}

# Test SSH connectivity to a host
test_ssh_connection() {
    local user=$1
    local ip=$2
    local vps_name=$3
    local quiet="${4:-false}"
    local vps_host="${user}@${ip}"
    
    [[ "$quiet" != "true" ]] && log "INFO" "Testing SSH connection to ${vps_name} (${ip})..."
    
    # Use timeout if available, otherwise use SSH's ConnectTimeout
    if command -v timeout >/dev/null 2>&1; then
        if timeout ${SSH_TIMEOUT} ssh -o ConnectTimeout=${SSH_TIMEOUT} \
           -o BatchMode=yes -o StrictHostKeyChecking=no \
           "$vps_host" exit 2>/dev/null; then
            [[ "$quiet" != "true" ]] && log "INFO" "SSH connection to ${vps_name} successful"
            return 0
        else
            [[ "$quiet" != "true" ]] && log "ERROR" "SSH connection to ${vps_name} (${ip}) failed"
            return 1
        fi
    else
        # macOS doesn't have timeout by default, use SSH ConnectTimeout only
        if ssh -o ConnectTimeout=${SSH_TIMEOUT} \
           -o BatchMode=yes -o StrictHostKeyChecking=no \
           "$vps_host" exit 2>/dev/null; then
            [[ "$quiet" != "true" ]] && log "INFO" "SSH connection to ${vps_name} successful"
            return 0
        else
            [[ "$quiet" != "true" ]] && log "ERROR" "SSH connection to ${vps_name} (${ip}) failed"
            return 1
        fi
    fi
}

# Get tunnel uptime
get_tunnel_uptime() {
    local port=$1
    local pid=$(lsof -iTCP:${port} -sTCP:LISTEN -t 2>/dev/null | head -n1)
    
    if [[ -z "$pid" ]]; then
        echo "N/A"
        return 1
    fi
    
    # Try to get elapsed time directly (Linux)
    local etime=$(ps -p "$pid" -o etime= 2>/dev/null | tr -d ' ')
    if [[ -n "$etime" ]] && [[ "$etime" != "ELAPSED" ]]; then
        echo "$etime"
        return 0
    fi
    
    # Fallback: calculate from start time (macOS)
    local start_time=$(ps -p "$pid" -o lstart= 2>/dev/null | tr -s ' ')
    if [[ -n "$start_time" ]]; then
        # Parse format: "Mon Jan 1 12:00:00 2024" or "Mon Jan  1 12:00:00 2024"
        local start_epoch=$(date -j -f "%a %b %d %H:%M:%S %Y" "$start_time" "+%s" 2>/dev/null)
        if [[ -z "$start_epoch" ]]; then
            # Try alternative format with single digit day
            start_epoch=$(date -j -f "%a %b  %d %H:%M:%S %Y" "$start_time" "+%s" 2>/dev/null)
        fi
        
        if [[ -n "$start_epoch" ]]; then
            local now_epoch=$(date "+%s")
            local uptime_seconds=$((now_epoch - start_epoch))
            
            if [[ $uptime_seconds -lt 0 ]]; then
                echo "N/A"
                return 1
            fi
            
            local days=$((uptime_seconds / 86400))
            local hours=$(((uptime_seconds % 86400) / 3600))
            local minutes=$(((uptime_seconds % 3600) / 60))
            
            if [[ $days -gt 0 ]]; then
                echo "${days}d ${hours}h ${minutes}m"
            elif [[ $hours -gt 0 ]]; then
                echo "${hours}h ${minutes}m"
            else
                echo "${minutes}m"
            fi
            return 0
        fi
    fi
    
    echo "N/A"
    return 1
}

# Find config by service name or port
find_config() {
    local search_term=$1
    local found_configs=()
    
    for config in "${VPS_CONFIGS[@]}"; do
        local vps_name user ip service_name local_port target_host target_port
        if parse_config "$config" vps_name user ip service_name local_port target_host target_port; then
            if [[ "$service_name" == "$search_term" ]] || \
               [[ "$local_port" == "$search_term" ]] || \
               [[ "$service_name" == *"$search_term"* ]]; then
                found_configs+=("$config")
            fi
        fi
    done
    
    echo "${found_configs[@]}"
}

# Check if there are active connections
has_active_connections() {
    for config in "${VPS_CONFIGS[@]}"; do
        # Skip empty configs
        if [[ -z "$config" ]] || [[ "$config" =~ ^[[:space:]]*$ ]]; then
            continue
        fi
        
        local vps_name user ip service_name local_port target_host target_port
        # Parse config - don't use subshell
        if parse_config "$config" vps_name user ip service_name local_port target_host target_port 2>/dev/null; then
            if is_port_in_use "${local_port}" && is_our_ssh_tunnel "${local_port}"; then
                return 0
            fi
        fi
    done
    return 1
}

# =============================================================================
# Connection Module - Main Functions
# =============================================================================

# Connect to databases via SSH tunnels
connect_db() {
    # Load secrets first to ensure VPS_CONFIGS is available
    load_secrets
    
    local service_filter=$1  # Optional: connect to specific service
    
    {
        echo "🔌 Connecting tunnels..."
        [[ -n "$service_filter" ]] && echo "   Filter: $service_filter"
        echo ""
        
        local already_connected=0
        local newly_connected=0
        local failed_connections=0
        local skipped_invalid=0
        local invalid_configs_list=()
        
        # Filter configs if service_filter is provided
        local configs_to_process=()
        if [[ -n "$service_filter" ]]; then
            local found_configs=($(find_config "$service_filter"))
            if [[ ${#found_configs[@]} -eq 0 ]]; then
                echo "❌ No service found matching: $service_filter"
                echo "💡 Use 'dbt list' to list all available services"
                return 1
            fi
            configs_to_process=("${found_configs[@]}")
        else
            configs_to_process=("${VPS_CONFIGS[@]}")
        fi
        
        # Check each port and connect if not already connected
        typeset -A vps_groups
        typeset -A vps_already_groups
        typeset -A ports_to_connect
        
        for config in "${configs_to_process[@]}"; do
            local vps_name user ip service_name local_port target_host target_port
            
            # Skip empty configs (from comments or blank lines)
            if [[ -z "$config" ]] || [[ "$config" =~ ^[[:space:]]*$ ]]; then
                continue
            fi
            
            # Parse config - keep success path lightweight (no temp file)
            if ! parse_config "$config" vps_name user ip service_name local_port target_host target_port 2>/dev/null; then
                ((skipped_invalid++))
                invalid_configs_list+=("$config")
                # Show error for invalid config (filter debug output)
                local parse_error
                parse_error=$(parse_config "$config" _vps_name _user _ip _service_name _local_port _target_host _target_port 2>&1 >/dev/null)
                if [[ -n "$parse_error" ]]; then
                    echo "$parse_error" | filter_debug >&2
                else
                    echo "❌ Invalid config format: $config" >&2
                fi
                continue
            fi
        
        if is_port_in_use "${local_port}"; then
            if is_our_ssh_tunnel "${local_port}"; then
                local vps_key="${vps_name}|${user}|${ip}"
                if [[ -z "${vps_already_groups[$vps_key]}" ]]; then
                    vps_already_groups[$vps_key]="$config"
                else
                    vps_already_groups[$vps_key]+=";$config"
                fi
                ((already_connected++))
            else
                local process_info=$(get_port_process_info "${local_port}")
                echo "⚠️  ${service_name} (port ${local_port}): Port in use by other process"
                echo "   ${process_info}"
                log "WARN" "Port ${local_port} is in use by another process: ${process_info}"
                ((failed_connections++))
            fi
        else
            # Validate parsed values before adding to groups
            if [[ -z "$vps_name" ]] || [[ -z "$user" ]] || [[ -z "$ip" ]] || [[ -z "$local_port" ]]; then
                echo "⚠️  Skipping invalid config (missing required fields): $config" >&2
                ((skipped_invalid++))
                invalid_configs_list+=("$config")
                continue
            fi
            
            # Mark this port for connection
            local vps_key="${vps_name}|${user}|${ip}"
            if [[ -z "${vps_groups[$vps_key]}" ]]; then
                vps_groups[$vps_key]="$config"
            else
                vps_groups[$vps_key]+=";$config"
            fi
            ports_to_connect[$local_port]=1
        fi
    done

    # Show existing tunnels grouped by VPS for compact output
    if [[ ${#vps_already_groups[@]} -gt 0 ]]; then
        for vps_key in ${(k)vps_already_groups}; do
            IFS='|' read -r vps_name user ip <<< "$vps_key"
            echo "${vps_name} ${ip}:"
            echo "  Already on:"

            IFS=';' read -rA configs <<< "${vps_already_groups[$vps_key]}"
            for config in "${configs[@]}"; do
                local _vps_name _user _ip service_name local_port target_host target_port
                if parse_config "$config" _vps_name _user _ip service_name local_port target_host target_port 2>/dev/null; then
                    echo "    - ${service_name}: local :${local_port} -> remote :${target_port}"
                fi
            done
            echo ""
        done
    fi
    
    # Establish SSH tunnels for ports that need connection
    if [[ ${#vps_groups[@]} -gt 0 ]]; then
        for vps_key in ${(k)vps_groups}; do
            IFS='|' read -r vps_name user ip <<< "$vps_key"
            local vps_host="${user}@${ip}"
            
            # Test SSH connection first (optional, skip if test fails but still try to connect)
            # SSH will fail on its own if connection is not possible
            if ! test_ssh_connection "$user" "$ip" "$vps_name" "true"; then
                echo "⚠️  SSH test failed for ${vps_name}, but will still attempt connection..."
            fi
            
            # Build SSH command using array (safe, no eval)
            local ssh_args=("-f" "-N" "-o" "ConnectTimeout=${SSH_TIMEOUT}" "-o" "ServerAliveInterval=60" "-o" "ServerAliveCountMax=3")
            local services=()
            IFS=';' read -rA configs <<< "${vps_groups[$vps_key]}"
            
            for config in "${configs[@]}"; do
                local _vps_name _user _ip service_name local_port target_host target_port
                if parse_config "$config" _vps_name _user _ip service_name local_port target_host target_port 2>/dev/null; then
                    ssh_args+=("-L" "${local_port}:${target_host}:${target_port}")
                    services+=("${service_name}: local :${local_port} -> remote :${target_port}")
                fi
            done
            
            ssh_args+=("${vps_host}")
            
            # Execute SSH tunnel using array (safe)
            local ssh_output
            ssh_output=$(ssh "${ssh_args[@]}" 2>&1)
            local ssh_exit_code=$?
            
            if [[ $ssh_exit_code -eq 0 ]]; then
                echo "${vps_name} ${ip}:"
                echo "  Opened:"
                for service_info in "${services[@]}"; do
                    echo "    - ${service_info}"
                    ((newly_connected++))
                done
                echo ""
            else
                echo "❌ Failed to establish tunnels to ${vps_name}"
                if [[ -n "$ssh_output" ]]; then
                    echo "   Error: $ssh_output"
                else
                    echo "💡 Check SSH connection, authentication, or if ports are already in use"
                fi
                ((failed_connections+=${#services[@]}))
                log "ERROR" "Failed to establish tunnels to ${vps_name}: $ssh_output"
            fi
        done
    fi
    
        # Display compact result summary (optimized for quick scanning)
        local total_services=$((already_connected + newly_connected + failed_connections))
        echo ""
        if [[ $failed_connections -gt 0 ]]; then
            echo "❌ Connect: ${newly_connected} opened | ${already_connected} already on | ${failed_connections} failed (${total_services} total)"
        elif [[ $newly_connected -gt 0 ]]; then
            echo "✅ Connect: ${newly_connected} opened | ${already_connected} already on (${total_services} total)"
        else
            echo "✅ Connect: nothing to open (all ${already_connected}/${total_services} already on)"
        fi

        if [[ $skipped_invalid -gt 0 ]]; then
            echo "⚠️  Invalid configs: ${skipped_invalid}"
            echo ""
            echo "Invalid configuration(s):"
            local i=1
            for invalid_config in "${invalid_configs_list[@]}"; do
                echo "   ${i}) ${invalid_config}"
                ((i++))
            done
            echo ""
            echo "💡 Expected format: VPS_NAME|USER|IP|SERVICE_NAME|LOCAL_PORT|TARGET_HOST|TARGET_PORT"
            echo "💡 Check your secrets file for correct format"
        fi
        echo ""
    } 2>&1 | filter_debug
}

# Disconnect all database tunnels
disconnect_db() {
    # Load secrets first to ensure VPS_CONFIGS is available
    load_secrets
    
    local service_filter=$1  # Optional: disconnect specific service
    
    {
        echo "🔌 Disconnecting tunnels..."
        [[ -n "$service_filter" ]] && echo "   Filter: $service_filter"
        echo ""
        
        local disconnected=0
        local not_connected=0
        local failed_disconnect=0
        
        # Filter configs if service_filter is provided
        local configs_to_process=()
        if [[ -n "$service_filter" ]]; then
            local found_configs=($(find_config "$service_filter"))
            if [[ ${#found_configs[@]} -eq 0 ]]; then
                echo "❌ No service found matching: $service_filter"
                echo "💡 Use 'dbt list' to list all available services"
                return 1
            fi
            configs_to_process=("${found_configs[@]}")
        else
            configs_to_process=("${VPS_CONFIGS[@]}")
        fi
        
        # Group ports by PID to avoid killing the same SSH process multiple times
        # (multiple ports can be forwarded by the same SSH command)
        typeset -A pid_to_ports
        typeset -A port_to_service
        typeset -A port_to_endpoint
        typeset -A port_to_vps_label
        typeset -A vps_off_groups
        
        # First pass: collect all ports and their PIDs
        for config in "${configs_to_process[@]}"; do
            local vps_name user ip service_name local_port target_host target_port
            
            # Parse config and suppress debug output
            {
                if ! parse_config "$config" vps_name user ip service_name local_port target_host target_port; then
                    continue
                fi
            } 2>/dev/null
        
        # Find PID by port (more reliable than pattern matching)
        local pid=$(lsof -iTCP:${local_port} -sTCP:LISTEN -t 2>/dev/null | head -n1)
        
        if [[ -n "$pid" ]]; then
            # Verify it's an SSH process
            local cmd=$(ps -p "$pid" -o command= 2>/dev/null)
            if echo "$cmd" | grep -q "ssh.*-L.*${local_port}"; then
                # Group ports by PID
                if [[ -z "${pid_to_ports[$pid]}" ]]; then
                    pid_to_ports[$pid]="${local_port}"
                else
                    pid_to_ports[$pid]+=" ${local_port}"
                fi
                port_to_service[$local_port]="${service_name}"
                port_to_endpoint[$local_port]="local :${local_port} -> remote :${target_port}"
                port_to_vps_label[$local_port]="${vps_name} ${ip}"
            else
                echo "⚠️  ${vps_name} ${ip}: ${service_name}: local :${local_port} -> remote :${target_port} port in use by non-SSH process"
                ((not_connected++))
            fi
        else
            local vps_key="${vps_name}|${user}|${ip}"
            local service_info="${service_name}: local :${local_port} -> remote :${target_port}"
            if [[ -z "${vps_off_groups[$vps_key]}" ]]; then
                vps_off_groups[$vps_key]="$service_info"
            else
                vps_off_groups[$vps_key]+=";$service_info"
            fi
            ((not_connected++))
        fi
    done

    # Show already-off tunnels grouped by VPS for compact output
    if [[ ${#vps_off_groups[@]} -gt 0 ]]; then
        for vps_key in ${(k)vps_off_groups}; do
            IFS='|' read -r vps_name user ip <<< "$vps_key"
            echo "${vps_name} ${ip}:"
            echo "  Already off:"

            IFS=';' read -rA services_info <<< "${vps_off_groups[$vps_key]}"
            for service_info in "${services_info[@]}"; do
                echo "    - ${service_info}"
            done
            echo ""
        done
    fi
    
    # Second pass: kill PIDs (each PID may handle multiple ports)
    for pid in ${(k)pid_to_ports}; do
        local ports=(${=pid_to_ports[$pid]})
        local services_info=()
        local first_port="${ports[1]}"
        local vps_label="${port_to_vps_label[$first_port]}"
        
        for port in "${ports[@]}"; do
            local service_name="${port_to_service[$port]}"
            services_info+=("${service_name}: ${port_to_endpoint[$port]}")
        done
        
        # Kill the process
        if kill "$pid" 2>/dev/null; then
            # Wait a bit and verify it's actually killed
            sleep 0.5
            if ! kill -0 "$pid" 2>/dev/null; then
                echo "${vps_label}:"
                echo "  Closed:"
                for service_info in "${services_info[@]}"; do
                    echo "    - ${service_info}"
                    ((disconnected++))
                done
            else
                echo "⚠️  Process still running, trying force kill..."
                kill -9 "$pid" 2>/dev/null
                sleep 0.5
                if ! kill -0 "$pid" 2>/dev/null; then
                    echo "${vps_label}:"
                    echo "  Closed (forced):"
                    for service_info in "${services_info[@]}"; do
                        echo "    - ${service_info}"
                        ((disconnected++))
                    done
                else
                    echo "❌ Failed to disconnect ${#ports[@]} service(s) on ${vps_label}:"
                    for service_info in "${services_info[@]}"; do
                        echo "  - ${service_info}"
                        ((failed_disconnect++))
                    done
                fi
            fi
        else
            echo "❌ Failed to disconnect ${#ports[@]} service(s) on ${vps_label} (permission denied?):"
            for service_info in "${services_info[@]}"; do
                echo "  - ${service_info}"
                ((failed_disconnect++))
            done
        fi
    done
    
        # Display compact result summary (optimized for quick scanning)
        local total_services=$((disconnected + not_connected + failed_disconnect))
        echo ""
        if [[ $failed_disconnect -gt 0 ]]; then
            echo "❌ Disconnect: ${failed_disconnect} failed | ${disconnected} closed | ${not_connected} already off (${total_services} total)"
        elif [[ $disconnected -gt 0 ]]; then
            echo "✅ Disconnect: ${disconnected} closed | ${not_connected} already off (${total_services} total)"
        else
            echo "✅ Disconnect: nothing to close (${not_connected}/${total_services} already off)"
        fi
        echo ""
    } 2>&1 | filter_debug
}

# Show all forwarded ports
show_forward_port() {
    # Disable any debug/trace output
    set +x 2>/dev/null
    
    {
        local connected=0
        local not_connected=0
        local conflicts=0
        local invalid_configs=0
        local invalid_configs_list=()
        local table_rows=()
        local issues=()
        local total_services=0
        
        # Always load secrets to ensure we have the latest configurations
        load_secrets
        
        if [[ ${#VPS_CONFIGS[@]} -eq 0 ]]; then
            echo "⚠️  No VPS configurations found. Please check your secrets file."
            echo ""
            return
        fi
        
        for config in "${VPS_CONFIGS[@]}"; do
            local vps_name user ip service_name local_port target_host target_port
            
            # Skip empty configs (from comments or blank lines)
            if [[ -z "$config" ]] || [[ "$config" =~ ^[[:space:]]*$ ]]; then
                continue
            fi
            
            # Parse config - keep success path lightweight (no temp file)
            if ! parse_config "$config" vps_name user ip service_name local_port target_host target_port 2>/dev/null; then
                ((invalid_configs++))
                invalid_configs_list+=("$config")
                local parse_error
                parse_error=$(parse_config "$config" _vps_name _user _ip _service_name _local_port _target_host _target_port 2>&1 >/dev/null)
                if [[ -n "$parse_error" ]]; then
                    echo "$parse_error" | filter_debug >&2
                fi
                continue
            fi
            
            local status_icon=""
            local status_label=""
            local uptime=""
            local target_info="${target_host}:${target_port}"
            local local_info=":${local_port}"
            local status_text=""
            ((total_services++))
            
            if is_port_in_use "${local_port}"; then
                if is_our_ssh_tunnel "${local_port}"; then
                    status_icon="🟢"
                    status_label="UP"
                    uptime=$(get_tunnel_uptime "${local_port}" 2>/dev/null)
                    ((connected++))
                else
                    status_icon="⚠️"
                    status_label="BUSY"
                    uptime="Other"
                    status_text="port occupied by non-SSH process"
                    issues+=("${status_icon} ${service_name} ${local_info} ${status_text}")
                    ((conflicts++))
                fi
            else
                status_icon="🔴"
                status_label="DOWN"
                uptime="-"
                status_text="tunnel is down"
                issues+=("${status_icon} ${service_name} ${local_info} ${status_text}")
                ((not_connected++))
            fi
            
            table_rows+=("$(printf "%-6s %-15s  %-7s  %-18s  %-10s  %-15s  %-8s" \
                "${status_label}" \
                "${service_name}" \
                "${local_info}" \
                "${target_info}" \
                "${vps_name}" \
                "${ip}" \
                "${uptime}")")
        done
        
        # Display compact summary first
        echo ""
        echo "Status: ${connected}/${total_services} connected | ${not_connected}/${total_services} disconnected | ${conflicts} conflict"
        [[ $invalid_configs -gt 0 ]] && echo "⚠️  Invalid configs: ${invalid_configs} (check secrets format)"

        echo ""
        printf "%-6s %-15s  %-7s  %-18s  %-10s  %-15s  %-8s\n" "STATUS" "SERVICE" "LOCAL" "TARGET" "VIA" "IP" "UPTIME"
        echo "───────────────────────────────────────────────────────────────────────────────────────────────"
        for row in "${table_rows[@]}"; do
            echo "$row"
        done
        echo "───────────────────────────────────────────────────────────────────────────────────────────────"

        if [[ ${#issues[@]} -gt 0 ]]; then
            echo ""
            echo "Issues:"
            for issue in "${issues[@]}"; do
                echo "  - ${issue}"
            done
        fi
    } 2>&1 | filter_debug
}

# Test SSH connectivity to all VPS
test_connections() {
    # Load secrets first to ensure VPS_CONFIGS is available
    load_secrets
    
    {
        echo "🧪 Testing SSH connectivity to all VPS..."
        echo ""
        
        local success=0
        local failed=0
        typeset -A vps_hosts
        
        # Collect unique VPS hosts
        for config in "${VPS_CONFIGS[@]}"; do
            local vps_name user ip service_name local_port target_host target_port
            
            # Parse config and suppress debug output
            {
                if ! parse_config "$config" vps_name user ip service_name local_port target_host target_port; then
                    continue
                fi
            } 2>/dev/null
            
            local vps_key="${vps_name}|${user}|${ip}"
            if [[ -z "${vps_hosts[$vps_key]}" ]]; then
                vps_hosts[$vps_key]="${user}|${ip}"
            fi
        done
        
        # Test each VPS
        for vps_key in ${(k)vps_hosts}; do
            IFS='|' read -r vps_name user ip <<< "$vps_key"
            
            echo -n "Testing ${vps_name} (${ip})... "
            echo ""
            if test_ssh_connection "$user" "$ip" "$vps_name" "true"; then
                echo "✅ OK"
                ((success++))
            else
                echo "❌ FAILED"
                ((failed++))
            fi
        done
        
        local total_vps=$((success + failed))
        echo ""
        if [[ $failed -gt 0 ]]; then
            echo "❌ SSH Test: ${failed}/${total_vps} failed | ${success}/${total_vps} ok"
        else
            echo "✅ SSH Test: all ${success}/${total_vps} VPS reachable"
        fi
        echo ""
    } 2>&1 | filter_debug
}
