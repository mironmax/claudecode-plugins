# Antigravity probe

How the claims marked *observed (run)* in the
[Antigravity card](../../cards/antigravity.md) were obtained, so they can be
checked again when `agy` updates itself. No Google account is needed.

## Method

Antigravity CLI accepts a Gemini API key instead of a signed-in account, and
sends model requests to `GOOGLE_GEMINI_BASE_URL` when it is set. Pointed at
`gemini_mock.py`, every request the CLI makes to the model is saved, so what
the CLI put in front of the model (hook injections included) can be read
exactly. Hooks are logged by `hook_logger.sh`. What this cannot show is how
a real model weighs the input: it shows placement, not effect.

Use a scratch `HOME`, so the probe never touches a real configuration:

```sh
export HOME=/tmp/agy-probe-home GEMINI_API_KEY=dummy \
       GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:18901
mkdir -p "$HOME/.gemini/antigravity-cli" "$HOME/work/proj"
echo '{"modelProvider": "gemini"}' > "$HOME/.gemini/antigravity-cli/settings.json"
curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- --dir "$HOME/bin"
python3 gemini_mock.py 18901 /tmp/agy-probe/model &
```

Register hooks in `$HOME/.gemini/config/hooks.json`, one logger per event:

```json
{"probe": {
  "SessionStart":  [{"type": "command", "command": "PROBE_LOG=/tmp/agy-probe/hooks /abs/path/hook_logger.sh SessionStart"}],
  "PreInvocation": [{"type": "command", "command": "PROBE_LOG=/tmp/agy-probe/hooks /abs/path/hook_logger.sh PreInvocation"}],
  "PostToolUse":   [{"matcher": "", "hooks": [{"type": "command", "command": "PROBE_LOG=/tmp/agy-probe/hooks /abs/path/hook_logger.sh PostToolUse"}]}]
}}
```

Script the model in `/tmp/agy-probe/model/scenario.json` (see the docstring
of `gemini_mock.py`), run a turn, and read the saved requests:

```sh
cd "$HOME/work/proj" && "$HOME/bin/agy" -p "probe" --output-format stream-json
```

Two prompts in one process, to see what fires per prompt:

```sh
printf '%s\n' '{"event":"user","message":{"content":"one"}}' \
              '{"event":"user","message":{"content":"two"}}' |
  "$HOME/bin/agy" --input-format stream-json --output-format stream-json
```

`agy -p "/hooks"` lists the hooks the CLI loaded, and `--log-file <path>`
records why a hook's reply was rejected. A rejected reply is otherwise
silent.

## Cautions

- `hook_logger.sh` does not save the environment: a hook inherits the CLI's,
  and that can carry credentials.
- A `PreToolUse` hook that answers `{}` denies the tool.
- The CLI updates itself in the background. Record `agy --version` with any
  result.
