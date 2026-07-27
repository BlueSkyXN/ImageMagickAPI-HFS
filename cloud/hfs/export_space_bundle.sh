#!/bin/sh
# Export the sole allowed Hugging Face Space wrapper bundle.
set -eu

usage() {
    printf '%s\n' "Usage: $0 --output DIRECTORY [--commit COMMIT]" >&2
    exit 64
}

fail() {
    printf '%s\n' "error: $*" >&2
    exit 1
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$script_dir/../.." && pwd)
output_dir=
commit_ref=

while [ "$#" -gt 0 ]; do
    case "$1" in
        --output)
            [ "$#" -ge 2 ] || usage
            output_dir=$2
            shift 2
            ;;
        --commit)
            [ "$#" -ge 2 ] || usage
            commit_ref=$2
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            usage
            ;;
    esac
done

[ -n "$output_dir" ] || usage
git -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || fail "repository metadata is required to resolve provenance"

if [ -z "$commit_ref" ]; then
    commit_ref=HEAD
fi
commit=$(git -C "$repo_dir" rev-parse --verify "${commit_ref}^{commit}") \
    || fail "cannot resolve commit: $commit_ref"
if ! printf '%s\n' "$commit" | grep -Eq '^[0-9a-f]{40}$'; then
    fail "resolved commit is not an exact 40-character lowercase SHA"
fi

archive_dir=$(mktemp -d "${TMPDIR:-/tmp}/imagemagick-hfs-source.XXXXXX")
cleanup() {
    rm -rf "$archive_dir"
}
trap cleanup EXIT HUP INT TERM

# Export from the requested immutable commit, not the caller's possibly dirty
# checkout.  This keeps wrapper files, the root manifest, and SOURCE_COMMIT
# bound to the same auditable Git tree.
git -C "$repo_dir" archive --format=tar "$commit" -- cloud/hfs \
    | tar -xf - -C "$archive_dir" --strip-components=2 \
    || fail "cannot archive HFS wrapper from commit $commit"
git -C "$repo_dir" show "$commit:hfs-dev.toml" > "$archive_dir/hfs-dev.toml" \
    || fail "cannot read root hfs-dev.toml from commit $commit"
wrapper_dir="$archive_dir"

if [ -e "$output_dir" ]; then
    [ -d "$output_dir" ] || fail "output path exists and is not a directory"
    [ -z "$(find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit)" ] \
        || fail "output directory must be empty"
else
    mkdir -p "$output_dir" || fail "cannot create output directory"
fi

for file in .dockerignore BUILD_SOURCE.txt README.md Dockerfile.template; do
    [ -f "$wrapper_dir/$file" ] || fail "missing wrapper source file in commit: $file"
done
[ -f "$wrapper_dir/hfs-dev.toml" ] || fail "missing root hfs-dev.toml in commit"

placeholder_count=$(grep -o '__SOURCE_COMMIT__' "$wrapper_dir/Dockerfile.template" | wc -l | tr -d ' ')
[ "$placeholder_count" -eq 1 ] || fail "Dockerfile template must contain exactly one provenance placeholder"

umask 077
cp "$wrapper_dir/.dockerignore" "$output_dir/.dockerignore"
cp "$wrapper_dir/BUILD_SOURCE.txt" "$output_dir/BUILD_SOURCE.txt"
cp "$wrapper_dir/README.md" "$output_dir/README.md"
cp "$wrapper_dir/hfs-dev.toml" "$output_dir/hfs-dev.toml"
python3 -B - "$wrapper_dir/Dockerfile.template" "$output_dir/Dockerfile" "$commit" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
output = pathlib.Path(sys.argv[2])
commit = sys.argv[3]
if source.count("__SOURCE_COMMIT__") != 1:
    raise SystemExit("error: provenance placeholder count changed during export")
output.write_text(source.replace("__SOURCE_COMMIT__", commit), encoding="utf-8")
PY

[ "$(find "$output_dir" -mindepth 1 -maxdepth 1 -type f -print | wc -l | tr -d ' ')" -eq 5 ] \
    || fail "export did not create exactly five files"
for file in .dockerignore BUILD_SOURCE.txt Dockerfile README.md hfs-dev.toml; do
    [ -f "$output_dir/$file" ] || fail "export missing required file: $file"
done
if find "$output_dir" -mindepth 1 -maxdepth 1 ! -type f -print -quit | grep -q .; then
    fail "export contains a non-file entry"
fi

printf '%s\n' "Exported five-file Space wrapper for commit $commit to $output_dir"
