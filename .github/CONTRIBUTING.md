## Contributing

### Local setup

- Install hooks:

```bash
python3 -m pip install --upgrade pip
python3 -m pip install pre-commit
pre-commit install
```

- Run checks:

```bash
pre-commit run --all-files
```

### Updating hook versions

```bash
pre-commit autoupdate
pre-commit run --all-files
```

> [!NOTE]
> `ggshield` runs on `pre-push`. To enable it, set `GITGUARDIAN_API_KEY` in your environment.

### Commit messages

This repository follows **Conventional Commits** and enforces rules via `.commitlintrc.yaml`
(validated at commit time by a git hook).

Example:

```text
feat(cloudflare): add api retry helper
```

### Pull requests

- Use the PR template
- Keep changes focused (avoid bundling refactors with feature work)
- Do not commit secrets (keys, tokens, passwords, credentials)
