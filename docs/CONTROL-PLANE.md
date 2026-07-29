# HomeCortex Control Plane

The v1.2 Control Plane is a local Go service with an embedded React dashboard.
It observes Kira Core, Ollama and Home Assistant, and manages HomeCortex-owned
services through the host service manager.

The dashboard is available in French and English. It initially follows the
browser language and stores the explicit FR/EN choice locally for later visits.
Command-line installation and validation messages remain in English.

## Local endpoints

The Control Plane binds to `127.0.0.1:3210` by default.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/system` | Host and Control Plane information |
| `GET` | `/api/v1/services` | Current service health |
| `POST` | `/api/v1/services/{id}/{action}` | Start, stop or restart a managed service |
| `GET` | `/api/v1/events` | Service-state SSE stream |
| `GET` | `/api/v1/logs/stream?service=...` | Masked file-log SSE stream |
| `GET` | `/api/v1/config` | Read `config/kira.yaml` |
| `PUT` | `/api/v1/config` | Validate and atomically replace `kira.yaml` |
| `GET/PUT` | `/api/v1/files/{id}` | Read or atomically update an allow-listed configuration or prompt file |
| `GET` | `/api/v1/ollama/model` | Configured model, loaded state, context and memory |
| `POST` | `/api/v1/ollama/model/{load,unload,restart}` | Manage only the model configured for HomeCortex |
| `GET` | `/api/v1/diagnostics` | CPU, unified memory, Metal compatibility and recommended profile |
| `GET` | `/api/v1/resources` | Live HomeCortex CPU/memory/storage and host network rates |
| `GET/POST` | `/api/v1/backups` | List or create local recovery points |
| `POST` | `/api/v1/backups/{name}/restore` | Restore an allow-listed archive and restart Kira |
| `POST` | `/api/v1/chat` | Authenticated proxy to Kira Core `/chat` |

The server refuses non-loopback clients. Remote LAN access will require an
explicit authentication and TLS design before it is enabled.

## Build

```bash
./scripts/build-control-plane.sh
```

The script builds the React application first, then embeds it into the Go
binary at `control-plane/homecortex-control`.

## Development

```bash
cd control-plane/web
npm install
npm run dev
```

In a second terminal:

```bash
cd control-plane
go run . --root /path/to/HomeCortex
```

## Configuration and prompt safety

The dashboard provides an allow-listed editor for `kira.yaml`, the French and
English prompts, and their suffix files. Updates are:

1. size-limited;
2. parsed with `yaml.safe_load` for YAML configuration;
3. backed up beside the active file;
4. written to a temporary file and atomically renamed.

Secrets remain in `.env` and are not exposed by the configuration API.

## Ollama model management

The Overview page manages the model declared as `llm.model` in
`config/kira.yaml`. It does not accept an arbitrary model name from the
browser. Load and reload keep the model resident; unload releases its unified
memory. Ollama itself remains an observed external service.

## Resource monitoring

The Resources page samples every two seconds and keeps the last two minutes in
the browser. CPU and memory cover the deployed HomeCortex processes. Storage is
the runtime-root footprint, cached for 30 seconds to avoid excessive disk
scanning. Network receive/transmit rates are host-wide because reliable
per-process counters on macOS require elevated tracing privileges.

## Backup and restore

Maintenance archives are stored under `backups/manual/` with mode `0600`.
They contain `.env`, configuration, prompts and application data. TTS cache is
optional. SQLite databases use the `sqlite3` online backup mechanism. Restore
accepts only allow-listed paths, creates an automatic pre-restore recovery
point, writes files atomically and restarts Kira Core.

The same operations are available locally:

```bash
homecortex-maintenance list
homecortex-maintenance backup
homecortex-maintenance backup --include-tts
homecortex-maintenance restore homecortex-YYYYMMDD-HHMMSS.NNNNNNNNN.zip
```
