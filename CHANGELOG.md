# Changelog

All notable changes to HomeCortex are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No unreleased changes.

## [1.2.1] - 2026-07-29

### Added

- Structured known-person responses in `config/personas.yaml`.
- Deterministic local routing for family and household identity questions.
- Automated tests for accented aliases, unknown people, family fallbacks and
  exact prompt rules.

### Changed

- Exact identity responses declared in the active system prompt now take
  priority over persona fallbacks.
- Known household members are answered locally before Ollama or `web_search`
  can be invoked.

### Fixed

- Prevented Ollama from inventing relationships or dates for known people.
- Prevented known family names from incorrectly triggering an Internet search.
- Fixed conflicts between generic family fallbacks and exact responses already
  defined in `prompt_fr.txt`.

## [1.2.0] - 2026-07-29

### Added

- Configuration-first installation using a private, Git-ignored `init/` kit.
- Automated macOS Apple Silicon installation with native `launchd` services.
- Linux ARM64 deployment templates using native `systemd` services.
- Go Control Plane with an embedded bilingual React dashboard.
- Service health monitoring for Kira Core, Ollama and Home Assistant.
- Start, stop and restart controls for HomeCortex-managed services.
- Ollama model load, unload and reload controls.
- Live masked log streaming.
- Direct text chat with Kira from the dashboard.
- Allow-listed configuration and prompt editor with atomic writes and backups.
- CPU, unified-memory, storage and network resource gauges.
- Platform diagnostics for processor, architecture, memory and Metal/MLX
  compatibility.
- Local backup and restoration from the dashboard and
  `homecortex-maintenance` command.
- Safe update workflow with a mandatory pre-update recovery archive.
- Safe uninstaller with keep-data and explicitly confirmed purge modes.
- Isolated lifecycle integration test covering installation, update, backup,
  restore, reinstallation and uninstallation.
- HomeCortex visual identity, dashboard logo, favicon and Kira chat portrait.
- Installation and Control Plane documentation.

### Changed

- Replaced the standalone Go chat interface with the integrated Control Plane
  Chat page.
- Replaced PM2 deployment guidance with native `launchd` and `systemd`
  management.
- Updated the project structure and roadmap to reflect the v1.2 architecture.
- Runtime configuration, secrets, prompts and databases are preserved by
  default during updates.
- Ollama is now a strict external prerequisite; HomeCortex only downloads the
  configured model when it is missing and model pulling is enabled.
- Resource reporting includes HomeCortex processes and the active Ollama model
  allocation.
- ElevenLabs remains available alongside local Piper TTS.

### Fixed

- Fixed Home Assistant chat commands failing during text normalization.
- Fixed Home Assistant alias-map initialization and optional proactive-service
  loading.
- Fixed incorrect memory reporting on Apple Silicon.
- Fixed transient macOS `launchctl bootstrap` failures during updates.
- Fixed backup filename collisions when multiple recovery points were created
  within the same second.
- Fixed backup inclusion of temporary editor backups and `.DS_Store` files.

### Security

- Secrets remain in the private `.env` file and are never exposed by the
  configuration API.
- Backup archives containing secrets are stored locally with mode `0600`.
- Restore operations accept only allow-listed paths and reject traversal.
- SQLite databases use online snapshots before archival.
- The Control Plane binds to loopback and rejects non-local clients.
- Logs are masked before streaming through the dashboard.

### Removed

- Removed the obsolete standalone `chatbox/` application.
- Removed PM2 as a production runtime requirement.
- Removed tracked runtime database files from the source repository.

## [1.1.0] - 2026-06-18

### Added

- Initial HomeCortex/Kira self-hosted voice-assistant backend.
- Local speech-to-text with Whisper on Apple Silicon.
- Local LLM inference through Ollama.
- Piper, ElevenLabs and optional XTTS speech synthesis support.
- Home Assistant device control and state retrieval.
- ESP32-S3 satellite authentication and room mapping.
- Conversational memory and optional speaker identification.
- Multilingual French and English configuration.
- Proactive announcements, weather lookup and optional Web search tools.
- Initial standalone Go Web Chat interface.

### Changed

- Expanded project documentation, architecture diagrams and configuration
  examples during the v1.1 development cycle.
- Refined prompts, phonetic corrections and Home Assistant configuration.

[Unreleased]: https://github.com/colussim/HomeCortex/compare/v1.2.1...HEAD
[1.2.1]: https://github.com/colussim/HomeCortex/compare/v1.2...v1.2.1
[1.2.0]: https://github.com/colussim/HomeCortex/releases/tag/v1.2
[1.1.0]: https://github.com/colussim/HomeCortex/releases/tag/v1.1
