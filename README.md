# kokoro-say

Read highlighted or clipboard text aloud with a hotkey, using
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), a small, natural-sounding text-to-speech model that runs
entirely on your machine. Nothing is sent anywhere.

It's built for Wayland desktops, and the installer wires up Hyprland bindings on
[Omarchy](https://omarchy.org/).

| Key | Action |
|---|---|
| `SUPER + ALT + R` | Read the highlighted text. Press again to stop. |
| `SUPER + SHIFT + ALT + R` | Read the clipboard. Press again to stop. |

## How it works

A small user service (`kokoro-say serve`) loads the model once and listens on
`$XDG_RUNTIME_DIR/kokoro-say.sock`. The hotkeys run `kokoro-say toggle …`, which
only sends the text over the socket, so speech starts in about 1 s on CPU and
about 0.3 s on GPU. Text is split into sentences: the first one plays while the
rest are still being generated.

## Install

Requirements: `uv`, PipeWire (`pw-play`), `wl-clipboard`, `libnotify`, `curl`.

```bash
sudo pacman -S --needed uv pipewire wl-clipboard libnotify curl   # Arch / Omarchy
git clone <this repo> ~/Work/kokoro-say && cd ~/Work/kokoro-say
./install.sh          # CPU, which is fast enough on any recent machine
./install.sh --gpu    # NVIDIA: about 7× faster, uses ~3 GB of CUDA wheels and ~0.5 GB of VRAM
```

The installer:

- creates a Python 3.12 venv in `~/.local/share/kokoro-say/venv`
- downloads the model (~350 MB) and checks it against the checksums in `models.sha256`
- installs `~/.local/bin/kokoro-say` and the `kokoro-say.service` user unit (enabled, so it starts at login)
- appends the bindings from `hypr/bindings.lua` to `~/.config/hypr/bindings.lua`, after backing that file up.
  `--no-bind` skips this step.

It's safe to re-run; that's also how you update.

## Usage

```bash
kokoro-say toggle primary            # highlighted text (what the hotkey runs)
kokoro-say toggle clipboard
kokoro-say say --text "Hello there"  # speak, replacing anything already playing
some-command | kokoro-say say stdin
kokoro-say say -v bf_emma -s 1.2 clipboard
kokoro-say stop
kokoro-say status                    # idle/speaking, CPU or CUDA, voice
kokoro-say voices
```

## Settings

`~/.config/kokoro-say/config.toml` controls the voice, speed, accent, the pause
between sentences, CPU or GPU, and the maximum length. After editing it, run
`systemctl --user restart kokoro-say`.

Good English voices to try: `af_heart` (default), `af_bella`, `bf_emma`,
`am_fenrir`, `bm_george`. Use `lang = "en-gb"` with the `b*` (British) voices.

## Troubleshooting

- **Nothing happens.** Check `systemctl --user status kokoro-say` and
  `journalctl --user -u kokoro-say`. Errors also appear as desktop notifications.
- **"Nothing to read".** Some apps don't set the primary selection. Copy the text and use the clipboard key instead.

## Uninstall

```bash
./uninstall.sh          # keeps ~/.config/kokoro-say
./uninstall.sh --purge  # removes that too
```

## Licence

MIT for this project. Kokoro-82M is Apache-2.0. The ONNX export and voices come from
[kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) (MIT).
