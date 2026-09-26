#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/opt/astra-kpf-light"
RELEASES_DIR="$APP_ROOT/releases"
CURRENT_LINK="$APP_ROOT/current"
PREVIOUS_LINK="$APP_ROOT/previous"

release_target_or_empty() {
  local link="$1"
  local target=""
  if [ -L "$link" ]; then
    target="$(readlink -f "$link" || true)"
  fi
  if [ -n "$target" ]; then
    case "$target" in
      "$RELEASES_DIR"/pkg-*) ;;
      *) echo "Refusing release link outside $RELEASES_DIR: $link -> $target" >&2; exit 1 ;;
    esac
    if [ ! -d "$target" ] || [ -L "$target" ]; then
      echo "Release target is not a real directory: $target" >&2
      exit 1
    fi
  fi
  printf '%s' "$target"
}

previous_target="$(release_target_or_empty "$PREVIOUS_LINK")"
if [ -z "$previous_target" ]; then
  echo "No previous Astra KPF light release symlink exists." >&2
  exit 1
fi

old_target="$(release_target_or_empty "$CURRENT_LINK")"

next_link="$APP_ROOT/.current-next"
ln -sfn "$previous_target" "$next_link"
mv -Tf "$next_link" "$CURRENT_LINK"
if [ -n "$old_target" ] && [ "$old_target" != "$previous_target" ]; then
  ln -sfn "$old_target" "$PREVIOUS_LINK"
fi

systemctl daemon-reload
systemctl restart astra-kpf-light.timer 2>/dev/null || true

echo "Rolled back Astra KPF light current release to: $previous_target"
