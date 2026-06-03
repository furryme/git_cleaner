"""CLI entry point for git-cleaner."""

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .runner import run_cleaning


def main():
    parser = argparse.ArgumentParser(
        prog="git-cleaner",
        description="Sanitize internal git repositories for public release",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Verbosity level (-v INFO, -vv DEBUG)",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--step",
        choices=["sanitize", "history", "copyright", "license", "all"],
        default="all",
        help="Run only a specific step (default: all)",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.WARNING
    if args.verbose == 1:
        log_level = logging.INFO
    elif args.verbose >= 2:
        log_level = logging.DEBUG

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load config
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        _dry_run(config)
        return

    # Confirm
    if not args.yes:
        src = config["source_repo"]
        out = config["output_dir"]
        print(f"\nSource repo: {src}")
        print(f"Output dir:  {out}")
        print(f"\nThis will create a cleaned copy of the repository.")
        print(f"Note: git filter-repo modifies the repository in place.")
        response = input("Continue? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    # Run
    try:
        output = run_cleaning(config)
        print(f"\nClean repository ready at: {output}")
        sys.exit(0)
    except Exception as e:
        logging.getLogger(__name__).error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


def _dry_run(config):
    """Show what would be done without making changes."""
    import git

    print(f"\n{'='*60}")
    print(f"  Dry Run - No changes will be made")
    print(f"{'='*60}")

    src = Path(config["source_repo"])
    repo = git.Repo(str(src))
    total_commits = len(list(repo.iter_commits("HEAD")))
    branches = [str(b.name) for b in repo.branches]

    print(f"\nSource: {src}")
    print(f"Output: {config['output_dir']}")
    print(f"Commits: {total_commits}")
    print(f"Branches: {', '.join(branches)}")

    # Count files
    tree = repo.tree("HEAD")
    file_count = sum(1 for _ in tree.traverse() if getattr(_.type, "sha", None) == "blob")
    print(f"Files: {file_count}")

    # What would be sanitized
    sanitize = config["sanitize"]
    if sanitize.get("enabled"):
        emails = len(sanitize.get("email_replacements", {}))
        patterns = len(sanitize.get("redact_patterns", []))
        excluded = len(sanitize.get("exclude_files", []))
        print(f"\nSanitize:")
        print(f"  Email replacements: {emails}")
        print(f"  Redact patterns: {patterns}")
        print(f"  Exclude patterns: {excluded}")

    # History
    history = config["history"]
    if history.get("enabled"):
        squash = len(history.get("squash_patterns", []))
        rewrites = len(history.get("message_rewrites", []))
        print(f"\nHistory ({history.get('strategy', 'simplify')}):")
        print(f"  Squash patterns: {squash}")
        print(f"  Message rewrites: {rewrites}")

    # Copyright
    copyright_cfg = config["copyright"]
    if copyright_cfg.get("enabled"):
        exts = len(copyright_cfg.get("file_extensions", []))
        print(f"\nCopyright:")
        print(f"  File extensions: {exts}")
        print(f"  License header: {copyright_cfg.get('license_header', 'mit')}")

    # License
    license_cfg = config["license"]
    if license_cfg.get("enabled"):
        print(f"\nLicense:")
        print(f"  Type: {license_cfg.get('license_type', 'mit')}")
        print(f"  Generate README: {license_cfg.get('generate_readme', True)}")
        print(f"  Generate PR template: {license_cfg.get('generate_prtemplate', True)}")

    print(f"\n{'='*60}")
    print("  (Use --yes to run without confirmation)")
    print(f"{'='*60}\n")


def init_config():
    """Generate a template config file."""
    import shutil

    example = Path(__file__).parent.parent / "config.example.yaml"
    target = Path("config.yaml")

    if target.exists():
        print(f"config.yaml already exists. Remove it first or use a different path.")
        sys.exit(1)

    shutil.copy(example, target)
    print(f"Created {target} - edit it to customize, then run: git-cleaner")


if __name__ == "__main__":
    main()
