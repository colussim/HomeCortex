#!/usr/bin/env python3
"""Validate a HomeCortex initialization kit without exposing secrets."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


REQUIRED_CONFIG = (
    "kira.yaml",
    "satellites.json",
    "personas.yaml",
    "phonetic.yaml",
    "room_groups.yaml",
    "tools_config_fr.json",
    "tools_config_en.json",
    "lang/fr.yaml",
    "lang/en.yaml",
)
REQUIRED_PROMPTS = (
    "prompt_fr.txt",
    "prompt_en.txt",
    "prompt_suffix_fr.txt",
    "prompt_suffix_en.txt",
)
REQUIRED_ENV = ("KIRA_API_TOKEN", "HA_TOKEN", "HA_URL", "HA_URL_C")
PLACEHOLDER_RE = re.compile(r"(change-me|your_token|x\.x\.x\.x)", re.IGNORECASE)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid .env line: {raw_line}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def validate(root: Path, allow_placeholders: bool) -> list[str]:
    errors: list[str] = []
    install_file = root / "install.yaml"
    env_file = root / ".env"

    for path in (install_file, env_file):
        if not path.is_file():
            errors.append(f"missing file: {path.relative_to(root)}")

    for relative in REQUIRED_CONFIG:
        if not (root / "config" / relative).is_file():
            errors.append(f"missing file: config/{relative}")

    for relative in REQUIRED_PROMPTS:
        if not (root / "prompts" / relative).is_file():
            errors.append(f"missing file: prompts/{relative}")

    if errors:
        return errors

    try:
        install_text = install_file.read_text(encoding="utf-8")
        if yaml is not None:
            install = yaml.safe_load(install_text) or {}
            if install.get("version") != 1:
                errors.append("install.yaml: version must be 1")
            if not install.get("instance", {}).get("profile"):
                errors.append("install.yaml: instance.profile is required")
        else:
            if not re.search(r"(?m)^version:\s*1\s*$", install_text):
                errors.append("install.yaml: version must be 1")
            if not re.search(r"(?m)^\s+profile:\s*\S+", install_text):
                errors.append("install.yaml: instance.profile is required")
    except Exception as exc:
        errors.append(f"install.yaml: {exc}")

    for relative in REQUIRED_CONFIG:
        path = root / "config" / relative
        try:
            if path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
            elif yaml is not None:
                yaml.safe_load(path.read_text(encoding="utf-8"))
            elif not path.read_text(encoding="utf-8").strip():
                errors.append(f"config/{relative}: file is empty")
        except Exception as exc:
            errors.append(f"config/{relative}: {exc}")

    try:
        env = parse_env(env_file)
        for key in REQUIRED_ENV:
            value = env.get(key, "")
            if not value:
                errors.append(f".env: {key} is required")
            elif not allow_placeholders and PLACEHOLDER_RE.search(value):
                errors.append(f".env: {key} still contains an example value")
        kira_text = (root / "config/kira.yaml").read_text(encoding="utf-8")
        elevenlabs_enabled = False
        if yaml is not None:
            kira = yaml.safe_load(kira_text) or {}
            elevenlabs_enabled = bool(kira.get("tts", {}).get("elevenlabs_enabled"))
        else:
            elevenlabs_enabled = bool(
                re.search(r"(?ms)^tts:\s*$.*?^\s+elevenlabs_enabled:\s*true\s*$", kira_text)
            )
        if elevenlabs_enabled and not env.get("ELEVENLABS_API_KEY"):
            errors.append(".env: ELEVENLABS_API_KEY is required when ElevenLabs is enabled")
    except Exception as exc:
        errors.append(f".env: {exc}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("init_dir", type=Path)
    parser.add_argument("--allow-placeholders", action="store_true")
    args = parser.parse_args()
    root = args.init_dir.expanduser().resolve()

    if not root.is_dir():
        print(f"Initialization directory not found: {root}", file=sys.stderr)
        return 2

    errors = validate(root, args.allow_placeholders)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"Initialization kit is valid: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
