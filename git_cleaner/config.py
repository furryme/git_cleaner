"""Configuration loader and validation."""

import copy
import os
from pathlib import Path

import yaml

DEFAULTS = {
    "source_repo": None,
    "output_dir": "./cleaned_repo",
    "sanitize": {
        "enabled": True,
        "email_replacements": {},
        "redact_patterns": [],
        "exclude_files": [],
    },
    "copyright": {
        "enabled": True,
        "text": "Copyright {year}-{current_year} {author}. All rights reserved.",
        "author": "The Project Authors",
        "organization": "Project Organization",
        "year": str(__import__("datetime").datetime.now().year),
        "license_header": "mit",
        "custom_header_file": None,
        "file_extensions": [],
    },
    "license": {
        "enabled": True,
        "license_type": "mit",
        "holder": "The Project Authors",
        "year": str(__import__("datetime").datetime.now().year),
        "generate_readme": True,
        "generate_codeowners": False,
        "generate_prtemplate": True,
        "readme": {
            "project_name": "Project Name",
            "project_badge": True,
            "description": "A brief description of the project",
            "installation_section": True,
            "usage_section": True,
            "contributing_section": True,
        },
    },
    "history": {
        "enabled": True,
        "strategy": "simplify",
        "squash_patterns": [],
        "message_rewrites": [],
        "message_language": None,
        "author_name": "Developer",
        "author_email": "developer@opensource.example.com",
        "tag_prefix": "v",
    },
}


def _deep_merge(base, override):
    """Merge override into base, returning a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(config_path):
    """Load and validate configuration from a YAML file."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path) as f:
        user_config = yaml.safe_load(f) or {}

    config = _deep_merge(DEFAULTS, user_config)
    config["_path"] = str(path.resolve())
    config["_dir"] = str(path.parent)

    # Validate required fields
    if not config.get("source_repo"):
        raise ValueError("source_repo is required in config")

    src = Path(config["source_repo"])
    if not src.exists():
        raise ValueError(f"Source repository not found: {src}")
    if not (src / ".git").exists() and not (src / ".git").is_file():
        raise ValueError(f"Source is not a git repository: {src}")

    return config
