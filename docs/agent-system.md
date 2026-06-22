# Local Two-Agent Development System

This repository uses a local, subscription-first autonomous workflow. The recommended portable model is a single autonomous work branch named `agent_developed`.

Agents work only on the configured work branch. `main` and `master` are protected human branches. Agents must never work on, push to, or merge into protected branches. A human reviews `agent_developed` later and manually merges or cherry-picks accepted work into `main` or `master`.

This avoids branch stacking and makes the system easier to port to other projects: one repository, one configured work branch, and task-specific review windows recorded in `.agent/run-state/`.

## Branch Configuration

Branch behavior is configured with `.agent/config.env` when present. The file is optional; these are the defaults:

```bash
AGENT_WORK_BRANCH=agent_developed
AGENT_PROTECTED_BRANCHES=main,master
AGENT_BRANCH_MODE=single_work_branch
```

The scripts load only simple `KEY=value` settings and do not print secret values. Keep this file non-secret.

In `single_work_branch` mode:

- Agents must already be on `AGENT_WORK_BRANCH`.
- Agents may commit to `AGENT_WORK_BRANCH`.
- Agents do not create per-task branches by default.
- Agents do not switch branches during the loop.
- Agents never push to `main`, `master`, or any branch listed in `AGENT_PROTECTED_BRANCHES`.
- Agents never merge pull requests.

Create or switch to the work branch manually:

```bash
git switch agent_developed
```

If the branch does not exist yet:

```bash
git switch -c agent_developed
```

## Shared Files

- `.agent/project_brief.md`: Human-owned product context.
- `.agent/operating_rules.md`: Contract both agents must follow.
- `.agent/backlog.md`: Proposed and approved work queues.
- `.agent/tasks/`: JSON task specs and loop-managed task state.
- `.agent/run-state/`: Local runtime task base records, ignored by git.
- `.agent/plans/`: Codex planner result notes.
- `.agent/handoff.md`: Resumable current-state handoff.
- `.agent/reviews/`: Markdown and JSON Codex review outputs.
- `.agent/approvals/`: Human approval records.
- `.agent/notifications/`: Local notification markdown files, ignored by git.
- `.agent/logs/`: Local script logs, ignored by git.
- `.agent/tmp/`: Local planner/reviewer prompts and transient output, ignored by git.

## Environment Check

Run:

```bash
scripts/agent-env-check.sh
```

The check loads `.agent/config.env` if present, prints the current branch and configured work branch, verifies required CLIs, confirms the repo is a git repo, warns on dirty worktrees, and fails closed if known Codex/OpenAI or Claude/Anthropic API-key variables are set:

- `OPENAI_API_KEY`
- `CODEX_API_KEY`
- `ANTHROPIC_API_KEY`

`CLAUDE_CODE_OAUTH_TOKEN` is allowed. Use ChatGPT login for Codex and subscription OAuth for Claude Code.

The check refuses to run on `main`, `master`, any branch in `AGENT_PROTECTED_BRANCHES`, or any branch other than `AGENT_WORK_BRANCH` in `single_work_branch` mode.

## One-Cycle Mode

From `agent_developed`:

```bash
scripts/agent-loop.sh
```

The default runs one complete task cycle: select one actionable task, record the task base commit, implement it, review it, and keep sending that same task back to Claude while Codex returns `needs_revision`, up to the revision limit. It then updates task state, checkpoints, and stops.

`--once` is an explicit alias for the default one-cycle behavior:

```bash
scripts/agent-loop.sh --once
```

Task selection happens before planning. The loop picks the first actionable task in deterministic priority order:

1. `needs_revision`
2. `in_progress`
3. `approved`

If no actionable task exists, it runs the planner unless `--skip-planner` is passed. If the planner creates only proposed tasks, the loop writes an `approval_needed` notification and stops.

Use `--skip-planner` when you only want to continue existing approved or revision work:

```bash
scripts/agent-loop.sh --skip-planner
```

The revision limit defaults to 3 Claude revision passes after review feedback:

```bash
scripts/agent-loop.sh --max-revisions-per-task 2
```

