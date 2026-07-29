# HomeCortex initialization kit

Generate a private kit from the repository defaults:

```bash
./scripts/create-init.sh
```

The generated `init/` directory contains:

```text
init/
├── install.yaml
├── .env
├── config/
├── prompts/
└── data/              # optional existing SQLite databases
```

Edit `init/.env` and the files under `init/config/`, then validate without
installing:

```bash
./install.sh --init-dir ./init --validate-only
```

Ollama must already be installed. The installer does not install it; it only
starts the local server if necessary and downloads the model declared under
`llm.model` when `install.yaml` allows it.

The entire `init/` directory is ignored by Git. The installer copies it to the
runtime installation; HomeCortex never runs directly from this directory.

To preserve existing memory or TTS cache data during a first installation,
place `kira_memory.db` and/or `tts_cache.db` in `init/data/`. Existing runtime
files are backed up before they are replaced.
