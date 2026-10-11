"""Small helper to update KEY=VALUE pairs in .env without losing other settings."""

from __future__ import annotations

from pathlib import Path


def set_env_values(updates: dict[str, str], path: str = ".env", template: str = ".env.example") -> Path:
    env = Path(path)
    source = env if env.exists() else Path(template)
    lines = source.read_text().splitlines() if source.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining and "=" in line and not line.lstrip().startswith("#"):
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in remaining.items())
    env.write_text("\n".join(out) + "\n")
    return env
