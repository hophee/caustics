#!/usr/bin/env bash
# Build a self-contained zip that runs Клинический компас on any machine with Docker (no repo needed):
# the app image built from this checkout + postgres image, the bundle compose file, start.sh, README,
# the ОХЛП drug database and (optionally) the diary model weights.
#
#   deploy/make-bundle.sh [--with-model] [--ohlp PATH] [--out DIR]
#
#   --with-model  add Qwen3-4B + opus-mt-ru-en from the Hugging Face cache (+~8 GB; enables KOMPAS_TEXT_MODEL=llm)
#   --ohlp PATH   the drug database (default: ohlp_fields_v4.csv in the main checkout or the repo root)
#   --out DIR     where to write the zip (default: dist/ in the repo root)
#
# On the team server Docker is reached through the docker group and the system daemon:
#   sg docker -c "DOCKER_CONTEXT=default deploy/make-bundle.sh"
set -euo pipefail

repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
with_model=0
ohlp=""
out="$repo/dist"
while [ $# -gt 0 ]; do
  case "$1" in
    --with-model) with_model=1 ;;
    --ohlp) ohlp=$2; shift ;;
    --out) out=$2; shift ;;
    -h|--help) sed -n 2,14p "$0"; exit 0 ;;
    *) echo "unknown option: $1"; exit 1 ;;
  esac
  shift
done

# the drug database: explicit path, else the repo root, else the main checkout (worktrees share it)
if [ -z "$ohlp" ]; then
  main=$(git -C "$repo" worktree list --porcelain | sed -n '1s/^worktree //p')
  for candidate in "$repo/ohlp_fields_v4.csv" "$main/ohlp_fields_v4.csv"; do
    [ -f "$candidate" ] && ohlp=$candidate && break
  done
fi
[ -n "$ohlp" ] && [ -f "$ohlp" ] || { echo "ohlp_fields_v4.csv not found — pass --ohlp PATH"; exit 1; }

hf=${HF_HUB_CACHE:-$HOME/.cache/huggingface/hub}
qwen_rev=$(sed -n 's/.*text_model_revision: str | None = "\([0-9a-f]*\)".*/\1/p' "$repo/src/backend/src/kompas/core/config.py")
opus_rev=$(sed -n 's/^TRANSLATOR_REVISION = "\([0-9a-f]*\)".*/\1/p' "$repo/src/backend/src/kompas/modules/monitoring/llm_model.py")
if [ "$with_model" = 1 ]; then
  for d in "models--Qwen--Qwen3-4B/snapshots/$qwen_rev" "models--Helsinki-NLP--opus-mt-ru-en/snapshots/$opus_rev"; do
    [ -d "$hf/$d" ] || { echo "model weights not found: $hf/$d"; exit 1; }
  done
fi

commit=$(git -C "$repo" rev-parse --short HEAD)
dirty=""
git -C "$repo" diff --quiet HEAD -- src deploy compose.yaml || dirty="-dirty"
suffix=""
[ "$with_model" = 1 ] && suffix="-model"
name="kompas-bundle-$commit$dirty$suffix"
tag="kompas-backend:bundle-$commit$dirty"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
dest="$work/$name"
mkdir -p "$dest/images" "$dest/data" "$dest/hf/hub" "$out"
# always ship hf/hub: otherwise Docker creates the mount point as root in the recipient's folder
echo "Diary model weights go here (the -model bundle ships them; see README.md)." > "$dest/hf/hub/README.txt"

echo "== building $tag from $repo ($commit$dirty)"
docker build --platform linux/amd64 -f "$repo/deploy/Dockerfile" -t "$tag" "$repo"
docker pull --platform linux/amd64 postgres:16-alpine >/dev/null

echo "== saving images"
docker save "$tag" | gzip -1 > "$dest/images/kompas-backend.tar.gz"
docker save postgres:16-alpine | gzip -1 > "$dest/images/postgres-16-alpine.tar.gz"
echo "$tag" > "$dest/images/IMAGE_TAG"

echo "== copying files"
cp "$repo/deploy/bundle/compose.yaml" "$repo/deploy/bundle/README.md" "$repo/deploy/bundle/start.sh" "$repo/deploy/bundle/seed-demo.sh" "$dest/"
cp "$repo/deploy/bundle/env.example" "$dest/.env.example"
sed -i.bak "s|^KOMPAS_IMAGE=.*|KOMPAS_IMAGE=$tag|" "$dest/.env.example" && rm "$dest/.env.example.bak"
chmod +x "$dest/start.sh" "$dest/seed-demo.sh"
cp "$ohlp" "$dest/data/ohlp_fields_v4.csv"
printf 'commit %s%s\nbuilt %s\nimage %s\nmodel %s\n' "$commit" "$dirty" "$(date -u +%Y-%m-%dT%H:%MZ)" "$tag" \
  "$([ "$with_model" = 1 ] && echo "Qwen3-4B@$qwen_rev, opus-mt-ru-en@$opus_rev" || echo none)" > "$dest/VERSION"

if [ "$with_model" = 1 ]; then
  echo "== copying model weights (real files, no symlinks)"
  for spec in "models--Qwen--Qwen3-4B:$qwen_rev" "models--Helsinki-NLP--opus-mt-ru-en:$opus_rev"; do
    model=${spec%%:*}; rev=${spec#*:}
    mkdir -p "$dest/hf/hub/$model/snapshots/$rev" "$dest/hf/hub/$model/refs"
    cp -rL "$hf/$model/snapshots/$rev/." "$dest/hf/hub/$model/snapshots/$rev/"
    echo "$rev" > "$dest/hf/hub/$model/refs/main"
  done
  chmod -R a+rX "$dest/hf"   # the container user (uid 10001) must read them
fi

echo "== zipping"
rm -f "$out/$name.zip"
(cd "$work" && zip -qr -n .gz:.safetensors:.bin "$out/$name.zip" "$name")   # already-compressed files are stored
ls -lh "$out/$name.zip"
echo "Done: $out/$name.zip — the recipient unzips it and runs ./start.sh (see README.md inside)."
