# git-cleaner

将内部 Git 仓库清理为可公开发布的状态——脱敏、重写历史、添加 License 和 Copyright。

```
git-cleaner -c config.yaml --dry-run   # 预览
git-cleaner -c config.yaml --yes       # 执行
```

## 安装

```bash
pip install -e .
```

依赖：Python 3.10+，GitPython，PyYAML。

## 快速开始

```bash
# 1. 从模板创建配置
cp config.example.yaml config.yaml

# 2. 编辑 config.yaml（至少填 source_repo）
# 3. 预览
git-cleaner -c config.yaml --dry-run

# 4. 执行
git-cleaner -c config.yaml --yes
```

清理后的仓库输出到 `output/` 目录，**原始仓库不会被修改**。

## 配置说明

`config.yaml` 包含四个模块，每个模块可独立开关：

### sanitize — 脱敏

从 git 历史和文件内容中移除敏感信息：

```yaml
sanitize:
  enabled: true
  # 邮箱替换（支持正则）
  email_replacements:
    ".*@internal\\.company\\.com": "contributor@opensource.example.com"
  # 内容脱敏（正则替换）
  redact_patterns:
    - pattern: "prod-[a-z0-9]+\\.internal\\.company\\.com"
      replacement: "[internal-server]"
    - pattern: "10\\.[0-9]+\\.[0-9]+\\.[0-9]+"
      replacement: "[internal-ip]"
  # 排除文件（类似 .gitignore 语法）
  exclude_files:
    - ".env*"
    - "*.db"
    - "thumbs/"
    - "*.tar.gz"
```

**历史重写**遍历每个 commit 的 tree，对其中所有 blob 应用脱敏规则后再通过 `commit-tree` 重建 commit。脱敏后的旧对象会被 `gc --aggressive` 彻底清除，`git log -p` 中不会残留敏感信息。同时修改作者信息（`history.author_name` / `author_email`）和提交信息中的敏感内容。

### history — 历史简化

```yaml
history:
  enabled: true
  # 策略: simplify | linearize | squash-ranges | rewrite-only
  strategy: "rewrite-only"
  # 自动合并匹配的提交（如 WIP、fixup、merge commit）
  squash_patterns:
    - "^Merge branch"
    - "^fixup!"
    - "^wip"
  # 提交信息中的引用替换
  message_rewrites:
    - pattern: "JIRA-[0-9]+"
      replacement: "[issue]"
  # 统一作者信息
  author_name: "Developer"
  author_email: "dev@example.com"
```

工具会自动清理 `worktree-agent-*` 等内部分支，只保留 `main/master`。

### copyright — 添加源码头

```yaml
copyright:
  enabled: true
  author: "Project Contributors"
  year: "2024"
  license_header: "mit"   # mit | apache2 | bsd3 | bsd2 | gpl3 | mpl2
  file_extensions:
    - ".py"
    - ".go"
    - ".ts"
```

自动识别文件类型并插入对应注释风格的版权头。已包含版权头的文件会被跳过。

### license — 生成项目文件

```yaml
license:
  enabled: true
  license_type: "mit"    # mit | apache2 | bsd3 | bsd2 | gpl3 | mpl2
  holder: "Project Authors"
  year: "2024"
  generate_readme: true       # 生成 README.md（可自定义）
  generate_prtemplate: true   # 生成 PR 模板
  generate_codeowners: false
```

生成 `LICENSE`、`.gitignore`、`.github/PULL_REQUEST_TEMPLATE.md` 等标准项目文件。

## CLI 选项

```
git-cleaner [OPTIONS]

  -c, --config PATH    配置文件路径 (默认: config.yaml)
  -v, --verbose        详细日志 (-vv 为 DEBUG)
  -y, --yes            跳过确认提示
  --dry-run            预览，不修改
  --step SANITIZE|HISTORY|COPYRIGHT|LICENSE|ALL  仅执行指定步骤
```

## 执行流程

```
克隆仓库 → 脱敏 (历史 + 文件内容 + 排除文件) → 简化历史 → 添加版权头 → 生成项目文件 → 清理
```

每个步骤有独立的计时和错误报告，某一步失败不会回滚之前的步骤。

## 注意事项

- **原始仓库安全**：工具先克隆再处理，源仓库不会被修改
- **大仓库**：历史重写（commit-tree + blob 过滤）对大仓库可能需要较长时间。工具会预扫描所有唯一 blob 并缓存结果，避免重复处理
- **旧对象清理**：脱敏后通过 `reflog expire` + `gc --aggressive` 彻底清除不可达的旧对象，`git log -p` 中不会残留敏感信息
- **分支处理**：默认只重写 `main/master`，其余内部分支会被删除。如需保留特定分支，请在配置中调整 `history` 策略

## 示例

完整的配置示例见 [`config.example.yaml`](config.example.yaml)。

针对 photo_tagger 项目的实际配置见 [`config.photo_tagger.yaml`](config.photo_tagger.yaml)，演示了：

- 脱敏内部主机名 (`code-server-gpu-*`, `trainference-workload`)
- 替换内部 pip mirror 为 `pypi.org`
- 排除 `.env`、`.db`、`thumbs/`、`.tar.gz` 等文件
- 统一作者为 `Photo Tagger Dev <dev@phototagger.io>`
- 添加 MIT copyright header

## License

MIT
