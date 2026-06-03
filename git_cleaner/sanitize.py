"""Sanitize sensitive information from git history and file contents."""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from git import Repo

logger = logging.getLogger(__name__)


def _write_blob(repo_path, content_bytes):
    """Write content as a blob and return its SHA."""
    result = subprocess.run(
        ["git", "-C", repo_path, "hash-object", "-w", "--stdin"],
        input=content_bytes, capture_output=True,
    )
    return result.stdout.decode().strip()


def _filter_blob(repo_path, sha, subs):
    """Apply substitutions to a blob. Return new SHA, or None if unchanged."""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "cat-file", "-s", sha],
            capture_output=True, text=True,
        )
        size = int(result.stdout.strip())
        if size > 100 * 1024 * 1024:
            return None
    except Exception:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "cat-file", "-p", sha],
            capture_output=True,
        )
        raw = result.stdout
    except Exception:
        return None
    if b"\x00" in memoryview(raw)[:8192]:
        return None
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    original = text
    for regex, replacement in subs:
        text = regex.sub(replacement, text)
    if text == original:
        return None
    return _write_blob(repo_path, text.encode("utf-8"))


def _filter_tree(repo_path, tree_sha, subs, exclude_patterns, blob_map=None):
    """Filter a tree, replacing blobs and removing excluded entries.
    Uses git update-index --cacheinfo + write-tree for reliable tree creation.
    When blob_map is provided, uses pre-computed mappings for speed.
    Return new tree SHA, or None if tree is unchanged.
    """
    result = subprocess.run(
        ["git", "-C", repo_path, "ls-tree", "-r", tree_sha],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None

    lines = result.stdout.strip().split("\n")
    if not lines or lines == [""]:
        return None

    # Build cacheinfo entries and track changes
    adds = []    # (mode, sha, path) for entries to add
    changed = False

    for line in lines:
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        meta, pathname = parts
        mode, _, _sha = meta.split(" ", 2)

        # Skip excluded entries
        if should_exclude_file(pathname, exclude_patterns):
            changed = True
            continue

        # Only filter blobs
        if not mode.startswith("100"):
            adds.append((mode, _sha, pathname))
            continue

        # Use pre-computed blob_map if available, otherwise filter on-the-fly
        if blob_map:
            new_sha = blob_map.get(_sha)
        else:
            new_sha = _filter_blob(repo_path, _sha, subs)
        if new_sha and new_sha != _sha:
            changed = True
        adds.append((mode, new_sha or _sha, pathname))

    if not changed:
        return None

    # Use read-tree + update-index + write-tree with index swap
    idx_path = Path(repo_path) / ".git" / "index"
    backup_path = Path(repo_path) / ".git" / "index.cleaner_bak"

    try:
        # Save current index
        if idx_path.exists():
            shutil.copy2(str(idx_path), str(backup_path))
        else:
            backup_path = None

        # Load the tree into the index
        subprocess.run(
            ["git", "-C", repo_path, "read-tree", tree_sha],
            capture_output=True,
        )

        # Update entries with filtered blob SHAs
        for mode, sha, path in adds:
            subprocess.run(
                ["git", "-C", repo_path, "update-index", "--cacheinfo",
                 f"{mode},{sha},{path}"],
                capture_output=True,
            )

        # Write the filtered tree
        result = subprocess.run(
            ["git", "-C", repo_path, "write-tree"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.debug(f"write-tree failed: {result.stderr[:200]}")
            return None
        return result.stdout.strip()

    finally:
        # Restore original index
        if backup_path:
            shutil.copy2(str(backup_path), str(idx_path))
            backup_path.unlink(missing_ok=True)
        elif idx_path.exists():
            idx_path.unlink()


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


def _build_blob_map(repo_path, blob_shas, subs, exclude_patterns):
    """Scan blobs and return {old_sha: new_sha} for those needing cleaning."""
    if not blob_shas or not subs:
        return None

    # Collect unique (sha, path) pairs for dedup
    seen = set()
    to_scan = []
    for sha in blob_shas:
        if sha not in seen:
            seen.add(sha)
            to_scan.append(sha)

    blob_map = {}
    scanned = 0
    for sha in to_scan:
        new_sha = _filter_blob(repo_path, sha, subs)
        if new_sha:
            blob_map[sha] = new_sha
        scanned += 1

    if blob_map:
        logger.info(f"Blob content filter: {len(blob_map)}/{scanned} unique blobs need cleaning (of {len(blob_shas)} total)")
    else:
        logger.info(f"Blob content filter: no blobs need cleaning (scanned {scanned})")
    return blob_map or None


def _finalize_object_store(repo_path):
    """Expire reflogs and garbage-collect unreachable objects (old sensitive blobs)."""
    try:
        subprocess.run(
            ["git", "-C", repo_path, "reflog", "expire", "--expire=all", "--all"],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "-C", repo_path, "gc", "--prune=now", "--aggressive"],
            capture_output=True, check=True, timeout=300,
        )
        logger.info("Pruned unreachable objects from object store")
    except Exception as e:
        logger.warning(f"Object store cleanup warning: {e}")


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

    # Build content substitution patterns for tree filtering
    email_map = sanitize_cfg.get("email_replacements", {})
    redact_patterns = sanitize_cfg.get("redact_patterns", [])
    exclude_patterns = sanitize_cfg.get("exclude_files", [])

    subs = []
    for pattern, replacement in email_map.items():
        subs.append((re.compile(pattern), replacement))
    for p in redact_patterns:
        pat = p.get("pattern", "")
        rep = p.get("replacement", "")
        if pat:
            subs.append((re.compile(pat), rep))

    # 1. Rewrite git history — filters blob content through commit-tree
    _rewrite_history(repo_path, repo, config, subs=subs or None, exclude_patterns=exclude_patterns or None)

    # 2. Clean any remaining dirty content in the working tree
    # (safety net for edge cases and when history rewrite skips content filtering)
    _clean_working_tree_contents(repo_path, sanitize_cfg, config)

    # 3. Remove excluded files from working tree
    _remove_excluded_files(repo_path, sanitize_cfg, config)


def _rewrite_history(repo_path, repo, config, subs=None, exclude_patterns=None):
    """Rewrite author info, commit messages, and optionally filter blob content.

    When subs/exclude_patterns are provided, each commit's tree is rebuilt
    with sensitive blobs replaced — the source is never modified.
    """
    sanitize_cfg = config["sanitize"]
    history_cfg = config["history"]

    author_name = history_cfg.get("author_name", "")
    author_email = history_cfg.get("author_email", "")
    email_replacements = sanitize_cfg.get("email_replacements", {})
    msg_rewrites = history_cfg.get("message_rewrites", [])
    msg_redact = [p for p in sanitize_cfg.get("redact_patterns", [])]

    if not (author_name or author_email or email_replacements or msg_rewrites or msg_redact):
        if not subs:
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
        if not subs:
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

    # Pre-collect all unique blobs across the history for content filtering
    blob_map = None
    if subs:
        all_blob_shas = set()
        for commit in all_commits:
            tree = commit.tree
            for item in tree.traverse():
                t = item.type
                if t == "blob" or (not isinstance(t, str) and getattr(t, "name", None) == "blob"):
                    all_blob_shas.add(item.hexsha)
        blob_map = _build_blob_map(repo_path, all_blob_shas, subs, exclude_patterns or [])

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

            # Filter tree content: rebuild with cleaned blobs
            tree_sha = commit.tree.hexsha
            if blob_map:
                try:
                    filtered = _filter_tree(repo_path, tree_sha, subs, exclude_patterns or [],
                                           blob_map=blob_map)
                    if filtered:
                        tree_sha = filtered
                except Exception as e:
                    logger.debug(f"Tree filter warning at commit {i}: {e}")

            commit_env = env.copy()
            commit_env.update({
                "GIT_AUTHOR_NAME": a_name,
                "GIT_AUTHOR_EMAIL": a_email,
                "GIT_AUTHOR_DATE": _format_git_date(commit.authored_datetime),
                "GIT_COMMITTER_NAME": c_name,
                "GIT_COMMITTER_EMAIL": c_email,
                "GIT_COMMITTER_DATE": _format_git_date(commit.committed_datetime),
            })

            cmd = ["git", "-C", repo_path, "commit-tree", tree_sha]
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

    # Prune old sensitive blobs that are no longer referenced by the rewritten history
    if blob_map:
        _finalize_object_store(repo_path)


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
