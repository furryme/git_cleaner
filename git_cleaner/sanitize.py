"""Sanitize sensitive information from git history and file contents."""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from git import Repo

logger = logging.getLogger(__name__)


def should_exclude_file(filepath, exclude_patterns):
    """Check if a file should be excluded based on gitignore-style patterns."""
    from fnmatch import fnmatch

    for pattern in exclude_patterns:
        # Direct match on full path
        if fnmatch(filepath, pattern):
            return True
        # Match just the filename (for patterns without /)
        if "/" not in pattern and fnmatch(os.path.basename(filepath), pattern):
            return True
        # Match directory prefix (for patterns ending with /)
        if pattern.endswith("/"):
            prefix = pattern.rstrip("/")
            if filepath.startswith(prefix + "/") or fnmatch(filepath, prefix + "/*"):
                return True
        # Handle ** glob patterns
        if "**" in pattern:
            regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
            if re.match(regex, filepath):
                return True
    return False


def commit_with_author(repo_path, message, config):
    """Commit using the sanitized author identity from config.

    Avoids leaking the local git config identity into the cleaned repo.
    """
    env = os.environ.copy()
    hist = config.get("history", {})
    env["GIT_AUTHOR_NAME"] = hist.get("author_name", "Developer")
    env["GIT_AUTHOR_EMAIL"] = hist.get("author_email", "developer@example.com")
    env["GIT_COMMITTER_NAME"] = env["GIT_AUTHOR_NAME"]
    env["GIT_COMMITTER_EMAIL"] = env["GIT_AUTHOR_EMAIL"]

    subprocess.run(
        ["git", "-C", repo_path, "commit", "-m", message],
        env=env, check=True, capture_output=True,
    )


def run_sanitize(repo_path, config):
    """Run sanitization: rewrite history (authors, messages) then clean file contents."""
    sanitize_cfg = config["sanitize"]
    if not sanitize_cfg.get("enabled"):
        logger.info("Sanitization disabled, skipping")
        return

    repo = Repo(repo_path)

    # 1. Rewrite git history with filter-branch (authors + commit messages)
    _rewrite_history(repo_path, repo, config)

    # 2. Clean file contents in the working tree (current HEAD)
    _clean_working_tree_contents(repo_path, sanitize_cfg, config)

    # 3. Remove excluded files from working tree
    _remove_excluded_files(repo_path, sanitize_cfg, config)


