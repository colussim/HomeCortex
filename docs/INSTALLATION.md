# HomeCortex v1.2 installation

## Initialization kit

Create a private kit from the current repository defaults:

```bash
./scripts/create-init.sh
```

Edit `init/.env`, `init/config/` and `init/prompts/`, then validate. Existing
`kira_memory.db` and `tts_cache.db` files may optionally be placed in
`init/data/`; they are installed with mode `0600` and an existing runtime copy
is backed up before replacement.

```bash
./install.sh --init-dir ./init --validate-only
```

The installer never runs HomeCortex directly from `init/`. It validates and
copies this input into the platform runtime layout.

Before displaying the installation plan, it runs `scripts/platform-doctor.sh`.
On Apple Silicon this reports the processor, CPU cores, unified memory,
Metal/MLX compatibility, Ollama version and a conservative model-size
recommendation. The diagnostic is informational and does not expose `.env`.

## Ollama prerequisite

Ollama is a strict external prerequisite. HomeCortex never installs it
automatically. Before writing to the runtime directory, the installer:

1. checks that the `ollama` command exists;
2. reads the single required model from `init/config/kira.yaml` at `llm.model`;
3. starts the already-installed local Ollama server when necessary;
4. checks whether that exact model is available;
5. runs `ollama pull <model>` only when it is missing and
   `ollama.pull_model` is enabled in `init/install.yaml`.

If Ollama is absent or cannot start, installation stops with instructions and
does not continue with HomeCortex runtime changes.

## macOS Apple Silicon

Default runtime root:

```text
~/Library/Application Support/HomeCortex
```

HomeCortex uses per-user LaunchAgents:

```text
~/Library/LaunchAgents/io.homecortex.core.plist
~/Library/LaunchAgents/io.homecortex.control.plist
```

The installer also places `homecortex-maintenance` in the runtime `bin/`
directory for local backup and restore operations.

Run a safe plan first:

```bash
./install.sh --init-dir ./init --dry-run
```

For an isolated installer test:

```bash
./install.sh \
  --init-dir ./init \
  --install-dir /tmp/homecortex-test \
  --skip-dependencies \
  --skip-services
```

## Updating an existing installation

Run updates from the checked-out HomeCortex repository:

```bash
git pull
./update.sh --init-dir ./init
```

The updater refuses to operate when no existing installation is found. Before
changing installed files, it creates a mandatory recovery archive through the
local Control Plane. It then validates Ollama and the configured model, updates
the application and Control Plane, installs dependencies, refreshes the service
definitions, and restarts the services.

By default, the installed `.env`, `config/`, `prompts/` and `data/` directories
are preserved. To intentionally replace them with the current private
initialization kit, use:

```bash
./update.sh --init-dir ./init --apply-init
```

For a source-only update when Python dependencies and the Control Plane build
have not changed:

```bash
./update.sh --init-dir ./init --skip-dependencies --skip-build
```

If backup creation fails, the update stops before modifying the runtime.

## VENTUNO/Linux ARM64

The planned layout is:

```text
/opt/homecortex
/etc/homecortex
/var/lib/homecortex
/var/log/homecortex
```

The same initialization kit is used with the `ventuno-arm64` profile. The
platform dependency lock and hardware acceleration choices will only be frozen
after validation on the production VENTUNO Linux image.