## Task Base Run State

Before Claude changes a selected task, the loop records the current `HEAD` in:

```text
.agent/run-state/<TASK-ID>.json
```

Each run-state file contains:

- `task_id`
- `branch`
- `task_start_commit`
- `started_at`
- `mode: single_work_branch`

If run-state already exists for the task, the loop reuses the same `task_start_commit`. This lets review focus only on the current task even when older accepted or pending work already exists on `agent_developed`.

Reset the selected task base to the current `HEAD` only after manually confirming that older work should be excluded from the next review:

```bash
scripts/agent-loop.sh --reset-task-base
```

`--reset-task-base` is valid only in `single_work_branch` mode on the current work branch.

## Forever Mode

Run:

```bash
scripts/agent-loop.sh --forever
```

Useful options:

```bash
scripts/agent-loop.sh --forever --sleep-seconds 1800
scripts/agent-loop.sh --forever --max-iterations 3
scripts/agent-loop.sh --forever --max-revisions-per-task 3
```

The loop sleeps between cycles and stops after 3 consecutive planner/reviewer/checkpoint failures. `Ctrl+C` stops safely. After `implemented`, `blocked`, `approval_needed`, `usage_limit`, or `auth_failed`, forever mode sleeps before the next outer cycle instead of retrying immediately.

## Approval Gate

Planner output creates proposed tasks and pending approval files. Review `.agent/approvals/pending/`, then approve exactly one task:

```bash
scripts/agent-approve.sh TASK-ID
```

High-risk tasks require explicit approval:

```bash
scripts/agent-approve.sh --allow-high-risk TASK-ID
```

Proposed tasks still require human approval before Claude may implement them.

## Task Status Lifecycle

Allowed task statuses:

- `proposed`: Planned but not approved.
- `approved`: Human approved and eligible for implementation.
- `in_progress`: Selected by the implementer.
- `needs_revision`: Codex review found required fixes inside the original approved scope; Claude may fix these without new approval if the fix stays in that scope.
- `implemented`: Codex review accepted the implementation; human final review and merge/cherry-pick are still required.
- `blocked`: Human decision or external access is needed.
- `rejected`: Human rejected the proposed task.

Implementation priority is deterministic: `needs_revision`, then `in_progress`, then `approved`. `proposed`, `rejected`, `implemented`, and `blocked` are ignored for implementation.

Use the state helper for manual inspection or repair:

```bash
scripts/agent-task-state.py list-actionable
scripts/agent-task-state.py get-next
scripts/agent-task-state.py mark-blocked TASK-ID --reason "human decision needed"
```

## Planner Behavior

Run the planner directly when needed:

```bash
scripts/codex-planner.sh
```

The wrapper gathers context into `.agent/tmp/planner-context.md`, asks Codex for JSON only, validates the output, and materializes files through `scripts/materialize-planner-output.py`. Existing task files are not overwritten.

Planner output is delta-based. Codex should emit full task objects only for new tasks, while `recommended_order` may reference task IDs that already exist in `.agent/tasks/`. Proposed tasks require human approval before implementation.

## Implementer Behavior

Run the implementer directly for the next actionable task:

```bash
scripts/claude-implementer.sh
```

Or pass an explicit task id:

```bash
scripts/claude-implementer.sh TASK-ID
```

In `single_work_branch` mode, the implementer verifies the current branch is `AGENT_WORK_BRANCH`, verifies it is not protected, does not create or switch branches, records or reuses `.agent/run-state/<TASK-ID>.json`, runs Claude Code with subscription auth, asks Claude to update `.agent/handoff.md`, commits implementation changes when possible, and may push or open a draft PR from the work branch when possible.

For a `needs_revision` task, the prompt tells Claude to read the latest `.agent/reviews/REVIEW-<TASK-ID>-*.json` and fix only `required_fixes` within the original approved task scope. It does not create a new task or ask for new approval for in-scope review fixes.

Claude must not mark the task implemented. The review verdict controls final status.

## Review Behavior

Run the reviewer after implementation:

