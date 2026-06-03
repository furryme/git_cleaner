"""Add copyright headers to source files."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Comment style mapping by file extension
COMMENT_STYLES = {
    ".go": {"prefix": "// ", "block_start": "", "block_end": "", "multi": False},
    ".py": {"prefix": "# ", "block_start": "", "block_end": "", "multi": False},
    ".js": {"prefix": "// ", "block_start": "/* ", "block_end": " */", "multi": True},
    ".ts": {"prefix": "// ", "block_start": "/* ", "block_end": " */", "multi": True},
    ".tsx": {"prefix": "// ", "block_start": "/* ", "block_end": " */", "multi": True},
    ".jsx": {"prefix": "// ", "block_start": "/* ", "block_end": " */", "multi": True},
    ".java": {"prefix": "// ", "block_start": "/* ", "block_end": " */", "multi": True},
    ".cpp": {"prefix": "// ", "block_start": "/* ", "block_end": " */", "multi": True},
    ".c": {"prefix": "// ", "block_start": "/* ", "block_end": " */", "multi": True},
    ".h": {"prefix": "// ", "block_start": "/* ", "block_end": " */", "multi": True},
    ".rs": {"prefix": "// ", "block_start": "/* ", "block_end": " */", "multi": True},
    ".rb": {"prefix": "# ", "block_start": "", "block_end": "", "multi": False},
    ".sh": {"prefix": "# ", "block_start": "", "block_end": "", "multi": False},
    ".yaml": {"prefix": "# ", "block_start": "", "block_end": "", "multi": False},
    ".yml": {"prefix": "# ", "block_start": "", "block_end": "", "multi": False},
    ".xml": {"prefix": "  ", "block_start": "<!--", "block_end": "-->", "multi": True},
    ".html": {"prefix": "  ", "block_start": "<!--", "block_end": "-->", "multi": True},
    ".css": {"prefix": "  ", "block_start": "/*", "block_end": "*/", "multi": True},
    ".sql": {"prefix": "-- ", "block_start": "", "block_end": "", "multi": False},
    ".tf": {"prefix": "# ", "block_start": "", "block_end": "", "multi": False},
    ".hcl": {"prefix": "// ", "block_start": "", "block_end": "", "multi": False},
}

# Standard license headers
LICENSE_HEADERS = {
    "mit": """MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.""",
    "apache2": """Apache License
Version 2.0, January 2004
http://www.apache.org/licenses/

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.""",
    "bsd3": """BSD 3-Clause License

Copyright (c) {year} {holder}

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.""",
    "bsd2": """BSD 2-Clause License

Copyright (c) {year} {holder}

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.""",
    "gpl3": """GNU GENERAL PUBLIC LICENSE
Version 3, 29 June 2007

Copyright (C) {year} {holder}

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.""",
    "mpl2": """Mozilla Public License Version 2.0
Copyright (c) {year} {holder}

This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
If a copy of the MPL was not distributed with this file,
You can obtain one at https://mozilla.org/MPL/2.0/.""",
}

# File-level header templates (shorter, for individual source files)
FILE_HEADER_TEMPLATES = {
    "mit": "Copyright (c) {year} {holder}\n\nPermission is hereby granted, free of charge, to use this software.",
    "apache2": "Copyright (c) {year} {holder}\n\nLicensed under the Apache License, Version 2.0.",
    "bsd3": "Copyright (c) {year} {holder}\n\nAll rights reserved.\n\nRedistribution permitted under BSD 3-Clause License.",
    "bsd2": "Copyright (c) {year} {holder}\n\nAll rights reserved.\n\nRedistribution permitted under BSD 2-Clause License.",
    "gpl3": "Copyright (c) {year} {holder}\n\nThis program is free software under GPL v3.",
    "mpl2": "Copyright (c) {year} {holder}\n\nThis Source Code Form is subject to MPL-2.0.",
}


def _get_comment_style(ext):
    """Get the comment style for a file extension."""
    return COMMENT_STYLES.get(ext, {"prefix": "// ", "block_start": "/* ", "block_end": " */", "multi": True})


def _format_header_lines(lines, comment_style):
    """Format header lines with the appropriate comment syntax."""
    prefix = comment_style["prefix"]
    block_start = comment_style.get("block_start", "")
    block_end = comment_style.get("block_end", "")

    if comment_style.get("multi") and block_start and block_end:
        formatted = [block_start]
        for line in lines:
            formatted.append(f"  {prefix}{line}" if line else "  ")
        formatted.append(block_end)
    else:
        formatted = [f"{prefix}{line}" if line else prefix.rstrip() for line in lines]

    return formatted


