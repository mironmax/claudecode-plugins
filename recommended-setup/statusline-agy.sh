#!/usr/bin/env bash
# Antigravity CLI status line. Two data rows with a divider; Python 3 only.
# Persist quota observations separately from the time the display was rendered.
exec python3 /dev/fd/3 3<<'PY'
import datetime as dt
import getpass
import json
import math
import os
from pathlib import Path
import socket
import sys
import tempfile
import time


def number(value, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if 0 <= value <= maximum and math.isfinite(value) else None


def clean(value):
    # Paths and model names are data, including quotes and backslashes.
    return ''.join(c for c in str(value) if c.isprintable())


def reset_epoch(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.timestamp() if stamp.tzinfo else None
    except (ValueError, OverflowError):
        return None


def quota_bucket(payload, names, group_words):
    quota = payload.get('quota')
    if not isinstance(quota, dict):
        return {}
    for name in names:
        if isinstance(quota.get(name), dict):
            return quota[name]
    groups = quota.get('groups')
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        if not any(word in str(group.get('name', '')).lower() for word in group_words):
            continue
        buckets = group.get('buckets')
        if isinstance(buckets, list) and buckets and isinstance(buckets[0], dict):
            return buckets[0]
    return {}


def save_limits(payload, directory, now):
    target = directory / 'last-limits.json'
    try:
        cached = json.loads(target.read_text())
        limits = cached if isinstance(cached, dict) else {}
    except (OSError, ValueError):
        limits = {}
    fresh = set()
    for prefix, names, words in (
        ('gemini', ('gemini-weekly', 'gemini'), ('gemini',)),
        ('third_party', ('3p-weekly', 'claude-weekly'), ('claude', 'gpt', '3p')),
    ):
        bucket = quota_bucket(payload, names, words)
        fraction = number(bucket.get('remaining_fraction'), 1)
        if fraction is not None:
            limits[prefix + '_pct_remaining'] = round(fraction * 100, 2)
            reset = bucket.get('reset_time')
            limits[prefix + '_resets_at'] = reset if reset_epoch(reset) is not None else None
            limits[prefix + '_seen_at'] = now
            fresh.add(prefix)
    context = payload.get('context_window')
    context = context if isinstance(context, dict) else {}
    model = payload.get('model')
    model = model if isinstance(model, dict) else {}
    limits.update(context_pct=number(context.get('used_percentage'), 100),
                  model=clean(model.get('display_name') or model.get('id') or 'unknown'),
                  updated_at=now)
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Each renderer owns a temporary file; a second process cannot rename it.
        with tempfile.NamedTemporaryFile(mode='w', dir=directory,
                prefix='.last-limits-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(limits, stream, ensure_ascii=False, allow_nan=False)
            stream.write('\n')
        os.replace(temporary, target)
    except (OSError, ValueError):
        pass  # A read-only home must not break the display.
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return limits, fresh


def styled(text, color):
    return f'\033[{color}m{text}\033[0m'


def quota_text(label, prefix, limits, fresh, now):
    pct = number(limits.get(prefix + '_pct_remaining'), 100)
    stamp = reset_epoch(limits.get(prefix + '_resets_at'))
    if prefix not in fresh and stamp is not None and stamp <= now:
        return styled(label + ':– (reset passed)', 2)
    if pct is None:
        return styled(label + ':–', 2)
    text = f'{label}:{round(pct)}%'
    color = 32 if pct >= 50 else 33 if pct >= 20 else 31
    if prefix not in fresh:
        text += ' (cached)'
    if stamp is not None:
        text += '→' + dt.datetime.fromtimestamp(stamp).astimezone().strftime('%a %H:%M')
    return styled(text, color)


def mcp_text(payload, home):
    servers = payload.get('mcpServers')
    items = servers.items() if isinstance(servers, dict) else (
        ((str(i), entry) for i, entry in enumerate(servers)) if isinstance(servers, list) else [])
    connected = [clean(entry.get('name') or name) for name, entry in items
                 if isinstance(entry, dict) and entry.get('status') == 'connected']
    if connected:
        return '🔗 ' + ','.join(connected)
    if servers is not None:
        return '🔗 none connected'
    installed = home / '.gemini/config/plugins/knowledge-graph'
    return '🔗 kg installed' if installed.is_dir() else '🔗 MCP unknown'


def render(payload, home, limits, fresh, now):
    workspace = payload.get('workspace')
    workspace = workspace if isinstance(workspace, dict) else {}
    current = workspace.get('current_dir') or payload.get('cwd') or os.getcwd()
    project = workspace.get('project_dir')
    current = current if isinstance(current, str) else os.getcwd()
    if isinstance(project, str) and project and project != str(home):
        location = os.path.relpath(current, project)
        if location == '.':
            location = os.path.basename(project)
    else:
        location = '~' + current[len(str(home)):] if (
            current == str(home) or current.startswith(str(home) + '/')) else current
        if len(location) > 35:
            location = '…/' + '/'.join(current.rstrip('/').split('/')[-2:])
    clock = dt.datetime.fromtimestamp(now).astimezone().strftime('%H:%M')
    version = clean(payload.get('version') or 'unknown')
    line1 = (styled(clean(getpass.getuser()), 36) + '@' + styled(socket.gethostname().split('.')[0], 33)
             + '  ' + styled('📁 ' + clean(location), 32) + '  ' + styled('🕐 ' + clock, 96)
             + '  ' + styled(mcp_text(payload, home), 93) + '  ' + styled('[agy v' + version + ']', 35))
    quota = '  '.join(quota_text(label, prefix, limits, fresh, now)
                      for label, prefix in (('Gemini', 'gemini'), ('3P', 'third_party')))
    pct = limits.get('context_pct')
    context = styled('📐 ctx:–', 2) if pct is None else styled(
        f'📐 ctx:{round(pct)}%', 31 if pct >= 80 else 33 if pct >= 50 else 36)
    tokens = payload.get('context_window') or {}
    tokens = number(tokens.get('total_input_tokens'), sys.maxsize) if isinstance(tokens, dict) else None
    if tokens is not None and tokens >= 1000:
        context += styled(f' ({int(tokens // 1000)}k toks)', 2)
    sep = ' ' + styled('│', 2) + ' '
    line2 = styled('⚡ ' + limits['model'], '1;34') + sep + '📊 ' + quota + sep + context
    print(line1)
    width = number(payload.get('terminal_width'), 1000)
    print(styled('─' * max(20, min(int(width or 80), 80)), 2))
    print(line2)


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return
    if not isinstance(payload, dict):
        return
    home, now = Path.home(), int(time.time())
    limits, fresh = save_limits(payload, home / '.gemini/antigravity-cli', now)
    render(payload, home, limits, fresh, now)


if __name__ == '__main__':
    main()
PY
