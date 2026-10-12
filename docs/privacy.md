# Privacy and publishing

Keep credentials, machine-specific state and unreviewed downloaded content out of the public repository. The [ignore rules](../.gitignore) and [staged-content guard](../scripts/pre_commit_secret_guard.py) reduce accidental disclosure. They do not remove files already tracked or secrets already present in history.

## What stays out of git

| Ignore entry or location | Contents and reason |
|---|---|
| `data/*`, with `!data/geo/` | Raw CSV copies and hashes, processed records, features, predictions, text stores and local state. These are generated or downloaded inputs, not a portable release bundle. Reviewed geography is the deliberate exception. |
| `data/live/` | Cached gzip poll/archive responses, accepted day partitions, live observations, current state, validation evidence, coverage ledger and nightly status. These belong to the local collector. |
| `data/news/` | SQLite message versions, cursors, observation clocks, validated evidence, model outputs, adjustments and usage. Public posts can still contain personal content or untrusted instructions. Do not publish the store by accident. |
| `data/settings.local.json`, `settings.local.json` | Machine-local news choices and settings. Defaults leave news off. |
| `.env`, `.env.*`, with `!.env.example` | Optional provider credentials. Only the empty template belongs in git. |
| `artifacts/` | Fitted models, calibrators and production generations. A reviewed release bundle needs a separate distribution decision; git ignores these files. |
| `*.session`, `secrets/`, `credentials*`, `auth.json`, `*.pem`, `*.key` | Session files, credential exports and private keys. Do not copy Codex credentials into this project. |
| `.lead/`, `docs/current-state.md` | Local coordination files and the machine-specific inventory. They can contain paths, process details or transient logs. |
| `.venv/`, `__pycache__/`, Ruff/pytest caches, `web/node_modules/`, `web/dist/`, `web/test-results/`, `web/playwright-report/` | Environment, build and test output. Review any screenshot or log separately before sharing it. |

Committed reports and geography are public artifacts. Check their contents and source attribution before release. The ignore rules cover local stores regardless of whether news is enabled.

## ChatGPT login and optional news

