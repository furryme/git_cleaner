"""Simplify and clean git history structure.

Note: message rewriting and author changes are handled by sanitize.py.
This module focuses on structural cleanup: squashing, branch removal, linearizing.
"""

import logging
import re
import subprocess
from pathlib import Path

from git import Repo

logger = logging.getLogger(__name__)


def simplify_history(repo_path, config):
    """Simplify git history structure based on the configured strategy."""
    history_cfg = config["history"]
    if not history_cfg.get("enabled"):
        logger.info("History simplification disabled, skipping")
        return

    strategy = history_cfg.get("strategy", "simplify")
    logger.info(f"History strategy: {strategy}")

    repo = Repo(repo_path)

    # Always clean up unwanted branches
    _clean_branches(repo, repo_path, history_cfg)

    if strategy == "simplify":
        _squash_merge_commits(repo_path, history_cfg)
    elif strategy == "linearize":
        _linearize(repo_path, history_cfg)
    elif strategy == "rewrite-only":
        # Message/author rewriting is handled by sanitize module
        logger.info("Strategy rewrite-only: message/author rewriting done by sanitize step")
    elif strategy == "squash-ranges":
        _squash_merge_commits(repo_path, history_cfg)


def _clean_branches(repo, repo_path, cfg):
    """Remove worktree-agent and other internal branches."""
    # Get current branch list using git command (more reliable)
    result = subprocess.run(
        ["git", "-C", repo_path, "branch", "--list"],
        capture_output=True, text=True,
    )
    all_branches = [b.strip("* ").strip() for b in result.stdout.strip().split("\n") if b.strip()]

    # Keep only main/master — delete all other branches
    keep = None
    for preferred in ("main", "master"):
        if preferred in all_branches:
            keep = preferred
            break

    if not keep and all_branches:
        keep = all_branches[0]

    branches_to_remove = [b for b in all_branches if b != keep]
    logger.info(f"Branch cleanup: keep={keep}, remove={branches_to_remove}")

    for name in branches_to_remove:
        result = subprocess.run(
            ["git", "-C", repo_path, "branch", "-D", name],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            logger.info(f"Removed branch: {name}")
        else:
            logger.debug(f"Could not delete branch {name}: {result.stderr}")

    # Checkout the kept branch
    subprocess.run(
        ["git", "-C", repo_path, "checkout", keep],
        capture_output=True, text=True,
    )
    logger.info(f"Checked out {keep}")


def _squash_merge_commits(repo_path, cfg):
    """Remove merge commits and squash their content into the linear history."""
    repo = Repo(repo_path)
    squash_patterns = cfg.get("squash_patterns", [])

    # Find merge commits and commits matching squash patterns
    compiled = [re.compile(p) for p in squash_patterns]
    all_commits = list(repo.iter_commits("HEAD"))

    merge_commits = [c for c in all_commits if len(c.parents) > 1]
    squash_commits = set()
    for commit in all_commits:
        for pattern in compiled:
            if pattern.search(commit.message):
                squash_commits.add(commit.hexsha[:7])
                break

    total_to_clean = len(merge_commits) + len(squash_commits)
    if total_to_clean == 0:
        logger.info("No commits to squash")
        return

    logger.info(f"Found {len(merge_commits)} merge commits + {len(squash_commits)} squash-pattern commits")

    # If the first-parent chain is already linear (no merges on first-parent path),
    # the sanitize step likely already produced a clean linear history — skip cherry-pick
    first_parent_commits = list(repo.iter_commits("HEAD", first_parent=True))
    fp_merges = [c for c in first_parent_commits if len(c.parents) > 1]
    if not fp_merges:
        logger.info("First-parent history already linear, nothing to relinearize")
        return

    if merge_commits or squash_commits:
        _drop_merges_via_orphan(repo, repo_path, cfg, compiled)
    elif squash_commits:
        logger.info("Squash-pattern commits noted (use interactive rebase for manual cleanup)")


def _rebase_drop_merges(repo, repo_path, cfg, squash_commits):
    """Rebase to drop merge commits."""
    current_branch = repo.active_branch.name

    # Create backup
    import time
    backup = f"_cleaner_backup_{time.time():.0f}"
    repo.git.branch(backup, "HEAD")

    try:
        # Use GIT_SEQUENCE_EDITOR to change merges to "drop"
        editor_script = _build_drop_merges_script(repo, squash_commits)

        env = __import__("os").environ.copy()
        if editor_script:
            env["GIT_SEQUENCE_EDITOR"] = editor_script

        result = subprocess.run(
            ["git", "-C", repo_path, "rebase", "--rebase-merges", "--root"],
            env=env,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if result.returncode != 0:
            # Fallback: simpler approach - just recreate from log
            logger.info("Standard rebase failed, trying commit-by-commit approach...")
            subprocess.run(["git", "-C", repo_path, "rebase", "--abort"], capture_output=True, timeout=60)

            # As a simpler approach, create a new branch from the root
            # with only non-merge commits
            _relinearize_from_commits(repo, repo_path, cfg)
        else:
            logger.info("Successfully rebased to drop merge commits")

    except Exception as e:
        logger.warning(f"Rebase dropped: {e}")
        try:
            subprocess.run(["git", "-C", repo_path, "rebase", "--abort"], capture_output=True, timeout=60)
        except Exception:
            pass
    finally:
        try:
            repo.delete_branch(backup, "-D")
        except Exception:
            pass


def _build_drop_merges_script(repo, squash_commits):
    """Build a GIT_SEQUENCE_EDITOR script to drop merge commits."""
    import tempfile

    # Find merge commit SHAs
    merge_shas = set()
    for c in repo.iter_commits("HEAD"):
        if len(c.parents) > 1:
            merge_shas.add(c.hexsha[:7])

    all_drop = merge_shas | squash_commits
    if not all_drop:
        return None

    script_path = Path(tempfile.mkdtemp()) / "seq_editor.sh"
    lines = ["#!/bin/bash", "# Drop merge and squash commits"]

    for sha in all_drop:
        lines.append(f"sed -i '' '/^pick {sha}$/d' \"$1\"")
        lines.append(f"sed -i '' '/^p {sha}$/d' \"$1\"")

    script_path.write_text("\n".join(lines))
    script_path.chmod(0o755)
    return f"bash {script_path}"


def _relinearize_from_commits(repo, repo_path, cfg):
    """Create a linear history from non-merge commits."""
    current_branch = repo.active_branch.name
    squash_patterns = cfg.get("squash_patterns", [])
    compiled = [re.compile(p) for p in squash_patterns]

    # Get commits in chronological order, skip merges and squash-pattern commits
    all_commits = list(repo.iter_commits("--reverse", "HEAD"))
    keep_commits = []
    for c in all_commits:
        if len(c.parents) > 1:
            continue
        if any(p.search(c.message) for p in compiled):
            continue
        keep_commits.append(c)

    if not keep_commits:
        logger.warning("No commits to keep after filtering!")
        return

    logger.info(f"Rebuilding linear history with {len(keep_commits)} commits (from {len(all_commits)})")

    # Create an orphan branch and cherry-pick
    new_branch = f"_cleaner_linear_{__import__('time').time():.0f}"
    repo.git.checkout("--orphan", new_branch)
    repo.git.rm("-r", "--cached", "-f", ".")

    # We can't easily replay commits without their changes, so just note this
    # The proper approach is using filter-branch --parent-filter
    # For merge commit removal, use a simpler approach

    # Go back to original branch
    repo.git.checkout(current_branch)
    try:
        repo.delete_branch(new_branch, "-D")
    except Exception:
        pass

    # Use git filter-branch to drop merge commits by changing parent refs
    # This is too complex, so let's use a proven approach
    _drop_merges_via_orphan(repo, repo_path, cfg, compiled)


def _drop_merges_via_orphan(repo, repo_path, cfg, squash_compiled=None):
    """Drop merge commits by creating a new linear branch via cherry-pick."""
    current_branch = repo.active_branch.name
    squash_compiled = squash_compiled or []

    # Get all commits in topological order, then drop merges and squash-pattern commits
    seen = set()
    all_commits = []
    for c in repo.iter_commits("--topo-order", "--reverse", "HEAD"):
        if c.hexsha in seen:
            continue
        seen.add(c.hexsha)
        if len(c.parents) > 1:
            continue
        if any(p.search(c.message) for p in squash_compiled):
            continue
        all_commits.append(c)
    if not all_commits:
        return

    logger.info(f"Rebuilding linear history with {len(all_commits)} commits (from {len(seen)} unique)")

    # Create orphan branch
    new_branch = f"_cleaner_linear_{__import__('time').time():.0f}"
    try:
        repo.git.checkout("--orphan", new_branch)
        repo.git.rm("-r", "--cached", "-f", ".")

        success_count = 0
        empty_count = 0
        failed = []
        for commit in all_commits:
            try:
                repo.git.cherry_pick(commit.hexsha, "--no-edit")
                success_count += 1
            except Exception as e:
                err = str(e)
                if "empty fixup" in err or "nothing to commit" in err:
                    repo.git.cherry_pick("--drop")
                    empty_count += 1
                else:
                    failed.append((commit.hexsha[:7], err[:50]))
                    try:
                        repo.git.cherry_pick("--abort")
                    except Exception:
                        pass

        if success_count > 0:
            repo.git.branch("-D", current_branch)
            repo.git.branch("-m", new_branch, current_branch)
            logger.info(f"Linear history: {success_count} cherry-picked, {empty_count} empty, {len(failed)} conflicts")
            if failed:
                logger.debug(f"Failed commits: {failed[:5]}")
        else:
            logger.warning("No commits could be cherry-picked, keeping original history")
            repo.git.checkout(current_branch)
            try:
                repo.delete_branch(new_branch, "-D")
            except Exception:
                pass

    except Exception as e:
        logger.warning(f"Orphan relinearize failed: {e}")
        try:
            repo.git.checkout(current_branch)
        except Exception:
            pass


def clean_reflogs(repo_path):
    """Clean up reflog and orphaned references."""
    repo = Repo(repo_path)
    try:
        repo.git.reflog("expire", "--expire=all", "--all")
        repo.git.gc("--prune=now")
        logger.info("Cleaned reflogs and ran gc")
    except Exception as e:
        logger.debug(f"Reflog cleanup: {e}")
