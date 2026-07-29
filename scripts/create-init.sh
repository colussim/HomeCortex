#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
INIT_DIR=${1:-"$REPO_DIR/init"}

if [ -e "$INIT_DIR" ]; then
  echo "Refusing to overwrite existing initialization kit: $INIT_DIR" >&2
  exit 1
fi

mkdir -p "$INIT_DIR/config/lang" "$INIT_DIR/prompts" "$INIT_DIR/data"
cp "$REPO_DIR/init.example/install.yaml" "$INIT_DIR/install.yaml"
cp "$REPO_DIR/init.example/.env.example" "$INIT_DIR/.env"

for file in kira.yaml personas.yaml phonetic.yaml room_groups.yaml tools_config_fr.json tools_config_en.json satellites.json; do
  cp "$REPO_DIR/config/$file" "$INIT_DIR/config/$file"
done

for file in fr.yaml en.yaml; do
  cp "$REPO_DIR/config/lang/$file" "$INIT_DIR/config/lang/$file"
done

for file in prompt_fr.txt prompt_en.txt prompt_suffix_fr.txt prompt_suffix_en.txt; do
  cp "$REPO_DIR/$file" "$INIT_DIR/prompts/$file"
done

chmod 600 "$INIT_DIR/.env"
echo "Initialization kit created: $INIT_DIR"
echo "Edit $INIT_DIR/.env and configuration files before installing."
echo "Optional existing databases can be placed in $INIT_DIR/data/."
