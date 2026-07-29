#!/bin/sh
set -eu

os_name=$(uname -s)
arch_name=$(uname -m)

echo "Platform diagnostics"
echo "  Operating system: $os_name"
echo "  Architecture:     $arch_name"

if [ "$os_name" = Darwin ]; then
  cpu_name=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo unknown)
  cpu_cores=$(sysctl -n hw.ncpu 2>/dev/null || echo unknown)
  memory_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
  memory_gb=$((memory_bytes / 1024 / 1024 / 1024))
  echo "  Processor:        $cpu_name"
  echo "  CPU cores:        $cpu_cores"
  echo "  Unified memory:   ${memory_gb} GB"
  if [ "$arch_name" = arm64 ]; then
    echo "  Metal/MLX:        compatible"
  else
    echo "  Metal/MLX:        unsupported architecture"
  fi
  if [ "$memory_gb" -ge 32 ]; then
    echo "  Recommended:      3B to 7B models"
  elif [ "$memory_gb" -ge 16 ]; then
    echo "  Recommended:      3B model, moderate context"
  else
    echo "  Recommended:      compact model, reduced context"
  fi
elif [ "$os_name" = Linux ]; then
  memory_kb=$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo)
  memory_gb=$((memory_kb / 1024 / 1024))
  echo "  Memory:           ${memory_gb} GB"
  if [ -e /dev/dri/renderD128 ]; then
    echo "  GPU accelerator:  detected"
  else
    echo "  GPU accelerator:  not detected"
  fi
fi

if command -v ollama >/dev/null 2>&1; then
  echo "  Ollama:           $(ollama --version 2>/dev/null || echo detected)"
else
  echo "  Ollama:           not found in PATH"
fi
