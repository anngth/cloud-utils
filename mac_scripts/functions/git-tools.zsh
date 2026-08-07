# Check if we're in a git repository
_git_check_repo() {
    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        echo "❌ Error: Not in a git repository"
        return 1
    fi
    return 0
}

# Helper function to prune a stale remote-tracking ref when the remote branch was deleted
_prune_deleted_remote_branch_ref() {
    local remote="$1"
    local branch="$2"
    local remote_ref="refs/remotes/$remote/$branch"

    if [ -z "$remote" ] || [ -z "$branch" ]; then
        return 0
    fi

    if ! git show-ref --verify --quiet "$remote_ref"; then
        return 0
    fi

    git ls-remote --exit-code --heads "$remote" "$branch" > /dev/null 2>&1
    local ls_remote_status=$?

    if [ "$ls_remote_status" -eq 0 ]; then
        return 0
    fi

    if [ "$ls_remote_status" -eq 2 ]; then
        echo "--- 🧹 Remote $remote/$branch no longer exists; pruning stale tracking ref..."
        if git fetch "$remote" --prune; then
            echo "--- ✅ Pruned stale remote-tracking refs"
        else
            echo "--- ⚠️ Failed to prune stale remote-tracking refs; continuing with push"
        fi
        return 0
    fi

    echo "--- ⚠️ Could not verify whether $remote/$branch exists; continuing with push"
}

# Function 1: Safe force push
git_safe_force_push() {
    if ! _git_check_repo; then
        return 1
    fi
    
    local current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
    local tracking_branch=$(git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>/dev/null)
    local tracking_remote=""
    local tracking_name=""
    local target_remote="origin"
    local push_output=""

    echo "--- 🔄 Force Push (safe)..."
    if [ "$current_branch" = "HEAD" ] || [ -z "$current_branch" ]; then
        echo "--- ❌ Cannot push from detached HEAD"
        return 1
    fi

    if [ -n "$current_branch" ]; then
        echo "--- 🌿 Current branch: $current_branch"
    fi
    if [ -n "$tracking_branch" ]; then
        echo "--- 🎯 Tracking branch: $tracking_branch"
        tracking_remote="${tracking_branch%%/*}"
        tracking_name="${tracking_branch#*/}"
        if [ -n "$tracking_remote" ] && [ "$tracking_remote" != "$tracking_branch" ]; then
            target_remote="$tracking_remote"
        fi
        if [ "$tracking_name" != "$current_branch" ]; then
            echo "--- ⚠️ Tracking branch name differs from local branch"
            echo "--- ℹ️ Will push explicitly to $target_remote/$current_branch"
        fi
    else
        echo "--- ⚠️ No upstream tracking branch configured"
        echo "--- ℹ️ Will push explicitly to $target_remote/$current_branch"
    fi

    _prune_deleted_remote_branch_ref "$target_remote" "$current_branch"

    push_output=$(git push --force-with-lease "$target_remote" "HEAD:$current_branch" 2>&1)
    if [ $? -eq 0 ]; then
        [ -n "$push_output" ] && echo "$push_output"
        echo "--- ✅ Force push successful"
        echo "--- ⬇️ Fetching..."
        if git fetch; then
            echo "--- ✅ Fetch successful"
        else
            echo "--- ❌ Fetch failed"
        fi
        return 0
    else
        echo "--- ❌ Force push failed"
        [ -n "$push_output" ] && echo "$push_output"
        echo "--- 💡 Tip: run 'git remote -v' and verify write permission to the remote"
        return 1
    fi
}

# Helper function to sync with upstream
_sync_with_upstream() {
    # Check if upstream remote exists
    if ! git remote get-url upstream > /dev/null 2>&1; then
        return 1
    fi
    
    # Get current branch
    local current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
    
    # If on main, merge upstream changes
    if [ "$current_branch" = "main" ]; then
        # Check if upstream main branch exists
        if [ -n "$(git ls-remote --heads upstream main 2>/dev/null)" ]; then
            # Check for uncommitted changes
            if ! git diff --quiet || ! git diff --cached --quiet; then
                echo "--- ⚠️ You have uncommitted changes. Skipping upstream merge."
                echo "--- 💡 Commit or stash your changes, then run 'gt fetch' again to merge upstream"
                return 0
            fi
            
            echo "--- 🔄 Merging upstream/main into main..."
            if git merge "upstream/main" --no-edit 2>/dev/null; then
                echo "--- ✅ Merged upstream changes successfully"
                
                # Push the updated main to origin
                echo "--- 🔄 Pushing updated main to origin..."
                if git push origin main 2>/dev/null; then
                    echo "--- ✅ Pushed updated main to origin"
                else
                    echo "--- ⚠️ Failed to push to origin, but local merge was successful"
                fi
            else
                echo "--- ❌ Merge failed - there might be conflicts"
                echo "--- 💡 Resolve conflicts manually, then run 'git add .' and 'git commit'"
                return 1
            fi
        fi
    fi
    
    return 0
}

