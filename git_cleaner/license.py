"""Generate LICENSE and other project-level files."""

import logging
from datetime import datetime
from pathlib import Path

from .copyright import LICENSE_HEADERS

logger = logging.getLogger(__name__)


def generate_license_files(repo_path, config):
    """Generate LICENSE, README, and other project files."""
    license_cfg = config["license"]
    if not license_cfg.get("enabled"):
        logger.info("License file generation disabled, skipping")
        return

    base = Path(repo_path)
    holder = license_cfg.get("holder", "The Project Authors")
    year = license_cfg.get("year", str(datetime.now().year))
    license_type = license_cfg.get("license_type", "mit").lower()

    generated = []

    # LICENSE file
    license_text = LICENSE_HEADERS.get(license_type, LICENSE_HEADERS["mit"])
    license_content = license_text.format(year=year, holder=holder)
    license_path = base / "LICENSE"
    license_path.write_text(license_content, encoding="utf-8")
    generated.append("LICENSE")
    logger.info(f"Generated LICENSE ({license_type})")

    # README if configured
    if license_cfg.get("generate_readme"):
        readme_content = _generate_readme(license_cfg, license_type, year, holder)
        readme_path = base / "README.md"
        if not readme_path.exists():
            readme_path.write_text(readme_content, encoding="utf-8")
            generated.append("README.md")
            logger.info("Generated README.md")
        else:
            logger.info("README.md already exists, skipping")

    # .gitignore if not present
    gitignore_path = base / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(_default_gitignore(), encoding="utf-8")
        generated.append(".gitignore")
        logger.info("Generated .gitignore")

    # PR template
    if license_cfg.get("generate_prtemplate"):
        pr_path = base / ".github" / "PULL_REQUEST_TEMPLATE.md"
        pr_path.parent.mkdir(parents=True, exist_ok=True)
        if not pr_path.exists():
            pr_path.write_text(_pr_template(), encoding="utf-8")
            generated.append(".github/PULL_REQUEST_TEMPLATE.md")
            logger.info("Generated PR template")

    # CODEOWNERS if configured
    if license_cfg.get("generate_codeowners"):
        co_path = base / ".github" / "CODEOWNERS"
        co_path.parent.mkdir(parents=True, exist_ok=True)
        co_path.write_text("# See https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners\n", encoding="utf-8")
        generated.append(".github/CODEOWNERS")
        logger.info("Generated CODEOWNERS")

    # Commit all generated files
    if generated:
        repo = __import__("git").Repo(repo_path)
        repo.index.add(generated)
        from .sanitize import commit_with_author
        commit_with_author(repo_path, "Add project license and boilerplate files", config)
        logger.info(f"Committed {len(generated)} generated files")

    return generated


def _generate_readme(config, license_type, year, holder):
    """Generate a README.md file."""
    readme_cfg = config.get("readme", {})
    name = readme_cfg.get("project_name", "Project Name")
    description = readme_cfg.get("description", "A brief description of the project")

    lines = [
        f"# {name}",
        "",
        f">{description}",
        "",
    ]

    if readme_cfg.get("project_badge"):
        lines.extend([
            "[![License: {license}]](LICENSE)".format(license=license_type.upper()),
            "",
        ])

    lines.extend([
        "## Installation",
        "",
        "```bash",
        "git clone https://github.com/<user>/<repo>.git",
        "cd <repo>",
        "```",
        "",
    ])

    if readme_cfg.get("usage_section"):
        lines.extend([
            "## Usage",
            "",
            "```bash",
            "# TODO: Add usage instructions",
            "```",
            "",
        ])

    if readme_cfg.get("contributing_section"):
        lines.extend([
            "## Contributing",
            "",
            "Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on our code of conduct",
            "and the process for submitting pull requests.",
            "",
        ])

    lines.extend([
        "## License",
        "",
        f"This project is licensed under the {license_type.upper()} License - see the [LICENSE](LICENSE) file for details.",
        "",
        f"Copyright (c) {year} {holder}",
    ])

    return "\n".join(lines) + "\n"


def _default_gitignore():
    """Return a default .gitignore template."""
    return """# Byte-compiled / optimized
__pycache__
*.py[cod]
*$py.class
*.so

# Node
node_modules/
npm-debug.log
yarn-error.log

# IDE
.idea/
.vscode/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Build
dist/
build/
*.o

# Environment
.env
.env.local
.env.*.local

# Logs
*.log

# Dependencies
vendor/
"""


def _pr_template():
    """Return a pull request template."""
    return """## Description

Describe your changes in detail.

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## How Has This Been Tested?

Describe the tests you ran.

## Checklist

- [ ] My code follows the style guidelines of this project
- [ ] I have performed a self-review
- [ ] I have commented my code
- [ ] I have made corresponding changes to documentation
- [ ] My changes generate no new warnings
- [ ] I have added tests that prove my fix/feature works
"""
