# Always-on operation with systemd (user services)

These user units keep the two long-running pieces of the agent system alive and
restart them on crash or reboot:

- `watchagent-telegram.service` — the Telegram inbound command bot.
- `watchagent-loop.service` — the autonomous plan/implement/review loop
  (`scripts/agent-loop.sh --forever`).

The unit files contain a `__REPO_DIR__` placeholder. The install commands below
substitute it with your real repository path.

## Prerequisites

- The repository is checked out on the configured work branch (`agent_developed`).
  The loop's environment check refuses to run on `main`/`master`.
- Codex (ChatGPT login) and Claude Code (subscription OAuth) are already
  authenticated for your user; their credentials live under `$HOME`.
- No `OPENAI_API_KEY` / `CODEX_API_KEY` / `ANTHROPIC_API_KEY` in your environment.
- `.agent/telegram.env` is filled in (token + allowlist).

## Install

```bash
cd /path/to/watchagent           # your repo
mkdir -p ~/.config/systemd/user

for unit in watchagent-telegram watchagent-loop; do
  sed "s#__REPO_DIR__#$PWD#g" "deploy/systemd/$unit.service" \
    > ~/.config/systemd/user/"$unit.service"
done

systemctl --user daemon-reload
systemctl --user enable --now watchagent-telegram.service
systemctl --user enable --now watchagent-loop.service

# Keep the services running after you log out / across reboots:
sudo loginctl enable-linger "$USER"
```

## Operate

```bash
systemctl --user status watchagent-loop.service
journalctl --user -u watchagent-loop.service -f
journalctl --user -u watchagent-telegram.service -f

# Pause the loop without stopping the service (Telegram /pause does the same):
touch .agent/state/paused        # loop skips cycles until removed
rm -f .agent/state/paused        # resume

systemctl --user restart watchagent-loop.service
systemctl --user stop watchagent-loop.service
```

## Approval policy

`watchagent-loop.service` sets `AGENT_AUTO_APPROVE=low`, so the loop
auto-approves only **low-risk** proposed tasks to avoid stalling on the approval
gate. Medium-risk requires `AGENT_AUTO_APPROVE=medium`; **high-risk is never**
auto-approved and always needs a human (`scripts/agent-approve.sh
--allow-high-risk TASK-ID`, or Telegram `/approve`). Remove the
`Environment=AGENT_AUTO_APPROVE=...` line to require manual approval for
everything.

## No systemd? Use the portable supervisor

```bash
scripts/agent-supervisor.sh telegram -- scripts/agent-telegram-bot.py &
AGENT_AUTO_APPROVE=low scripts/agent-supervisor.sh loop -- scripts/agent-loop.sh --forever &
```

Stop a supervised process: `touch .agent/state/supervisor-<name>.stop`.
