#!/usr/bin/env bash
# Remove kokoro-say. Leaves your config.toml; pass --purge to delete it too.
set -euo pipefail

data=${XDG_DATA_HOME:-$HOME/.local/share}/kokoro-say
config=${XDG_CONFIG_HOME:-$HOME/.config}/kokoro-say
units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
bindings=${XDG_CONFIG_HOME:-$HOME/.config}/hypr/bindings.lua

systemctl --user disable --now kokoro-say.service 2>/dev/null || true
rm -f "$units/kokoro-say.service" "$HOME/.local/bin/kokoro-say"
systemctl --user daemon-reload
rm -rf "$data"
[[ ${1:-} == --purge ]] && rm -rf "$config"

if [[ -f $bindings ]] && grep -q 'kokoro-say' "$bindings"; then
  cp "$bindings" "$bindings.bak.$(date +%s)"
  # Remove the block install.sh appended (between its marker comments).
  sed -i '/^-- >>> kokoro-say >>>$/,/^-- <<< kokoro-say <<<$/d' "$bindings"
  command -v hyprctl >/dev/null && hyprctl reload >/dev/null || true
fi
echo "kokoro-say removed."
