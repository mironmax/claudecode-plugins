#!/bin/bash
#
# Claude Code status line — identity + session health, and a quota gauge on disk.
#
# Two rendered lines:
#   user@host  📁 dir  🕐 time  🔗 mcp-servers  [output-style]
#   ⚡ model │ 📊 5h:NN%→reset 7d:NN%→reset │ 💾 cache:NN% │ 📐 ctx:NN%
#
# Side effect that matters: Claude Code pipes a `rate_limits` JSON to this
# command's stdin on every render and to nothing else — the model never sees it,
# and the harness does not persist it. This script writes it to
# ~/.claude/last-limits.json so an agent can read its own remaining quota with
# `jq . ~/.claude/last-limits.json`.
#
# Note: rate_limits reaches this stdin only for Claude.ai subscriber (Pro/Max)
# sessions, and only after the session's first API response. Each window
# (five_hour / seven_day) can be absent independently — the persist block below
# writes whichever arrived and carries the previous value, with its original
# observation stamp, for the one that didn't.
#
# Requires: jq, bash (Git Bash on Windows). Uses python3 for path shortening
# (falls back to basename).
# Install: see recommended-setup/README.md in this repo.

# Suppress all stderr to ensure only stdout is shown
exec 2>/dev/null

# Read JSON input from stdin
input=$(cat)

# Basic info
user=$(whoami)
host=$(hostname -s)
output_style=$(echo "$input" | jq -r '.output_style.name // "default"' 2>/dev/null || echo "default")
model_name=$(echo "$input" | jq -r '.model.display_name // "Claude"' 2>/dev/null || echo "Claude")

# Get current directory (simplified)
current_dir=$(echo "$input" | jq -r '.workspace.current_dir // .cwd // empty' 2>/dev/null)
if [ -z "$current_dir" ] || [ "$current_dir" = "null" ]; then
    current_dir=$(pwd)
fi

# Get project directory for relative path calculation
project_dir=$(echo "$input" | jq -r '.workspace.project_dir // empty' 2>/dev/null)

# Calculate directory display (simplified)
if [ -n "$project_dir" ] && [ "$project_dir" != "null" ]; then
    rel_path=$(python3 -c "import os; print(os.path.relpath('$current_dir', '$project_dir'))" 2>/dev/null || basename "$current_dir")
    [ "$rel_path" = "." ] && rel_path=$(basename "$project_dir")