```bash
scripts/codex-reviewer.sh TASK-ID
```

In `single_work_branch` mode, the reviewer reads `.agent/run-state/<TASK-ID>.json` and reviews only:

```text
git diff <task_start_commit>..HEAD
```

It does not review `main...HEAD` or any setup branch diff. Older work already present on `AGENT_WORK_BRANCH` before `task_start_commit` is ignored for this task review.

The wrapper provides Codex with the current branch, configured work branch, branch mode, `task_start_commit`, current `HEAD`, git status, diff stat, full diff, task JSON, and `.agent/handoff.md` content. Codex is instructed not to run shell, Bash, git, gh, or file-inspection commands and to emit structured review JSON only.

The reviewer writes gathered context to `.agent/tmp/reviewer-context-<TASK-ID>-<timestamp>.md`. Shell-generated reviewer context uses plain indented text sections instead of Markdown backtick fences so task JSON, handoff text, and diffs cannot be interpreted by Bash while the context file is built. If `.agent/run-state/<TASK-ID>.json` is missing, invalid, or lacks `task_start_commit` in `single_work_branch` mode, the reviewer writes a blocked review artifact before calling Codex.

The reviewer writes:

- `.agent/reviews/REVIEW-<TASK-ID>-<timestamp>.json`
- `.agent/reviews/REVIEW-<TASK-ID>-<timestamp>.md`

The JSON verdict is exactly one of:

- `accepted`: loop marks the task `implemented`, writes a completion note, and notifies the human that final review and merge/cherry-pick are required.
- `needs_revision`: loop marks the same task `needs_revision`; the same one-cycle run sends it back to Claude without new approval if fixes stay in scope, until the review is accepted, blocked, or `--max-revisions-per-task` is reached.
- `blocked`: loop marks the task `blocked` and notifies the human.

If run-state is missing or invalid, or the wrapper cannot gather the task diff, the reviewer writes a blocked review artifact instead of falling back to `main...HEAD` or a setup branch diff.

Accepted does not mean merged. Humans still review and merge/cherry-pick to protected branches.

## Notifications

Run manually when needed:

```bash
scripts/agent-notify.sh approval_needed
scripts/agent-notify.sh review_ready TASK-ID
```

Supported reasons are `approval_needed`, `implementation_blocked`, `review_ready`, `auth_failed`, `usage_limit`, `loop_failed`, and `max_revisions_reached`. Each notification includes the current work branch, task id, task status, whether human approval is needed, whether human final review/merge is needed, pending approvals, and the next command for the human.

GitHub issue creation is best-effort. If `gh` is authenticated and a remote exists, approval and review notifications can create or comment on issues labeled `agent/approval-needed` or `agent/review-needed`. GitHub is not required for success.

Outbound Telegram is also best-effort. If `scripts/telegram-send.sh` is present
and `.agent/telegram.env` (or the `TELEGRAM_*` environment) is configured, every
notification is pushed to the allowlisted chat(s) with a short summary: reason,
task id and status, branch, whether approval or final review is needed, the
latest commit subject, the files it changed, and the suggested next command.
Accepted-task (`review_ready`) messages also include the Codex review summary.
Delivery failures never break the loop. This is what tells you on your phone when
a run finishes, stalls, or needs approval. Run it manually with
`scripts/telegram-send.sh "message"`.

## Telegram Control Channel

`scripts/agent-telegram-bot.py` is an optional remote control surface. It
long-polls the Telegram Bot API (`getUpdates`), so it needs no inbound port and
no webhook. Only chat IDs in an explicit allowlist may issue commands; every
other message is logged and ignored (fail closed). The bot uses the Python
standard library only and never logs the bot token.

Configure it by copying the template and filling in real values, or by exporting
the variables in your shell. Environment variables take precedence over the file.

```bash
cp .agent/telegram.env.example .agent/telegram.env
# edit .agent/telegram.env:
#   TELEGRAM_BOT_TOKEN=...           (from @BotFather)
#   TELEGRAM_ALLOWED_CHAT_IDS=...    (comma-separated; the bot refuses to start if empty)
```