# Helper function to fetch and prune
_git_fetch_and_prune() {
    echo "--- ⬇️ Fetching from all remotes..."
    # Try fetching with increased buffer size to handle large repos/unstable network
    if git fetch --all --prune; then
        echo "--- ✅ Fetch & prune successful"
        return 0
    else
        echo "--- ⚠️ Failed to fetch from all remotes. Trying origin only..."
        # Fallback: try fetching just origin with increased buffer
        if git fetch origin --prune; then
             echo "--- ✅ Fetch origin successful"
             return 0
        fi
        
        echo "--- ❌ Failed to fetch"
        return 1
    fi
}

# Helper function to clean up gone branches
_cleanup_gone_branches() {
    local branches=$(git branch -vv | grep ': gone]' | grep -v '^*' | awk '{print $1}')
    
    if [ -z "$branches" ]; then
        return 0
    fi
    
    echo "--- 🧹 Cleaning gone branches: $branches"
    for branch in $(echo "$branches"); do
        if git branch -d "$branch" 2>/dev/null; then
            echo "--- ✅ Deleted: $branch"
        else
            echo "--- ⚠️ Skipped (not merged): $branch"
        fi
    done
}

# Helper function to detect best base branch for merged-branch cleanup
_get_cleanup_base_branch() {
    local current_branch="$1"

    if [ "$current_branch" = "main" ] || [ "$current_branch" = "develop" ]; then
        echo "$current_branch"
        return 0
    fi

    if git show-ref --verify --quiet refs/heads/main; then
        echo "main"
        return 0
    fi

    if git show-ref --verify --quiet refs/heads/develop; then
        echo "develop"
        return 0
    fi

    echo "$current_branch"
}

# Helper function to clean up local branches already merged into base branch
_cleanup_merged_branches() {
    local base_branch="$1"
    local current_branch="$2"
    local cleaned_any=0

    if [ -z "$base_branch" ] || ! git show-ref --verify --quiet "refs/heads/$base_branch"; then
        return 0
    fi

    local branches=$(git for-each-ref --format='%(refname:short)' refs/heads | grep -Ev '^(main|master|develop)$')

    if [ -z "$branches" ]; then
        return 0
    fi

    echo "--- 🧹 Cleaning merged branches (base: $base_branch)..."
    for branch in $(echo "$branches"); do
        if [ "$branch" = "$current_branch" ]; then
            continue
        fi

        if git merge-base --is-ancestor "$branch" "$base_branch" 2>/dev/null; then
            if git branch -d "$branch" 2>/dev/null; then
                echo "--- ✅ Deleted merged branch: $branch"
                cleaned_any=1
            fi
        fi
    done

    if [ "$cleaned_any" -eq 0 ]; then
        echo "--- ℹ️ No merged local branches to clean"
    fi
}

# Helper function to get current branch
_get_current_branch() {
    git rev-parse --abbrev-ref HEAD 2>/dev/null
}

# Helper function to check if branch should auto-sync
_should_auto_sync() {
    local branch="$1"
    [ "$branch" = "main" ] || [ "$branch" = "develop" ]
}

# Helper function to sync current branch with origin
_sync_with_origin() {
    local branch="$1"

    if ! git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
        echo "--- ℹ️ origin/$branch not found - skipping sync"
        return 0
    fi
    
    echo "--- 🔄 Syncing $branch branch..."
    if git pull --ff-only origin "$branch" 2>/dev/null; then
        echo "--- ✅ Pulled from origin/$branch"
        return 0
    else
        echo "--- ❌ Pull failed (non fast-forward or uncommitted changes)"
        return 1
    fi
}

# Helper function to refresh remote-tracking refs for primary branches
_refresh_primary_remote_refs() {
    # 1. Fetch origin/main (remote-tracking only, safe on any checked out branch)
    if [ -n "$(git ls-remote --heads origin main 2>/dev/null)" ]; then
        echo "--- ⬇️ Fetching origin/main without checkout..."
        if git fetch origin main; then
            echo "--- ✅ Successfully fetched origin/main"
        else
            echo "--- ⚠️ Failed to fetch origin/main"
        fi
    fi

    # 2. Fetch origin/develop (remote-tracking only, safe on any checked out branch)
    if [ -n "$(git ls-remote --heads origin develop 2>/dev/null)" ]; then
        echo "--- ⬇️ Fetching origin/develop without checkout..."
        if git fetch origin develop; then
            echo "--- ✅ Successfully fetched origin/develop"
        else
            echo "--- ⚠️ Failed to fetch origin/develop"
        fi
    fi
}