The app invokes the installed Codex CLI. **Login with ChatGPT** starts one app-owned CLI login process; Codex opens the browser flow, handles the callback, stores credentials and refreshes them. The backend runs under the same OS account. The app checks CLI status and model availability without opening, copying or logging the credential cache. It does not accept a ChatGPT password. [Official authentication documentation](https://learn.chatgpt.com/docs/auth), [app implementation](../src/airalert/news/auth.py)

Canceling login stops that app-owned login process. Turning news off stops news processing and does not sign out of Codex. Explicit logout affects other Codex use sharing that login on the machine. Login URLs and child stderr are not forwarded to the browser or saved in application logs. [News authentication](news.md#codex-safety-model)

News is off by default. Enabling it fetches public Telegram previews and sends selected message text and structured incident context through Codex to the model service. A local archive is not a claim that inference stays on the machine. Account/workspace data-handling rules apply to the CLI's ChatGPT session. Existing ChatGPT usage allowance is shared with other Codex work. [Official authentication policies](https://learn.chatgpt.com/docs/auth), [news settings](news.md#settings-and-cost-controls)

The runner uses an empty temporary working directory, an environment allowlist, read-only sandboxing, deadlines and structured-output checks. It discards unsafe tool-event results. This guard is reactive; stock CLI execution does not establish a strict no-tools guarantee, and ephemeral sessions do not promise zero CLI logs or authentication refresh writes. [Runner limitations](news.md#codex-safety-model)

Telegram's [content-licensing terms](https://telegram.org/tos/content-licensing) restrict AI use and specify source-user consent for exceptions. The owner's opt-in does not establish that consent. This project has not established a broad reuse license for the previews. [News terms](news.md#telegram-terms)

## Optional alerts.in.ua credential

[Request a token](https://alerts.in.ua/api-request) for the supported API. Put it in the backend process environment or a local `.env`, using [.env.example](../.env.example) as the template. The loader reads only `ALERTS_IN_UA_TOKEN` and does not execute the file. Keep the token out of browser code, requests to unrelated services, logs, screenshots and git. [Token loader](../src/airalert/sources/aiu_api.py)

## Install the pre-commit guard

The [guard](../scripts/pre_commit_secret_guard.py) scans staged added, copied, modified and renamed file blobs. It blocks Gmail addresses, Windows and Git Bash user-directory paths, recognized GitHub/Telegram/OpenAI-style tokens, JWTs and private-key markers. It excludes its own pattern-definition file. A passing check is limited to these patterns and the current index; it is not a scan of every secret type or git history.

From the repository root in PowerShell, install the supplied hook for a clone without an existing pre-commit hook:

```powershell
Copy-Item -LiteralPath scripts/pre-commit -Destination (git rev-parse --git-path hooks/pre-commit)
```

On a POSIX shell:

```sh
cp scripts/pre-commit "$(git rev-parse --git-path hooks/pre-commit)"
chmod +x "$(git rev-parse --git-path hooks/pre-commit)"
```

For an existing hook, add the guard invocation to that hook and preserve its other checks. The supplied [hook](../scripts/pre-commit) runs `uv run --no-project python` against the repository's guard script. Hooks are local and are not installed automatically by cloning. uv and Python must be available when committing.

Run the same staged-content check manually:

```powershell
uv run --no-project python scripts/pre_commit_secret_guard.py
```

A rejection exits nonzero and prints the affected path and pattern category. Review the staged content, remove private material and run the guard again. Do not bypass it to publish a credential or private machine detail.

## Pre-publish checklist

1. Review the exact staged patch with `git diff --cached` and the file list with `git status --short`. Confirm only intended source, docs, reviewed geography and reports are included.
2. Check tracked files with `git ls-files`. Ignore rules do not untrack files that were added earlier. Never commit `.env`, credential caches, local settings, raw live/news stores or personal machine paths.
3. Run `uv run --no-project python scripts/pre_commit_secret_guard.py` after staging. An empty index means there was nothing for it to inspect. Review untracked working files separately before adding them.
4. Review author and committer identities with `git log --format='%an <%ae> | %cn <%ce>'`. Commit metadata can publish a personal email even when every staged file passes. Check the configured identity with `git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT` before the next commit. Keep this output private.
5. Review existing history for credential or personal-data exposure; the hook checks only the next staged content. Revoke any exposed credential promptly and coordinate history remediation with the owner.
6. Check screenshots, copied command output, examples and links for private values. Use repository-relative paths in committed instructions.
7. Preserve source notices and attribution. Check the optional news terms and the [fresh-clone release blocker](../README.md#prepare-models-and-serving-artifacts) before presenting the repository as ready to run.

[Operations](operations.md) covers the service lifecycle. The [README](../README.md) documents optional features, source terms and the safety disclaimer.

## D3 delivery report

**Delivered.** [README.md](../README.md), the short v2 pointer in [report.md](report.md), [overview-v2.md](overview-v2.md), [.env.example](../.env.example), and this privacy guide. Only these assigned files were edited. No commit was made.

**Verified.** Fifteen CLI/module help checks returned exit 0, including the ingest validation/cutover options, v2 evaluation modules, duration production mode, production actions and serving. Bun's install/run interfaces and every documented package script exist. The mapshaper cache command matches the geography implementation. Headline BSS, intervals and duration coverage match the v2 JSON. Removing the pointer restores the original v1 report SHA256 byte for byte. All five working files have zero matches against the guard's exact patterns; the staged guard exits 0 with an empty index. Scoped `git diff --check` exits 0. Local file links and Markdown heading anchors pass except the expected pending operations guide. The documented private paths are ignored; the env template and geography are allowed.

**Decisions/assumptions.** Report served combined probabilities, retain all v1 numbers, and use portable instructions. The lead chose to document the fresh-clone blocker. Model builds, external login and the installed UI were not rerun for this documentation task.

**Open issues.** A fresh clone cannot complete model setup: the committed test2 claim blocks onset training, while production and replay need ignored evaluation artifacts. Calling `train_v2()` directly confirmed rejection before writes with `Final test2 was claimed; v2 selection and evaluation models are frozen`. The operations guide is being written separately; its links were intentionally added without depending on its content.

**Contract change requests.** Before promising a runnable fresh clone, supply a reviewed artifact bundle or a bootstrap entrypoint that preserves the frozen test2 claim, scores and selection. No contract file was edited by D3.

**Dependencies added.** None.

**README commands not verified against code.** None of the documented entrypoints or options is invented. Verification checked dispatch, help and package scripts, not a full install/build/serve workflow. The fresh-clone sequence remains blocked as described above.