`.agent/telegram.env` is gitignored. Do not commit real tokens.

Find your chat ID (needed for the allowlist) by setting the token, messaging
your bot, then running the bootstrap mode. It needs only the token, never runs
commands, and does not advance the offset:

```bash
scripts/agent-telegram-bot.py --print-chat-ids
```

Put the printed chat ID in `TELEGRAM_ALLOWED_CHAT_IDS`, then run the bot in its
own long-lived process:

```bash
scripts/agent-telegram-bot.py
```

The bot is a service: it must stay running for commands to be received. Nothing
starts it automatically. Use `--once` to drain pending updates a single time
(useful for testing).

Supported commands:

- `/status`: branch, loop pause state, actionable tasks, pending approvals, and pending inbox requests (via `scripts/agent-status.sh`).
- `/approve TASK-ID`: approve a proposed task (`scripts/agent-approve.sh`). Append `--allow-high-risk` to approve a high-risk task.
- `/reject TASK-ID reason`: reject a proposed or blocked task (`scripts/agent-reject.sh`); removes any pending approval request and writes a record under `.agent/approvals/rejected/`.
- `/pause`: write `.agent/state/paused`. The loop checks this before each cycle and will not start new work until resumed.
- `/resume`: remove `.agent/state/paused`.
- `/task your request`: write a request into `.agent/inbox/` for the planner to consider.
- `/goal your high-level direction`: write a high-level goal into `.agent/inbox/` for the planner to consider.
- `/details TASK-ID`: print a task's title, status, risk, area, approver, and latest review.
- `/help`: list the supported commands.

The bot maps commands onto the same scripts a human runs locally; it adds no new
ability to merge or push to protected branches. `/task` and `/goal` drop advisory
markdown files into `.agent/inbox/`, which `scripts/codex-planner.sh` includes in
the planner context on the next planning run. Both `.agent/inbox/` and
`.agent/state/` are gitignored runtime directories; clear processed inbox files
manually when they are no longer relevant.

## Claude Idea Pipeline (Claude-only, interactive)

Separate from the Codex plan/implement/review loop, this pipeline lets you turn
an idea into reviewed work entirely through Claude Code, with you confirming the
plan in the middle. It never uses Codex.

Flow:

1. You send `/idea <your idea>` on Telegram. The bot files it as
   `.agent/ideas/IDEA-<n>.json` (state `new`).
2. `scripts/idea-worker.py` picks it up and runs `scripts/claude-planner.sh`
   (Claude, read-only) to produce a short plan, then sends the plan to you
   (state `planned`).
3. You reply `/confirm IDEA-<n>` (or just `/confirm` for the latest). The worker
   runs `scripts/claude-idea-implement.sh` (Claude) to build it (state
   `approved` → `done`), commits on the work branch, and messages you a summary.
4. `/cancel IDEA-<n>` drops an idea; `/ideas` lists active ones.

The bot only edits the idea files; the worker does the Claude work, so the bot
never blocks. Run the worker continuously:

```bash
scripts/idea-worker.py                 # forever, polls every 20s
scripts/idea-worker.py --once          # advance one idea and exit
```

### Usage-limit handling and /continue

If Claude hits a usage/session limit mid-plan or mid-implementation, the worker
checkpoints the work, estimates when the next session starts, and messages you,
e.g. "paused (usage limit) … next session ~14:30 local (in ~2h 10m). Send
`/continue IDEA-<n>`". The estimate is parsed from Claude's output when possible
and otherwise falls back to a 5-hour window (`CLAUDE_USAGE_WINDOW_HOURS`).

`/continue` clears the pause and the worker resumes the **same Claude session**
(`--resume <session id>`), falling back to a fresh run with the plan and handoff
if the session is gone. `/continue` works at any time — if the limit has not
actually reset, the idea simply re-pauses with an updated estimate. Set
`IDEA_AUTO_CONTINUE=1` to let the worker auto-resume once the estimate passes
instead of waiting for `/continue`. A `max turns` stop pauses the same way but
needs no wait — just `/continue`.