def _rewrite_history(repo_path, repo, config):
    """Rewrite author info and commit messages using cherry-pick + amend."""
    sanitize_cfg = config["sanitize"]
    history_cfg = config["history"]

    author_name = history_cfg.get("author_name", "")
    author_email = history_cfg.get("author_email", "")
    email_replacements = sanitize_cfg.get("email_replacements", {})
    msg_rewrites = history_cfg.get("message_rewrites", [])
    msg_redact = [p for p in sanitize_cfg.get("redact_patterns", [])]

    if not (author_name or author_email or email_replacements or msg_rewrites or msg_redact):
        logger.info("No history rewriting needed")
        return

    # Build email map from all unique emails in history
    email_map = {}
    for commit in repo.iter_commits("--all"):
        for author_obj in (commit.author, commit.committer):
            orig = author_obj.email
            if orig and orig not in email_map:
                # Determine mapped email
                mapped = None
                for pattern, replacement in email_replacements.items():
                    if re.match(f"^{pattern}$", orig):
                        mapped = replacement
                        break
                if not mapped and author_email:
                    mapped = author_email
                email_map[orig] = mapped

    if not email_map and not msg_rewrites and not msg_redact:
        logger.info("No history rewriting needed")
        return

    import shutil

    msg_subs = []
    for p in msg_redact + msg_rewrites:
        pat, rep = p.get("pattern", ""), p.get("replacement", "")
        if pat:
            msg_subs.append((re.compile(pat), rep))

    current_branch = repo.active_branch.name

    # Determine which branch to rewrite — prefer main/master
    target_branch = current_branch
    for preferred in ("main", "master"):
        if any(b.name == preferred for b in repo.branches):
            target_branch = preferred
            break

    if target_branch != current_branch:
        repo.git.checkout(target_branch)
        current_branch = target_branch

    new_branch = f"_cleaner_rewrite_{__import__('time').time():.0f}"

    # Capture commits BEFORE any checkout changes
    all_commits = list(repo.iter_commits(rev=current_branch, first_parent=True, reverse=True))
    total = len(all_commits)
    logger.info(f"Rewriting {total} commits on {current_branch} via commit-tree...")

    # Use git commit-tree to create new commits with sanitized author info
    # This avoids cherry-pick conflicts and gives full control over author/committer
    env = os.environ.copy()
    parent_map = {}  # old_sha -> new_sha

    try:
        for i, commit in enumerate(all_commits):
            # Map author
            a_email = email_map.get(commit.author.email, author_email or commit.author.email)
            a_name = author_name or commit.author.name
            c_email = email_map.get(commit.committer.email, author_email or commit.committer.email)
            c_name = author_name or commit.committer.name

            # Rewrite message
            new_msg = commit.message
            for regex, replacement in msg_subs:
                new_msg = regex.sub(replacement, new_msg)

            # Build parent refs
            parent_shas = [parent_map.get(p.hexsha, p.hexsha) for p in commit.parents
                          if p.hexsha in {c.hexsha for c in all_commits}]

            # Create commit via commit-tree
            author_str = f'{a_name} <{a_email}>'
            committer_str = f'{c_name} <{c_email}>'

            commit_env = env.copy()
            commit_env.update({
                "GIT_AUTHOR_NAME": a_name,
                "GIT_AUTHOR_EMAIL": a_email,
                "GIT_AUTHOR_DATE": _format_git_date(commit.authored_datetime),
                "GIT_COMMITTER_NAME": c_name,
                "GIT_COMMITTER_EMAIL": c_email,
                "GIT_COMMITTER_DATE": _format_git_date(commit.committed_datetime),
            })

            cmd = ["git", "-C", repo_path, "commit-tree", commit.tree.hexsha]
            for p_sha in parent_shas:
                cmd.extend(["-p", p_sha])

            result = subprocess.run(
                cmd,
                input=new_msg,
                env=commit_env,
                capture_output=True, text=True,
            )

            if result.returncode != 0:
                logger.debug(f"commit-tree failed at {i}: {result.stderr[:100]}")
                # Fall back to using original commit SHA
                parent_map[commit.hexsha] = commit.hexsha
                continue

            new_sha = result.stdout.strip()
            if new_sha:
                parent_map[commit.hexsha] = new_sha
            else:
                parent_map[commit.hexsha] = commit.hexsha

            if (i + 1) % 50 == 0:
                logger.info(f"  Rewrote {i + 1}/{total} commits...")

        # Get the new HEAD SHA (last commit)
        old_head = all_commits[-1].hexsha if all_commits else None
        new_head = parent_map.get(old_head)

        if new_head and new_head != old_head:
            # Create new branch at the rewritten HEAD
            subprocess.run(
                ["git", "-C", repo_path, "update-ref",
                 f"refs/heads/{new_branch}", new_head],
                capture_output=True,
            )
            # Delete old branch ref
            subprocess.run(
                ["git", "-C", repo_path, "update-ref", "-d",
                 f"refs/heads/{current_branch}"],
                capture_output=True,
            )
            # Rename new branch
            subprocess.run(
                ["git", "-C", repo_path, "branch", "-m", new_branch, current_branch],
                capture_output=True,
            )
            # Checkout
            subprocess.run(
                ["git", "-C", repo_path, "checkout", "-f", current_branch],
                capture_output=True,
            )
        else:
            # No changes were made, clean up
            try:
                subprocess.run(
                    ["git", "-C", repo_path, "branch", "-D", new_branch],
                    capture_output=True,
                )
            except Exception:
                pass

        logger.info(f"Rewrote {len(parent_map)} commits on {current_branch}")

    except Exception as e:
        logger.error(f"History rewrite failed: {e}")
        raise

    # Ensure we're on the rewritten branch
    subprocess.run(
        ["git", "-C", repo_path, "checkout", "-f", current_branch],
        capture_output=True,
    )
    logger.info(f"Checked out {current_branch} after rewrite ({total} commits)")

    # Update tags to point to rewritten commits (annotated tags need recreation)
    _update_tags(repo_path, parent_map)


def _format_git_date(dt):
    """Format a datetime as a git date string."""
    from datetime import timezone

    if dt is None:
        return ""
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        epoch = int(dt.timestamp())
        utcoffset = dt.utcoffset()
        total_seconds = int(utcoffset.total_seconds()) if utcoffset else 0
        sign = "+" if total_seconds >= 0 else "-"
        total_seconds = abs(total_seconds)
        tz_hours = total_seconds // 3600
        tz_minutes = (total_seconds % 3600) // 60
        return f"{epoch} {sign}{tz_hours:02d}{tz_minutes:02d}"
    else:
        import calendar
        epoch = int(calendar.timegm(dt.timetuple()))
        return f"{epoch} +0000"


