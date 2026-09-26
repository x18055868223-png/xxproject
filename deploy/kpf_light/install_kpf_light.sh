#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_ROOT="/opt/astra-kpf-light"
RELEASES_DIR="$APP_ROOT/releases"
CURRENT_LINK="$APP_ROOT/current"
PREVIOUS_LINK="$APP_ROOT/previous"
DATA_DIR="/var/lib/astra-kpf-light"
ENV_FILE="/etc/astra-kpf-light.env"
RUN_USER="${ASTRA_KPF_LIGHT_RUN_USER:-bitnami}"
RUN_GROUP="${ASTRA_KPF_LIGHT_RUN_GROUP:-bitnami}"

safe_rm_staging() {
  local target="$1"
  case "$target" in
    "$RELEASES_DIR"/.staging-*) rm -rf -- "$target" ;;
    *) echo "Refusing to remove unexpected path: $target" >&2; return 1 ;;
  esac
}

safe_rm_release_dir() {
  local target="$1"
  case "$target" in
    "$RELEASES_DIR"/pkg-*) ;;
    *) echo "Refusing to remove non-release path: $target" >&2; return 1 ;;
  esac
  if [ -L "$target" ]; then
    echo "Refusing to remove release symlink: $target" >&2
    return 1
  fi
  rm -rf -- "$target"
}

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

package_hash="$(python3 - "$SRC_DIR" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
files = [
    pathlib.Path("tools/astra_kpf_light_v1.py"),
    pathlib.Path("deploy/kpf_light/README.md"),
    pathlib.Path("deploy/kpf_light/astra-kpf-light.env.example"),
    pathlib.Path("deploy/kpf_light/source_manifest.json"),
    pathlib.Path("deploy/kpf_light/systemd/astra-kpf-light.service"),
    pathlib.Path("deploy/kpf_light/systemd/astra-kpf-light.timer"),
]
files.extend(sorted(path.relative_to(root) for path in (root / "deploy/kpf_light/vendor/kpf").glob("*.py")))
payload = []
for rel in files:
    data = (root / rel).read_bytes()
    payload.append({"path": str(rel).replace("\\\\", "/"), "sha256": hashlib.sha256(data).hexdigest()})
print(hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16])
PY
)"
release_id="pkg-$package_hash"
staging="$RELEASES_DIR/.staging-$release_id"
release="$RELEASES_DIR/$release_id"

install -d -m 0755 "$APP_ROOT" "$RELEASES_DIR" "$DATA_DIR" "$DATA_DIR/published"
install -d -m 0755 "$DATA_DIR/source" "$DATA_DIR/quarantine" "$DATA_DIR/failures"
safe_rm_staging "$staging"
install -d -m 0755 "$staging/tools" "$staging/deploy/kpf_light/systemd" "$staging/deploy/kpf_light/vendor"

install -m 0644 "$SRC_DIR/tools/astra_kpf_light_v1.py" "$staging/tools/astra_kpf_light_v1.py"
install -m 0644 "$SRC_DIR/deploy/kpf_light/README.md" "$staging/deploy/kpf_light/README.md"
install -m 0644 "$SRC_DIR/deploy/kpf_light/astra-kpf-light.env.example" "$staging/deploy/kpf_light/astra-kpf-light.env.example"
install -m 0644 "$SRC_DIR/deploy/kpf_light/source_manifest.json" "$staging/deploy/kpf_light/source_manifest.json"
cp -R "$SRC_DIR/deploy/kpf_light/vendor/kpf" "$staging/deploy/kpf_light/vendor/kpf"
install -m 0644 "$SRC_DIR/deploy/kpf_light/systemd/astra-kpf-light.service" "$staging/deploy/kpf_light/systemd/astra-kpf-light.service"
install -m 0644 "$SRC_DIR/deploy/kpf_light/systemd/astra-kpf-light.timer" "$staging/deploy/kpf_light/systemd/astra-kpf-light.timer"

python3 - "$SRC_DIR" "$staging/deploy/kpf_light/package_manifest.json" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
files = [
    pathlib.Path("tools/astra_kpf_light_v1.py"),
    pathlib.Path("deploy/kpf_light/README.md"),
    pathlib.Path("deploy/kpf_light/astra-kpf-light.env.example"),
    pathlib.Path("deploy/kpf_light/source_manifest.json"),
    pathlib.Path("deploy/kpf_light/systemd/astra-kpf-light.service"),
    pathlib.Path("deploy/kpf_light/systemd/astra-kpf-light.timer"),
]
files.extend(sorted(path.relative_to(root) for path in (root / "deploy/kpf_light/vendor/kpf").glob("*.py")))
records = []
for rel in files:
    data = (root / rel).read_bytes()
    records.append({"path": str(rel).replace("\\", "/"), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
payload = {"schema": "astra_kpf_light_package_manifest@1", "files": records}
payload["package_sha256"] = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
PY

python3 "$staging/tools/astra_kpf_light_v1.py" verify-vendor --data-root "$DATA_DIR"

if [ -e "$release" ]; then
  safe_rm_staging "$staging"
else
  mv "$staging" "$release"
fi

old_target="$(release_target_or_empty "$CURRENT_LINK")"
next_link="$APP_ROOT/.current-next"
ln -sfn "$release" "$next_link"
mv -Tf "$next_link" "$CURRENT_LINK"
if [ -n "$old_target" ] && [ "$old_target" != "$release" ]; then
  ln -sfn "$old_target" "$PREVIOUS_LINK"
fi

if [ ! -f "$ENV_FILE" ]; then
  install -m 0644 "$release/deploy/kpf_light/astra-kpf-light.env.example" "$ENV_FILE"
fi

if id "$RUN_USER" >/dev/null 2>&1; then
  for owned_path in "$DATA_DIR" "$DATA_DIR/published" "$DATA_DIR/source" "$DATA_DIR/quarantine" "$DATA_DIR/failures"; do
    case "$owned_path" in
      "$DATA_DIR"|"$DATA_DIR"/*) ;;
      *) echo "Refusing to chown unexpected path: $owned_path" >&2; exit 1 ;;
    esac
    if [ -L "$owned_path" ]; then
      echo "Refusing to chown symlink data path: $owned_path" >&2
      exit 1
    fi
    chown "$RUN_USER:$RUN_GROUP" "$owned_path"
  done
fi

keep_a="$(release_target_or_empty "$CURRENT_LINK")"
keep_b="$(release_target_or_empty "$PREVIOUS_LINK")"
for candidate in "$RELEASES_DIR"/pkg-*; do
  [ -e "$candidate" ] || continue
  candidate_resolved="$(readlink -f "$candidate")"
  if [ "$candidate_resolved" = "$keep_a" ]; then
    continue
  fi
  if [ -n "$keep_b" ] && [ "$candidate_resolved" = "$keep_b" ]; then
    continue
  fi
  safe_rm_release_dir "$candidate"
done

install -m 0644 "$release/deploy/kpf_light/systemd/astra-kpf-light.service" /etc/systemd/system/astra-kpf-light.service
install -m 0644 "$release/deploy/kpf_light/systemd/astra-kpf-light.timer" /etc/systemd/system/astra-kpf-light.timer
systemctl daemon-reload

echo "Installed Astra KPF light release: $release"
echo "Enable manually with: systemctl enable --now astra-kpf-light.timer"
