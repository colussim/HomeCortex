# Platform dependency locks

- `macos-arm64-py314.lock` is generated from the real HomeCortex Python 3.14
  environment supplied for Apple Silicon.
- `ventuno-arm64-py314.lock` will be generated only after the production
  VENTUNO Linux image, accelerator libraries and compatible Python version have
  been validated on hardware.

The lock files are complete installation snapshots. Direct project
dependencies and optional feature groups are declared in the root
`pyproject.toml`.

