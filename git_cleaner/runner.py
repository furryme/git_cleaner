"""Orchestrator - runs the full cleaning pipeline."""

import logging
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def run_cleaning(config):
    """Run the full cleaning pipeline on the source repository."""
    source = Path(config["source_repo"]).resolve()
    output = Path(config["output_dir"]).absolute()

    logger.info(f"Source: {source}")
    logger.info(f"Output: {output}")

    # Step 0: Clone the source repo to output dir
    step("Clone repository", lambda: _clone_repo(source, output))

    # The rest of the steps operate on output_dir
    repo_path = str(output)

    # Step 1: Sanitize sensitive info from history
    if config["sanitize"].get("enabled"):
        step("Sanitize sensitive data", lambda: _run_sanitize(repo_path, config))

    # Step 2: Simplify git history
    if config["history"].get("enabled"):
        step("Simplify git history", lambda: _run_history(repo_path, config))

    # Step 3: Add copyright headers to source files
    if config["copyright"].get("enabled"):
        step("Add copyright headers", lambda: _run_copyright(repo_path, config))

    # Step 4: Generate LICENSE and project files
    if config["license"].get("enabled"):
        step("Generate project files", lambda: _run_license(repo_path, config))

    # Step 5: Final cleanup
    step("Final cleanup", lambda: _final_cleanup(repo_path, config))

    # Summary
    _print_summary(output, config)

    logger.info(f"\nDone! Clean repository at: {output}")
    return output


def step(name, fn):
    """Run a named step with timing and error handling."""
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    logger.info(f"Starting: {name}")

    start = time.time()
    try:
        fn()
        elapsed = time.time() - start
        logger.info(f"Completed: {name} ({elapsed:.1f}s)")
        print(f"  [OK] {name} ({elapsed:.1f}s)")
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"Failed: {name} after {elapsed:.1f}s - {e}")
        print(f"  [FAIL] {name} - {e}")
        raise


def _clone_repo(source, output):
    """Clone the source repository to the output directory."""
    if output.exists():
        shutil.rmtree(output)
        logger.info(f"Removed existing output dir: {output}")

    # Clone all branches with a single commit per branch for speed
    cmd = [
        "git", "clone",
        "--no-hardlinks",  # don't share object store with source
        str(source),
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to clone repo: {result.stderr}")

    logger.info(f"Cloned repository to {output}")

    # Fetch all branches
    fetch_result = subprocess.run(
        ["git", "-C", str(output), "fetch", "--all"],
        capture_output=True, text=True, timeout=120,
    )
    if fetch_result.returncode == 0:
        # Check out all local branches from remote
        for ref in subprocess.run(
            ["git", "-C", str(output), "branch", "-r"],
            capture_output=True, text=True,
        ).stdout.strip().split("\n"):
            ref = ref.strip()
            if ref and "HEAD" not in ref:
                remote, branch = ref.split("/", 1)
                try:
                    subprocess.run(
                        ["git", "-C", str(output), "checkout", "-b", branch, f"{remote}/{branch}"],
                        capture_output=True, text=True, timeout=30,
                    )
                except Exception:
                    pass

    # Return to the default branch
    default = _get_default_branch(str(output))
    if default:
        subprocess.run(
            ["git", "-C", str(output), "checkout", default],
            capture_output=True, text=True,
        )

    # Set a generic origin for the clean repo
    subprocess.run(
        ["git", "-C", str(output), "remote", "set-url", "origin",
         "https://github.com/example/cleaned-repo.git"],
        capture_output=True,
    )

    return output


def _get_default_branch(repo_path):
    """Get the default branch name (main or master)."""
    result = subprocess.run(
        ["git", "-C", repo_path, "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        ref = result.stdout.strip()
        return ref.split("/")[-1]
    # Fallback
    for name in ("main", "master"):
        result = subprocess.run(
            ["git", "-C", repo_path, "show-ref", "--verify", f"refs/heads/{name}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return name
    return None


def _run_sanitize(repo_path, config):
    """Run the sanitize step - rewrites history and cleans file contents."""
    from .sanitize import run_sanitize

    run_sanitize(repo_path, config)


def _run_history(repo_path, config):
    """Run the history simplification step."""
    from .history import clean_reflogs, simplify_history

    simplify_history(repo_path, config)
    clean_reflogs(repo_path)


def _run_copyright(repo_path, config):
    """Run the copyright header step."""
    from .copyright import add_copyright_headers

    add_copyright_headers(repo_path, config)


def _run_license(repo_path, config):
    """Run the license file generation step."""
    from .license import generate_license_files

    generate_license_files(repo_path, config)


def _final_cleanup(repo_path, config):
    """Run final cleanup operations."""
    import git

    repo = git.Repo(repo_path)

    # Remove backup branches created during processing
    backup_branches = [b for b in repo.branches if "_cleaner_backup_" in b.name]
    for branch in backup_branches:
        repo.delete_branch(branch, "-D")
        logger.info(f"Removed backup branch: {branch.name}")

    # Clean reflogs
    try:
        repo.git.reflog("--expire-all", "--all")
        repo.git.gc("--prune=now")
    except Exception as e:
        logger.debug(f"Final gc warning: {e}")

    # Remove .git/filter_branch (filter-branch backup)
    fb_dir = Path(repo_path) / ".git" / "refs" / "stash"
    orig_refs = Path(repo_path) / ".git" / "refs" / "original"
    if orig_refs.exists():
        shutil.rmtree(orig_refs)
        logger.info("Removed filter-branch original refs")

    # Print final stats
    total_commits = len(list(repo.iter_commits("HEAD")))
    logger.info(f"Final commit count: {total_commits}")


def _print_summary(output, config):
    """Print a summary of what was done."""
    import git

    repo = git.Repo(str(output))
    total_commits = len(list(repo.iter_commits("HEAD")))
    branches = [str(b.name) for b in repo.branches]

    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  Output:      {output}")
    print(f"  Commits:     {total_commits}")
    print(f"  Branches:    {', '.join(branches)}")

    files = list(output.glob("**/*"))
    files = [f for f in files if f.is_file() and "/.git/" not in str(f)]
    print(f"  Files:       {len(files)}")

    if (output / "LICENSE").exists():
        print(f"  License:     {(output / 'LICENSE').read_text()[:50].strip()}...")

    enabled = []
    if config["sanitize"].get("enabled"):
        enabled.append("sanitize")
    if config["history"].get("enabled"):
        enabled.append("history")
    if config["copyright"].get("enabled"):
        enabled.append("copyright")
    if config["license"].get("enabled"):
        enabled.append("license")

    print(f"  Steps:       {', '.join(enabled)}")
    print()