else
    rel_path=$(echo "$current_dir" | sed "s|$HOME|~|")
    # Truncate if too long
    [ ${#rel_path} -gt 30 ] && rel_path="...$(echo "$current_dir" | rev | cut -d'/' -f1-2 | rev)"
fi

# Current time
current_time=$(date "+%H:%M")

# Check MCP server status from JSON payload with fallback
get_mcp_status() {
    # Extract connected MCP servers from JSON input
    local connected_mcps=$(echo "$input" | jq -r '
        if .mcpServers then
            [.mcpServers[] | select(.status == "connected") | .name] | join(",")
        else
            empty
        end' 2>/dev/null)

    # If JSON doesn't have MCP info, parse MCP configuration files
    if [ -z "$connected_mcps" ] || [ "$connected_mcps" = "" ]; then
        local all_mcps=""

        # Get current directory from JSON or fallback to pwd
        local current_dir=$(echo "$input" | jq -r '.workspace.current_dir // .cwd // empty' 2>/dev/null)
        [ -z "$current_dir" ] && current_dir=$(pwd)

        # Check project-level settings (relative to current directory)
        if [ -f "$current_dir/.claude/settings.json" ]; then
            local project_mcps=$(jq -r '.enabledMcpjsonServers[]? // empty' "$current_dir/.claude/settings.json" 2>/dev/null | tr '\n' ',')
            all_mcps="$all_mcps$project_mcps"
        fi
        if [ -f "$current_dir/.claude/settings.local.json" ]; then
            local project_local_mcps=$(jq -r '.enabledMcpjsonServers[]? // empty' "$current_dir/.claude/settings.local.json" 2>/dev/null | tr '\n' ',')
            all_mcps="$all_mcps$project_local_mcps"
        fi

        # Check user-level MCP configuration (actual Claude Code MCP config)
        if [ -f "$HOME/.claude.json" ]; then
            local user_mcps=$(jq -r '.mcpServers | keys[]? // empty' "$HOME/.claude.json" 2>/dev/null | tr '\n' ',')
            all_mcps="$all_mcps$user_mcps"
        fi

        # Check user-level settings (for other enabledMcpjsonServers)
        if [ -f "$HOME/.claude/settings.json" ]; then
            local user_mcps=$(jq -r '.enabledMcpjsonServers[]? // empty' "$HOME/.claude/settings.json" 2>/dev/null | tr '\n' ',')
            all_mcps="$all_mcps$user_mcps"
        fi
        if [ -f "$HOME/.claude/settings.local.json" ]; then
            local user_local_mcps=$(jq -r '.enabledMcpjsonServers[]? // empty' "$HOME/.claude/settings.local.json" 2>/dev/null | tr '\n' ',')
            all_mcps="$all_mcps$user_local_mcps"
        fi

        # Clean up and deduplicate
        if [ -n "$all_mcps" ]; then
            connected_mcps=$(echo "$all_mcps" | tr ',' '\n' | sort -u | grep -v '^$' | tr '\n' ',' | sed 's/,$//')
        fi
    fi

    # Format MCP status
    if [ -n "$connected_mcps" ] && [ "$connected_mcps" != "" ]; then
        echo "🔗 $connected_mcps"
    else
        echo "🔗 none"
    fi
}

mcp_status=$(get_mcp_status)

# ── Rate limits (subscription quota) ──
FIVE_HR=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty' 2>/dev/null)
SEVEN_DAY=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty' 2>/dev/null)
RESETS_AT=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty' 2>/dev/null)
RESETS_AT_7D=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty' 2>/dev/null)

# ── Cache tokens (current turn) ──
CACHE_READ=$(echo "$input" | jq -r '.context_window.current_usage.cache_read_input_tokens // 0' 2>/dev/null)
CACHE_WRITE=$(echo "$input" | jq -r '.context_window.current_usage.cache_creation_input_tokens // 0' 2>/dev/null)
FRESH=$(echo "$input" | jq -r '.context_window.current_usage.input_tokens // 0' 2>/dev/null)

# ── Context window ──
CTX_PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' 2>/dev/null)

# ── Cache hit rate ──
TOTAL_INPUT=$((CACHE_READ + CACHE_WRITE + FRESH))
if [ "$TOTAL_INPUT" -gt 0 ]; then
    HIT_RATE=$(( CACHE_READ * 100 / TOTAL_INPUT ))
else
    HIT_RATE=-1
fi

# ── Persist live limits to disk so an agent (which never receives this stdin JSON)
#    can read the rolling 5h / 7d subscription quota between statusline renders.
#    The 5h/7d quota is account-global, so any session's render is valid for all.
#    context_pct is whichever session rendered LAST (per-session value differs).
#    Atomic write via tmp+mv.
#    Renders without rate_limits (some stdin payloads omit it) must NOT
#    clobber a previous good reading with nulls — readers treat non-numeric
#    fields as gauge-missing.
#    Each window can be absent INDEPENDENTLY, so persist whenever EITHER
#    arrives and carry the previous file's value for the missing one — a live
#    5h reading must never be dropped just because 7d was absent. A carried
#    value keeps its ORIGINAL observation stamp (five_hour_seen_at /
#    seven_day_seen_at) so it can never masquerade as fresh; updated_at
#    remains "when this file was last written".
#    Render variables are left untouched — the status line honestly shows a
#    dash for whatever this frame did not carry. ──
if [ -n "$FIVE_HR" ] || [ -n "$SEVEN_DAY" ]; then
    LIMITS_FILE="$HOME/.claude/last-limits.json"
    NOW=$(date +%s)
    P_5H="$FIVE_HR";   P_5H_AT="$RESETS_AT";     P_5H_SEEN=""
    P_7D="$SEVEN_DAY"; P_7D_AT="$RESETS_AT_7D";  P_7D_SEEN=""
    [ -n "$FIVE_HR" ]   && P_5H_SEEN="$NOW"
    [ -n "$SEVEN_DAY" ] && P_7D_SEEN="$NOW"
    if [ -f "$LIMITS_FILE" ]; then
        # `// .updated_at` upgrades files written before per-window stamps existed
        if [ -z "$FIVE_HR" ]; then
            P_5H=$(jq -r '.five_hour_pct // empty' "$LIMITS_FILE" 2>/dev/null)
            P_5H_AT=$(jq -r '.five_hour_resets_at // empty' "$LIMITS_FILE" 2>/dev/null)
            P_5H_SEEN=$(jq -r '.five_hour_seen_at // .updated_at // empty' "$LIMITS_FILE" 2>/dev/null)
        fi
        if [ -z "$SEVEN_DAY" ]; then
            P_7D=$(jq -r '.seven_day_pct // empty' "$LIMITS_FILE" 2>/dev/null)
            P_7D_AT=$(jq -r '.seven_day_resets_at // empty' "$LIMITS_FILE" 2>/dev/null)
            P_7D_SEEN=$(jq -r '.seven_day_seen_at // .updated_at // empty' "$LIMITS_FILE" 2>/dev/null)
        fi
    fi
{
  printf '{"five_hour_pct":%s,"five_hour_resets_at":%s,"five_hour_seen_at":%s,"seven_day_pct":%s,"seven_day_resets_at":%s,"seven_day_seen_at":%s,"context_pct":%s,"updated_at":%s}\n' \
    "${P_5H:-null}" "${P_5H_AT:-null}" "${P_5H_SEEN:-null}" \
    "${P_7D:-null}" "${P_7D_AT:-null}" "${P_7D_SEEN:-null}" \
    "${CTX_PCT:-0}" "$NOW"
} > "$LIMITS_FILE.tmp" 2>/dev/null && mv "$LIMITS_FILE.tmp" "$LIMITS_FILE" 2>/dev/null
fi

# ── Color for quota percentage ──
quota_color() {
    local pct=$1
    [ -z "$pct" ] && { echo "\x1b[2m"; return; }
    local pct_int=${pct%.*}
    if   [ "$pct_int" -ge 80 ]; then echo "\x1b[31m"
    elif [ "$pct_int" -ge 50 ]; then echo "\x1b[33m"
    else echo "\x1b[32m"
    fi
}

# ── Color for cache hit rate ──
cache_color() {
    local rate=$1
    if   [ "$rate" -lt 0 ];  then echo "\x1b[2m"
    elif [ "$rate" -ge 70 ]; then echo "\x1b[32m"
    elif [ "$rate" -ge 30 ]; then echo "\x1b[33m"
    else echo "\x1b[31m"
    fi
}

# ── Format reset time ──
reset_str=""
if [ -n "$RESETS_AT" ] && [ "$RESETS_AT" != "null" ]; then
    reset_str=$(date -d "@$RESETS_AT" +%H:%M 2>/dev/null)
fi

# ── Build quota segment ──
if [ -n "$FIVE_HR" ] && [ "$FIVE_HR" != "null" ]; then
    C=$(quota_color "$FIVE_HR")
    PCT_INT=${FIVE_HR%.*}
    quota_seg="${C}5h:${PCT_INT}%\x1b[0m"
    [ -n "$reset_str" ] && quota_seg="${quota_seg}\x1b[2m→${reset_str}\x1b[0m"
else
    quota_seg="\x1b[2m5h:–\x1b[0m"
fi

if [ -n "$SEVEN_DAY" ] && [ "$SEVEN_DAY" != "null" ]; then
    C=$(quota_color "$SEVEN_DAY")
    PCT_INT=${SEVEN_DAY%.*}
    quota_seg="${quota_seg} ${C}7d:${PCT_INT}%\x1b[0m"
    if [ -n "$RESETS_AT_7D" ] && [ "$RESETS_AT_7D" != "null" ]; then
        reset_7d=$(date -d "@$RESETS_AT_7D" +"%a %d" 2>/dev/null)
        [ -n "$reset_7d" ] && quota_seg="${quota_seg}\x1b[2m→${reset_7d}\x1b[0m"
    fi
fi

# ── Build cache segment ──
if [ "$HIT_RATE" -lt 0 ]; then
    cache_seg="\x1b[2m💾 cache:–\x1b[0m"
else
    C=$(cache_color "$HIT_RATE")
    warn=""
    [ "$HIT_RATE" -lt 30 ] && warn=" \x1b[31m⚠\x1b[0m"
    cache_seg="💾 ${C}cache:${HIT_RATE}%\x1b[0m${warn}"
fi

# ── Context window segment ──
ctx_int=${CTX_PCT%.*}
if   [ "$ctx_int" -ge 80 ]; then CTX_C="\x1b[31m"
elif [ "$ctx_int" -ge 60 ]; then CTX_C="\x1b[33m"
else CTX_C="\x1b[2m"
fi
ctx_seg="📐 ${CTX_C}ctx:${ctx_int}%\x1b[0m"

# ── Dim separator ──
SEP="\x1b[2m│\x1b[0m"

# ── Assemble full status line (two lines for ~90-char terminals) ──
# Line 1: identity — who/where/when/connections
line1="\x1b[36m${user}\x1b[0m@\x1b[33m${host}\x1b[0m \x1b[32m📁 ${rel_path}\x1b[0m \x1b[96m🕐 ${current_time}\x1b[0m \x1b[93m${mcp_status}\x1b[0m \x1b[35m[${output_style}]\x1b[0m"
echo -e "$line1"
# Dim horizontal separator matching line 1 visible width (emoji = +1 col each: 📁 🕐 🔗)
line1_plain=$(echo -e "$line1" | sed 's/\x1b\[[0-9;]*m//g')
sep_len=$(( $(echo -n "$line1_plain" | wc -m) + 3 ))
echo -e "\x1b[2m$(printf '─%.0s' $(seq 1 $sep_len))\x1b[0m"
# Line 2: session health — model | quota | cache | context
echo -e "\x1b[1;34m⚡ ${model_name}\x1b[0m ${SEP} 📊 ${quota_seg} ${SEP} ${cache_seg} ${SEP} ${ctx_seg}"