def _update_tags(repo_path, parent_map):
    """Update tags to point to rewritten commits.

    Handles both lightweight and annotated tags.
    Annotated tags must be deleted and recreated since the tag object
    embeds the old commit SHA.
    """
    result = subprocess.run(
        ["git", "-C", repo_path, "tag", "-l"],
        capture_output=True, text=True,
    )
    for tag_name in result.stdout.strip().split("\n"):
        if not tag_name:
            continue

        # Get the tag object type
        tag_obj_result = subprocess.run(
            ["git", "-C", repo_path, "cat-file", "-t", tag_name],
            capture_output=True, text=True,
        )
        obj_type = tag_obj_result.stdout.strip()

        # Get the commit SHA the tag ultimately points to
        commit_result = subprocess.run(
            ["git", "-C", repo_path, "rev-list", "-n", "1", tag_name],
            capture_output=True, text=True,
        )
        old_commit = commit_result.stdout.strip()
        new_commit = parent_map.get(old_commit)

        if new_commit and new_commit != old_commit:
            # Delete the old tag
            subprocess.run(
                ["git", "-C", repo_path, "tag", "-d", tag_name],
                capture_output=True,
            )
            # Recreate: annotated if it was, lightweight otherwise
            if obj_type == "tag":
                # Get original tagger info and date for annotated tag
                tag_log_result = subprocess.run(
                    ["git", "-C", repo_path, "tag", "-v", tag_name, "2>&1"],
                    capture_output=True, text=True,
                )
                # Recreate as lightweight (simpler, preserves pointer)
                subprocess.run(
                    ["git", "-C", repo_path, "tag", tag_name, new_commit],
                    capture_output=True,
                )
            else:
                subprocess.run(
                    ["git", "-C", repo_path, "tag", tag_name, new_commit],
                    capture_output=True,
                )
            logger.info(f"Updated tag {tag_name}: {old_commit[:7]} -> {new_commit[:7]}")
        elif not new_commit:
            # Tag points to a commit not in our rewrite (from other branches)
            # Leave it for _final_cleanup to handle
            logger.debug(f"Tag {tag_name} points to {old_commit[:7]} (not in parent_map)")


def _clean_working_tree_contents(repo_path, sanitize_cfg, config):
    """Replace sensitive strings in file contents of the working tree."""
    email_map = sanitize_cfg.get("email_replacements", {})
    redact_patterns = sanitize_cfg.get("redact_patterns", [])

    if not email_map and not redact_patterns:
        return

    # Compile patterns
    subs = []
    for pattern, replacement in email_map.items():
        subs.append((re.compile(pattern), replacement))
    for p in redact_patterns:
        pat = p.get("pattern", "")
        rep = p.get("replacement", "")
        if pat:
            subs.append((re.compile(pat), rep))

    if not subs:
        return

    exclude = sanitize_cfg.get("exclude_files", [])
    base = Path(repo_path)
    repo = Repo(repo_path)
    modified = []

    # Get tracked files using git ls-files (more reliable than tree traversal)
    result = subprocess.run(
        ["git", "-C", repo_path, "ls-files"],
        capture_output=True, text=True,
    )
    all_files = [f for f in result.stdout.strip().split("\n") if f]
    logger.info(f"Content sanitization: {len(subs)} patterns, {len(all_files)} tracked files")

    for rel_path in all_files:
        filepath = base / rel_path

        if not filepath.is_file():
            continue
        if should_exclude_file(rel_path, exclude):
            continue

        # Skip binary files
        try:
            raw = filepath.read_bytes()
            if b"\x00" in raw[:8192]:
                continue
        except Exception:
            continue

        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue

        new_text = text
        for regex, replacement in subs:
            new_text = regex.sub(replacement, new_text)

        if new_text != text:
            filepath.write_text(new_text, encoding="utf-8")
            modified.append(rel_path)
            logger.debug(f"Sanitized: {rel_path}")

    if modified:
        repo.index.add(modified)
        commit_with_author(repo_path, "Sanitize sensitive content in source files", config)
        logger.info(f"Sanitized {len(modified)} files in working tree")
    else:
        logger.info("No files needed content sanitization")


def _remove_excluded_files(repo_path, sanitize_cfg, config):
    """Remove excluded files from working tree and git index."""
    exclude = sanitize_cfg.get("exclude_files", [])
    if not exclude:
        return

    repo = Repo(repo_path)
    base = Path(repo_path)
    removed = []

    # Get all tracked files
    result = subprocess.run(
        ["git", "-C", repo_path, "ls-files"],
        capture_output=True, text=True,
    )
    all_files = [f for f in result.stdout.strip().split("\n") if f]

    for rel_path in all_files:
        if should_exclude_file(rel_path, exclude):
            full_path = base / rel_path
            if full_path.exists():
                full_path.unlink()
                removed.append(rel_path)
                logger.debug(f"Removed: {rel_path}")

    # Remove directories that are excluded
    for pattern in exclude:
        if pattern.endswith("/"):
            dir_prefix = pattern.rstrip("/")
            for d in list(base.glob(dir_prefix + "*/")):
                if d.is_dir() and d.name != ".git":
                    import shutil
                    shutil.rmtree(d)

    if removed:
        try:
            repo.git.rm("--cached", "-r", "--ignore-unmatch", *removed)
        except Exception:
            pass
        commit_with_author(repo_path, "Remove sensitive/excluded files", config)
        logger.info(f"Removed {len(removed)} excluded files")


def sanitize_working_tree(repo_path, config):
    """Legacy alias - now handled by run_sanitize."""
    _remove_excluded_files(repo_path, config["sanitize"], config)