## Always-On Operation

The loop and the Telegram bot are both long-running processes that nothing starts
automatically. To run the system continuously (research/develop on its own,
steerable from Telegram), keep both alive with a supervisor:

- systemd user services (survives logout/reboot): see `deploy/systemd/README.md`.
- Portable fallback (`scripts/agent-supervisor.sh`), which restarts a child on
  crash with capped backoff:

  ```bash
  scripts/agent-supervisor.sh telegram -- scripts/agent-telegram-bot.py &
  AGENT_AUTO_APPROVE=low CLAUDE_MAX_TURNS=40 \
    scripts/agent-supervisor.sh loop -- scripts/agent-loop.sh --forever &
  ```

  `CLAUDE_MAX_TURNS` (default 16) is the implementer's per-task turn budget;
  raise it so larger tasks finish in one pass instead of stopping as WIP.

  Stop a supervised process cleanly: `touch .agent/state/supervisor-<name>.stop`.

### Keeping the queue fed (approval policy)

In `--forever`, when only `proposed` tasks exist the loop re-plans, writes an
`approval_needed` notification, and waits — it does not implement unapproved
work. To keep it moving unattended, set `AGENT_AUTO_APPROVE`:

```bash
AGENT_AUTO_APPROVE=low scripts/agent-loop.sh --forever      # auto-approve low-risk only
AGENT_AUTO_APPROVE=medium scripts/agent-loop.sh --forever   # low + medium
```

The loop runs `scripts/agent-autoapprove.sh` before each selection. **High-risk
tasks are never auto-approved**; they always require an explicit human approval
(`scripts/agent-approve.sh --allow-high-risk TASK-ID` or Telegram `/approve`).
Leave `AGENT_AUTO_APPROVE` unset to require human approval for every task (the
default, unchanged behavior). You can also approve on demand from Telegram, or
keep a pre-approved backlog under "Approved Now" in `.agent/backlog.md`.

`scripts/agent-autoapprove.sh [--max-risk low|medium]` can also be run by hand.

## Human Final Integration

Agents do not merge into protected branches. After a task is accepted, a human can inspect the work branch and manually merge or cherry-pick:

```bash
git switch main
git merge --no-ff agent_developed
```

Or cherry-pick specific commits:

```bash
git switch main
git cherry-pick <commit>
```

Use the repository's normal human review process before either operation.

## Recovery

For Claude auth errors:

1. Inspect `.agent/handoff.md` and the latest `.agent/logs/claude-implementer-*.log`.
2. Re-authenticate Claude Code with subscription OAuth.
3. Confirm API-key environment variables are unset.
4. Re-run `scripts/agent-loop.sh` or `scripts/claude-implementer.sh TASK-ID` from `AGENT_WORK_BRANCH`.

For usage, auth, or max-turn stops:

1. Inspect `.agent/handoff.md`.
2. Re-authenticate when needed, wait for usage to reset, or increase `CLAUDE_MAX_TURNS` for a scoped retry.
3. Resume the same task with `scripts/agent-loop.sh`.

For planner failures, inspect `.agent/logs/agent-loop-events.log` and the latest Codex planner log. Already-actionable approved, in-progress, or revision work can still continue through the loop because task selection happens before planning.

For reviewer or checkpoint failures, inspect `.agent/logs/agent-loop-events.log` and rerun one cycle after fixing the cause. For missing or wrong task bases, rerun with `--reset-task-base` only after confirming the current `HEAD` should be the new review base.

For max revision stops, inspect the latest review JSON and `.agent/handoff.md`. The task is marked `blocked`; a human can manually intervene or approve a new scoped task.

## Avoid Paid API Usage

- Use local Codex CLI with ChatGPT login.
- Use Claude Code with subscription OAuth.
- Do not set `OPENAI_API_KEY`, `CODEX_API_KEY`, `ANTHROPIC_API_KEY`, or `ANTHROPIC_AUTH_TOKEN`.
- Do not add secrets or API keys to files, prompts, logs, tasks, or commits.
- Do not change scripts to allow paid key-backed automation.
