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
| `reports/v2/track_record.json` | Rolling live scores written by the server's nightly job. This is git-ignored runtime state; once written, its scores appear in the report page's notes. |
| `*.session`, `secrets/`, `credentials*`, `auth.json`, `*.pem`, `*.key` | Session files, credential exports and private keys. Do not copy Codex credentials into this project. |
| `.lead/`, `docs/current-state.md` | Local coordination files and the machine-specific inventory. They can contain paths, process details or transient logs. |
| `.venv/`, `__pycache__/`, Ruff/pytest caches, `web/node_modules/`, `web/dist/`, `web/test-results/`, `web/playwright-report/` | Environment, build and test output. Review any screenshot or log separately before sharing it. |

Committed reports and geography are public artifacts. Check their contents and source attribution before release. The ignore rules cover local stores regardless of whether news is enabled.

## ChatGPT login and optional news

Optional news requires the Codex CLI installed and signed in with ChatGPT. Settings → **Login with ChatGPT** starts one app-owned `codex login` process; Codex opens the browser flow, handles the callback, stores credentials and refreshes them. The backend runs under the same OS account. The app checks CLI status and model availability without opening, copying or logging the credential cache. The app never sees your password or tokens. [Official authentication documentation](https://learn.chatgpt.com/docs/auth), [app implementation](../src/airalert/news/auth.py)

Canceling login stops that app-owned login process. Turning news off stops news processing and does not sign out of Codex. Explicit logout affects other Codex use sharing that login on the machine. Login URLs and child stderr are not forwarded to the browser or saved in application logs. [News authentication](news.md#codex-safety-model)

News is off by default. Enabling it fetches public Telegram previews and sends selected message text and structured incident context through Codex to the model service. A local archive is not a claim that inference stays on the machine. Account/workspace data-handling rules apply to the CLI's ChatGPT session. Existing ChatGPT usage allowance is shared with other Codex work. [Official authentication policies](https://learn.chatgpt.com/docs/auth), [news settings](news.md#settings-and-cost-controls)

The runner uses an empty temporary working directory, an environment allowlist, read-only sandboxing, deadlines and structured-output checks. It discards unsafe tool-event results. This guard is reactive; stock CLI execution does not establish a strict no-tools guarantee, and ephemeral sessions do not promise zero CLI logs or authentication refresh writes. [Runner limitations](news.md#codex-safety-model)

Telegram's [content-licensing terms](https://telegram.org/tos/content-licensing) restrict AI use and specify source-user consent for exceptions. The owner's opt-in does not establish that consent. This project has not established a broad reuse license for the previews. [News terms](news.md#telegram-terms)

## Optional alerts.in.ua credential

[Request a token](https://alerts.in.ua/api-request) for the supported API. Put it in the backend process environment or a local `.env`, using [.env.example](../.env.example) as the template. The loader reads only `ALERTS_IN_UA_TOKEN` and does not execute the file. Keep the token out of browser code, requests to unrelated services, logs, screenshots and git. [Token loader](../src/airalert/sources/aiu_api.py)

## Install the pre-commit guard

The [guard](../scripts/pre_commit_secret_guard.py) scans staged added, copied, modified and renamed file blobs and their names. It blocks personal-provider email addresses, literal home/workspace paths, process identifiers, recognized GitHub/Telegram/OpenAI-style tokens, JWTs and private-key markers. PNG checks inspect text metadata rather than compressed image pixels. The script also checks the next author and committer email, including Git's environment and command overrides. It accepts the anonymous project email or a GitHub no-reply email. A passing check covers these patterns and the current index; it does not scan every secret type or git history.

This repository uses a local anonymous identity. To apply the same identity in another clone:

```sh
git config --local user.name "Air Alert Contributors"
git config --local user.email "airalert@example.invalid"
```

These settings affect only this clone. Git's global identity stays unchanged. A personal email supplied by an environment variable or command override still causes the hook to reject the commit.

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

A rejection exits nonzero and prints pattern categories or the rejected identity role. It does not print matched values or filenames, which can themselves contain private data. Review the staged content, remove private material and run the guard again. Do not bypass it to publish a credential or private machine detail.

## History remediation status

The owner authorized removing private information from local Git history before publication. Current documentation now uses portable paths or redacted tool-safety examples. The staged guard also blocks future private commit emails. Its installed local hook matches the supplied hook.

The prepared rewrite scans all local refs, every reachable blob, commit/tag messages, tree/ref names and author/committer/tagger metadata. It anonymizes contributor identities, preserves commit dates and parent links, retains every ref, and verifies the final tree against the intended sanitization. Genuine private matches in binary metadata stop the rewrite for review. The current dry-run covers 170 commits and 133 refs; the transformed content and metadata have zero remaining matches.

History execution and the final object audit are pending. The lead will verify the rewrite before expiring reflogs and pruning unreachable private objects. No remote operation is part of this remediation. Runtime data and Codex credentials remain outside this process.

## Pre-publish checklist

1. Review the exact staged patch with `git diff --cached` and the file list with `git status --short`. Confirm only intended source, docs, reviewed geography and reports are included.
2. Check tracked files with `git ls-files`. Ignore rules do not untrack files that were added earlier. Never commit `.env`, credential caches, local settings, raw live/news stores or personal machine paths.
3. Run `uv run --no-project python scripts/pre_commit_secret_guard.py` after staging. An empty index means there was nothing for it to inspect. Review untracked working files separately before adding them.
4. Review author and committer identities with `git log --format='%an <%ae> | %cn <%ce>'`. Commit metadata can publish a personal email even when every staged file passes. Check the configured identity with `git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT` before the next commit. Keep this output private.
5. Review existing history for credential or personal-data exposure; the hook checks only the next staged content. Revoke any exposed credential promptly and coordinate history remediation with the owner.
6. Check screenshots, copied command output, examples and links for private values. Use repository-relative paths in committed instructions.
7. Preserve source notices and attribution. Check the optional news terms and the [fresh-clone Quickstart](../README.md#quickstart-from-a-fresh-clone) before publishing run instructions.

[Operations](operations.md) covers the service lifecycle. The [README](../README.md) documents optional features, source terms and the safety disclaimer.

## D3 delivery report

**Delivered.** [README.md](../README.md), the short v2 pointer in [report.md](report.md), [overview-v2.md](overview-v2.md), [.env.example](../.env.example), and this privacy guide. Only these assigned files were edited. No commit was made.

**Verified.** Fifteen CLI/module help checks returned exit 0, including the ingest validation/cutover options, v2 evaluation modules, duration production mode, production actions and serving. Bun's install/run interfaces and every documented package script exist. The mapshaper cache command matches the geography implementation. Headline BSS, intervals and duration coverage match the v2 JSON. Removing the pointer restores the original v1 report SHA256 byte for byte. All five working files have zero matches against the guard's exact patterns; the staged guard exits 0 with an empty index. Scoped `git diff --check` exits 0. Local file links and Markdown heading anchors were checked; the operations guide was pending at that delivery and is now available. The documented private paths are ignored; the env template and geography are allowed.

**Decisions/assumptions.** Report served combined probabilities, retain all v1 numbers, and use portable instructions. The initial delivery documented the then-current fresh-clone blocker; the bootstrap entrypoint now resolves it. Model builds, external login and the installed UI were not rerun for that documentation task.

**Open issues.** The earlier fresh-clone setup blocker and pending operations guide are resolved. Use `uv sync` → `uv run airalert setup` → `uv run airalert serve`, then start the dashboard as the [Quickstart](../README.md#quickstart-from-a-fresh-clone) describes. The [independent v2 audit](audit-v2.md#5-literal-fresh-clone-quickstart) completed setup in 524.08 seconds, about nine minutes, without copying ignored evaluation artifacts into the clone. This resolves the setup blocker; other findings remain recorded in that audit.

**Contract change requests.** The requested bootstrap entrypoint is implemented and preserves frozen evaluation. No outstanding setup contract change is needed. No contract file was edited by D3.

**Dependencies added.** None.

**README commands not verified against code.** None of the documented entrypoints or options is invented. The original D3 verification checked dispatch, help and package scripts. The later independent v2 audit verified the fresh-clone setup and serving workflow.

## Y3 docs fixes, AGENTS.md rule 9 report

**Delivered.** `README.md`, `docs/privacy.md`, `docs/overview-v2.md` and `docs/audit.md`. Corrected V2-08's setup and runtime-report descriptions, clarified the news login requirement, and removed V2-10's process identifiers and the audit's absolute repository path. No commit was made.

**Verified.** Read `cli.py` and `setup.py`. Disposable checks run with `uv run --no-sync python -` returned:

- `PASS: 18 documented airalert invocations match cli.py; Quickstart exact`. Each invocation's help returned exit 0. The README's first command block is exactly `uv sync`, `uv run airalert setup`, `uv run airalert serve`.
- Six `uv run --no-sync python -m <module> <documented arguments> --help` checks returned exit 0: ingest validation and cutover backfill, production refit and forecast, duration production mode, and static geography. The documented `build_v2` callable, Python scripts and five Bun package scripts also exist.
- The ten-line relative-link check returned `{"checked": 117, "unique_targets": 77, "broken": []}`. It checks tracked destinations and Markdown heading anchors, so a locally present ignored file cannot pass as a repository artifact.
- `PASS: 4 files scanned; 0 PIDs, absolute machine/home paths or machine identifiers`. Public source domains and standard loopback addresses were retained.
- `git check-ignore -v reports/v2/track_record.json` matched `.gitignore`'s `reports/v2/track_record.json` rule. Scoped `git diff --check -- README.md docs/privacy.md docs/overview-v2.md docs/audit.md` returned exit 0 with no output. Final `git diff --stat` lists only the four delivered files, with `4 files changed, 50 insertions(+), 17 deletions(-)`.

The broken references found and corrected were:

| Source | Old relative target | Correction |
|---|---|---|
| `README.md` | `reports/v2/track_record.json` | Removed the link; describe nightly runtime state and report-page notes. |
| `docs/privacy.md` | `../README.md#prepare-models-and-serving-artifacts` | Point to `../README.md#quickstart-from-a-fresh-clone`. |
| `docs/overview-v2.md`, ignored data/models row | `../README.md#prepare-models-and-serving-artifacts` | Point to the current Quickstart and setup audit. |
| `docs/overview-v2.md`, legacy backtest row | `../README.md#prepare-models-and-serving-artifacts` | Point to the current Quickstart. |
| `docs/overview-v2.md` | `../reports/v2/track_record.json` | Removed the link; describe nightly runtime state and report-page notes. |

**Decisions/assumptions.** The independent v2 audit supplies the 524.08-second setup evidence. This documentation task checked commands and links without rerunning setup, model fitting, enabled-news login or the UI workflow. The v1 audit's measurements and shutdown meaning remain unchanged.

**Open issues.** Historical copies of the removed machine details remain for the lead's history remediation. The shared worktree contained unrelated edits at entry; those were left to their owners. Y3 edited only its four owned documents.

**Contract change requests.** None.

**Dependencies added.** None.