def _get_file_header(config):
    """Get the file header text based on config."""
    copyright_cfg = config["copyright"]
    holder = copyright_cfg.get("author", "The Project Authors")
    year = copyright_cfg.get("year", "2024")
    license_type = copyright_cfg.get("license_header", "mit").lower()

    template = FILE_HEADER_TEMPLATES.get(license_type, FILE_HEADER_TEMPLATES["mit"])
    header = template.format(year=year, holder=holder)

    # Add custom copyright text if provided
    custom_text = copyright_cfg.get("text", "")
    if custom_text and custom_text != template:
        current_year = str(__import__("datetime").datetime.now().year)
        custom = custom_text.format(
            year=year,
            current_year=current_year,
            author=holder,
            organization=copyright_cfg.get("organization", ""),
        )
        header = custom + "\n\n" + header

    return header


def add_copyright_headers(repo_path, config):
    """Add copyright headers to all matching source files."""
    copyright_cfg = config["copyright"]
    if not copyright_cfg.get("enabled"):
        logger.info("Copyright headers disabled, skipping")
        return

    extensions = set(copyright_cfg.get("file_extensions", []))
    header_text = _get_file_header(config)
    header_lines = header_text.split("\n")

    repo = __import__("git").Repo(repo_path)
    modified = []

    for commit in repo.iter_commits("HEAD"):
        pass  # We work on the working tree

    base = Path(repo_path)
    for ext in extensions:
        for filepath in base.rglob(f"*{ext}"):
            if _is_excluded(filepath, base, config):
                continue

            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug(f"Cannot read {filepath}: {e}")
                continue

            if _already_has_header(content, ext):
                logger.debug(f"Already has header: {filepath}")
                continue

            comment_style = _get_comment_style(ext)
            formatted_header = _format_header_lines(header_lines, comment_style)
            header_block = "\n".join(formatted_header) + "\n\n"

            # Handle shebang
            if content.startswith("#!"):
                first_nl = content.index("\n") if "\n" in content else len(content)
                shebang = content[:first_nl] + "\n"
                new_content = shebang + header_block + content[first_nl + 1:]
            else:
                # Handle existing file headers (UTF-8 BOM, encoding pragmas)
                stripped = content
                preamble = ""
                if content.startswith("﻿"):
                    preamble = "﻿"
                    stripped = content[1:]

                # Python encoding pragma
                import re as re_mod
                if ext == ".py":
                    match = re_mod.match(r"(.*#.*coding[:=]\s*\w+\s*\n)", stripped)
                    if match:
                        preamble += match.group(1)
                        stripped = stripped[match.end():]

                new_content = preamble + header_block + stripped

            filepath.write_text(new_content, encoding="utf-8")
            modified.append(str(filepath.relative_to(base)))
            logger.debug(f"Added header to: {filepath.relative_to(base)}")

    if modified:
        logger.info(f"Added copyright headers to {len(modified)} files")
        # Stage and commit
        repo.index.add(modified)
        repo.index.commit("Add copyright headers to source files")
    else:
        logger.info("No files needed copyright headers")

    return modified


def _is_excluded(filepath, base, config):
    """Check if a file should be excluded from header injection."""
    from .sanitize import should_exclude_file

    rel = str(filepath.relative_to(base))
    exclude = config.get("sanitize", {}).get("exclude_files", [])
    if should_exclude_file(rel, exclude):
        return True

    # Skip vendor/node_modules/etc
    skip_dirs = {"node_modules", "vendor", ".git", "__pycache__", "venv", ".venv"}
    for part in filepath.parts:
        if part in skip_dirs:
            return True

    return False


def _already_has_header(content, ext):
    """Check if a file already has a copyright header."""
    first_lines = content.split("\n")[:5]
    first_text = "\n".join(first_lines).lower()

    # Strip comment characters
    import re
    cleaned = re.sub(r"^(//|#|/\*|\*|\s|--|<\!--)\s*", "", first_text, flags=re.MULTILINE)

    indicators = ["copyright", "licensed", "license", "all rights reserved", "permission is hereby"]
    return any(ind in cleaned for ind in indicators)