# Helper function to align local branch with origin/<branch> (fast-forward only, no checkout)
_sync_local_branch_with_origin_ff() {
    local branch="$1"
    local current_branch="$2"

    # Guard: only continue if remote-tracking ref exists locally (from fetch)
    if ! git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
        return 0
    fi

    # Skip checked-out branch (handled by pull flow)
    if [ "$current_branch" = "$branch" ]; then
        return 0
    fi

    # Create local branch if it doesn't exist yet
    if ! git show-ref --verify --quiet "refs/heads/$branch"; then
        echo "--- 🌱 Creating local '$branch' from origin/$branch..."
        if git branch "$branch" "origin/$branch" 2>/dev/null; then
            echo "--- ✅ Created local '$branch'"
        else
            echo "--- ⚠️ Failed to create local '$branch'"
        fi
        return 0
    fi

    local local_sha=$(git rev-parse "refs/heads/$branch" 2>/dev/null)
    local remote_sha=$(git rev-parse "refs/remotes/origin/$branch" 2>/dev/null)

    if [ -z "$local_sha" ] || [ -z "$remote_sha" ] || [ "$local_sha" = "$remote_sha" ]; then
        return 0
    fi

    # Update only when local is behind (no history rewrite if diverged)
    if git merge-base --is-ancestor "$branch" "origin/$branch" 2>/dev/null; then
        echo "--- 🔄 Fast-forwarding local '$branch' to origin/$branch..."
        if git update-ref "refs/heads/$branch" "$remote_sha" "$local_sha" 2>/dev/null; then
            echo "--- ✅ Updated local '$branch' to origin/$branch"
        else
            echo "--- ⚠️ Failed to update local '$branch'"
        fi
    else
        echo "--- ⚠️ Local '$branch' has local-only commits; skipping auto-sync"
        echo "--- 💡 Run: git switch $branch && git pull --ff-only origin $branch"
    fi
}

# Function 2: Fetch and sync repository
git_fetch() {
    local sync_upstream=0

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --sync-upstream)
                sync_upstream=1
                ;;
            -h|--help)
                echo "Usage: gt fetch [--sync-upstream]"
                echo "  --sync-upstream  Merge upstream/main into local main and push origin/main (main only)"
                return 0
                ;;
            *)
                echo "❌ Unknown option for fetch: $1"
                echo "Usage: gt fetch [--sync-upstream]"
                return 1
                ;;
        esac
        shift
    done

    if ! _git_check_repo; then
        return 1
    fi
    
    echo "--- ⬇️ Fetching and syncing repository ---"
    
    # Step 1: Refresh remote-tracking refs for primary branches
    _refresh_primary_remote_refs
    
    # Step 2: Fetch and prune
    if ! _git_fetch_and_prune; then
        return 1
    fi
    
    # Step 3: Get current branch and decide sync strategy
    local current_branch=$(_get_current_branch)

    # Keep local main/develop aligned with origin/* while staying on feature branches
    _sync_local_branch_with_origin_ff "main" "$current_branch"
    _sync_local_branch_with_origin_ff "develop" "$current_branch"
    
    if _should_auto_sync "$current_branch"; then
        # Step 4: Sync with origin
        if ! _sync_with_origin "$current_branch"; then
            return 1
        fi
        
        # Step 5: If on main, optionally sync with upstream
        if [ "$current_branch" = "main" ]; then
            if [ "$sync_upstream" -eq 1 ]; then
                _sync_with_upstream
            else
                echo "--- ℹ️ Skipping upstream sync (use 'gt fetch --sync-upstream' to enable)"
            fi
        fi
    else
        echo "--- ℹ️ On branch '$current_branch' - no auto-sync performed"
    fi

    # Step 6: Clean up gone branches and merged local branches
    _cleanup_gone_branches
    local cleanup_base_branch=$(_get_cleanup_base_branch "$current_branch")
    _cleanup_merged_branches "$cleanup_base_branch" "$current_branch"
    
    return 0
}


# Main function gt
gt() {
    if [[ $# -eq 0 ]]; then
        echo "Usage: gt <command> [options]"
        echo ""
        echo "Available commands:"
        echo "  push   - Force push (safe)"
        echo "  fetch [--sync-upstream] - Fetch and sync repository"
        return 1
    fi
    
    COMMAND="$1"
    shift  # Remove first argument, pass rest to function
    
    # Map commands to functions
    case "$COMMAND" in
        push)
            git_safe_force_push "$@"
            ;;
        fetch)
            git_fetch "$@"
            ;;
        *)
            echo "❌ Unknown command: $COMMAND"
            echo ""
            echo "Available commands:"
            echo "  push   - Force push (safe)"
            echo "  fetch [--sync-upstream] - Fetch and sync repository"
            return 1
            ;;
    esac
}
