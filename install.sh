#!/usr/bin/env bash
# Install kokoro-say for the current user. Safe to re-run (it updates in place).
#
#   ./install.sh            CPU inference (works everywhere)
#   ./install.sh --gpu      NVIDIA CUDA inference (pulls ~3 GB of CUDA wheels)
#   ./install.sh --no-bind  skip adding Hyprland keybindings
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
data=${XDG_DATA_HOME:-$HOME/.local/share}/kokoro-say
config=${XDG_CONFIG_HOME:-$HOME/.config}/kokoro-say
units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
bindings=${XDG_CONFIG_HOME:-$HOME/.config}/hypr/bindings.lua
bin=$HOME/.local/bin

gpu=0 bind=1
for arg in "$@"; do
  case $arg in
    --gpu) gpu=1 ;;
    --no-bind) bind=0 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

step "Checking dependencies"
missing=()
for cmd in uv pw-play wl-paste notify-send curl sha256sum; do
  command -v "$cmd" >/dev/null || missing+=("$cmd")
done
if ((${#missing[@]})); then
  echo "missing: ${missing[*]}"
  echo "on Arch/Omarchy: sudo pacman -S --needed uv pipewire wl-clipboard libnotify curl coreutils"
  exit 1
fi

step "Creating Python environment in $data/venv"
mkdir -p "$data/models" "$config" "$units" "$bin"
uv venv --quiet --allow-existing --python 3.12 "$data/venv"
uv pip install --quiet --python "$data/venv/bin/python" -r "$repo/requirements.txt"
if ((gpu)); then
  # kokoro-onnx depends on the CPU onnxruntime; both wheels own the same
  # module, so the CPU one has to go before the GPU one goes in.
  uv pip uninstall --quiet --python "$data/venv/bin/python" onnxruntime
  uv pip install --quiet --python "$data/venv/bin/python" -r "$repo/requirements-gpu.txt"
fi

step "Downloading Kokoro-82M model files"
base=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
while read -r sum name; do
  file=$data/models/$name
  if [[ -f $file ]] && echo "$sum  $file" | sha256sum --check --status; then
    echo "$name: already present"
    continue
  fi
  curl -fL --retry 3 --progress-bar -o "$file.part" "$base/$name"
  echo "$sum  $file.part" | sha256sum --check --quiet || { rm -f "$file.part"; echo "checksum mismatch: $name" >&2; exit 1; }
  mv "$file.part" "$file"
done < "$repo/models.sha256"

step "Installing command and service"
install -Dm755 "$repo/kokoro_say.py" "$data/kokoro_say.py"
cat > "$bin/kokoro-say" <<EOF
#!/bin/sh
exec "$data/venv/bin/python" -I "$data/kokoro_say.py" "\$@"
EOF
chmod 755 "$bin/kokoro-say"
[[ -f $config/config.toml ]] || install -m644 "$repo/config.example.toml" "$config/config.toml"
install -m644 "$repo/systemd/kokoro-say.service" "$units/kokoro-say.service"
systemctl --user daemon-reload
systemctl --user enable kokoro-say.service >/dev/null
systemctl --user restart kokoro-say.service

if ((bind)) && [[ -f $bindings ]]; then
  if grep -q 'kokoro-say' "$bindings"; then
    step "Hyprland bindings already present in $bindings"
  else
    step "Adding Hyprland bindings to $bindings"
    cp "$bindings" "$bindings.bak.$(date +%s)"
    printf '\n' >> "$bindings"
    cat "$repo/hypr/bindings.lua" >> "$bindings"
    command -v hyprctl >/dev/null && hyprctl reload >/dev/null && hyprctl configerrors
  fi
elif ((bind)); then
  step "No $bindings found; bind these yourself:"
  echo "  kokoro-say toggle primary     # speak highlighted text / stop"
  echo "  kokoro-say toggle clipboard   # speak clipboard / stop"
fi

step "Waiting for the model to load"
for _ in $(seq 60); do
  "$bin/kokoro-say" status >/dev/null 2>&1 && break
  sleep 0.5
done
"$bin/kokoro-say" status || { journalctl --user -u kokoro-say -n 20 --no-pager; exit 1; }
"$bin/kokoro-say" say --text "Kokoro is ready."
echo
echo "Done. Highlight text and press SUPER+ALT+R. Settings: $config/config.toml"
