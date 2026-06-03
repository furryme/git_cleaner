# CLAUDE.md — git-cleaner

## What this is

A Python CLI tool that sanitizes internal git repos for public release. It clones the source repo, then applies 4 pipeline steps to a working copy — the **source is never modified**.

## Architecture

```
git_cleaner/
  main.py       — CLI entry (argparse), dry-run mode
  config.py     — YAML loader, deep-merge with defaults, validation
  sanitize.py   — History rewrite (commit-tree) + file content redaction + file exclusion
  history.py    — Branch cleanup, merge squash, linearize
  copyright.py  — Header injection by file extension, comment-style detection
  license.py    — LICENSE file generation (mit/apache2/bsd3/gpl3/mpl2), PR template
  runner.py     — Orchestrator: clone → sanitize → history → copyright → license → cleanup
```

## Key Implementation Details

- **Author rewrite**: Uses `git commit-tree` (not cherry-pick or filter-branch) to create new commits with sanitized author/committer. Builds a parent_map tracking old_sha → new_sha, then uses `update-ref` to swap the branch pointer.
- **Content sanitization (history-wide)**: Pre-scans all unique blobs across history via `_build_blob_map` → `{old_sha: new_sha}`. During the `commit-tree` loop, each commit's tree is rebuilt with `_filter_tree` (uses `read-tree` + `update-index --cacheinfo` + `write-tree`, index swap for isolation). Old sensitive blobs are unreachable after rewrite, pruned via `reflog expire` + `gc --aggressive`. A post-rewrite working tree scan acts as a safety net.
- **Content sanitization (working tree)**: Reads `git ls-files`, skips binary files (`\x00` check), applies compiled regex substitutions to UTF-8 text. Only modifies files that changed.
- **Blob filtering**: Skips files >100MB, binary content (`\x00` in first 8KB), non-UTF-8. Uses pre-computed `blob_map` for O(1) lookup per entry instead of re-filtering.
- **Branch cleanup**: Keeps only `main` or `master`, force-deletes all others (they reference unsanitized commits).
- **Copyright headers**: Uses `COMMENT_STYLES` dict mapping extensions to comment syntax. Detects existing headers by scanning first 5 lines. Handles shebang lines and Python encoding pragmas.
- **License files**: Templates in `LICENSE_HEADERS` dict (full text) and `FILE_HEADER_TEMPLATES` dict (short per-file version).

## Config Flow

`config.yaml` is deep-merged over `DEFAULTS`. Required: `source_repo`. Each module (`sanitize`, `history`, `copyright`, `license`) has an `enabled` toggle.

## Gotchas

- Python 3.13 `Path.resolve()` requires existing path — use `.absolute()` for output dir
- `Repo.iter_commits()` with multiple positional args fails — use keyword args: `rev=..., first_parent=True, reverse=True`
- `tree.traverse()` `item.type` is a **string** (`"blob"`), not a Git object — use `item.type == "blob"`, NOT `getattr(item.type, "sha", None)`
- `_filter_tree` swaps the working `.git/index` — must restore on failure (try/finally)
- `git mktree` requires `mode type sha\tpath` format (includes `blob`/`tree` type word), not `mode sha\tpath`
- Empty temp files can't be used as `GIT_INDEX_FILE` — git requires a valid index v2 header; use index swap instead
- After `filter-branch`, HEAD may be detached — re-checkout the branch name
- The CWD may disappear mid-run (git operations can change it) — always use absolute paths for subprocess calls
