#!/bin/sh
# Validate the HFS v2 source and generated-wrapper contract without network access.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/hfs-export.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM

commit=$(git -C "$repo_dir" rev-parse --verify 'HEAD^{commit}')
case "$commit" in
    ''|*[!0-9a-f]*)
        printf '%s\n' "HFS contract validation requires a lowercase 40-character HEAD commit" >&2
        exit 1
        ;;
esac
[ "${#commit}" -eq 40 ] || {
    printf '%s\n' "HFS contract validation requires a 40-character HEAD commit" >&2
    exit 1
}

"$repo_dir/cloud/hfs/export_space_bundle.sh" --output "$tmp_dir/bundle" --commit "$commit" >/dev/null
python3 -B - "$repo_dir" "$tmp_dir/bundle" "$commit" <<'PY'
import ast
import os
import pathlib
import re
import sys
import tomllib

root = pathlib.Path(sys.argv[1])
bundle = pathlib.Path(sys.argv[2])
expected_commit = sys.argv[3]
errors = []

def require(condition, message):
    if not condition:
        errors.append(message)

def text(relative):
    return (root / relative).read_text(encoding="utf-8")

# HFS v2 metadata: exact semantics and environment-name-only classifications.
config = tomllib.loads(text("hfs-dev.toml"))
expected_metadata = {
    "standard": "2.0",
    "project": "imagemagickapi-hfs",
    "space": "BlueSkyXN/ImageMagickAPI-HFS",
    "sovereignty": "sovereign",
    "lane": "source",
    "version_source": "commit",
}
require({key: config.get(key) for key in expected_metadata} == expected_metadata,
        "hfs-dev.toml metadata must match the HFS v2 source-lane contract")
candidate = tomllib.loads(text("hfs-dev.candidate.toml"))
require(candidate.get("space") == "BlueSkyXN/ImageMagickAPI-HFS-v2-candidate",
        "candidate manifest must use the fixed v2 candidate Space")
for key in sorted(set(config) | set(candidate)):
    if key != "space":
        require(config.get(key) == candidate.get(key), f"candidate manifest differs at {key}")
require(set(config) == set(expected_metadata) | {"local_only", "secrets", "variables"},
        "hfs-dev.toml may contain only required metadata and root Settings fields")
roles = config
name_re = re.compile(r"^[A-Z][A-Z0-9_]*$")
role_values = {}
for role in ("local_only", "secrets", "variables"):
    values = roles.get(role)
    require(isinstance(values, list) and all(isinstance(value, str) and name_re.fullmatch(value) for value in values or []),
            f"{role} must contain environment key names only")
    require(len(values or []) == len(set(values or [])), f"{role} contains duplicate keys")
    role_values[role] = set(values or [])
require(not (role_values["local_only"] & role_values["secrets"] or
             role_values["local_only"] & role_values["variables"] or
             role_values["secrets"] & role_values["variables"]),
        "environment classifications must be disjoint")
expected_local = {"TEMP_DIR"}
expected_variables = {
    "PORT", "PYTHONUNBUFFERED", "MAGICK_MEMORY_LIMIT", "MAGICK_MAP_LIMIT",
    "MAGICK_DISK_LIMIT", "MAGICK_TIME_LIMIT", "MAGICK_THREAD_LIMIT", "WORKERS",
    "MAX_CONCURRENT_PER_WORKER",
}
require(role_values["local_only"] == expected_local, "local_only keys must be exact")
require(role_values["secrets"] == set(), "this service has no classified secrets")
require(role_values["variables"] == expected_variables, "variable keys must be exact")

env_example = root / ".env.example"
require(env_example.is_file(), ".env.example is required")
env_keys = {
    line.split("=", 1)[0]
    for line in env_example.read_text(encoding="utf-8").splitlines()
    if line and not line.startswith("#") and "=" in line
}
require(env_keys == expected_local | expected_variables,
        ".env.example must register every and only non-secret runtime key")

# Wrapper source, export allowlist, and one-location full-SHA provenance.
wrapper = root / "cloud/hfs"
allowed = {".dockerignore", "BUILD_SOURCE.txt", "Dockerfile", "README.md", "hfs-dev.toml"}
require({path.name for path in bundle.iterdir()} == allowed, "export must contain exactly the five-file allowlist")
for name in allowed:
    require((bundle / name).is_file(), f"export missing {name}")
wrapper_readme = text("cloud/hfs/README.md")
require("\nemoji: 🖼️\n" in wrapper_readme,
        "Space card emoji must be a valid pictographic character")
template = (wrapper / "Dockerfile.template").read_text(encoding="utf-8")
exported_dockerfile = (bundle / "Dockerfile").read_text(encoding="utf-8")
exporter = (wrapper / "export_space_bundle.sh").read_text(encoding="utf-8")
require("archive --format=tar" in exporter and 'show "$commit:$manifest_file"' in exporter,
        "exporter must source wrapper and root manifest from the requested Git archive commit")
require(template.count("__SOURCE_COMMIT__") == 1, "Dockerfile template must have one SHA placeholder")
sha_matches = re.findall(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", exported_dockerfile)
require(sha_matches == [expected_commit],
        "exported Dockerfile must contain exactly the requested full 40-character commit")
arg_lines = [line.strip() for line in exported_dockerfile.splitlines() if line.strip().startswith("ARG ")]
require(arg_lines == [f"ARG SOURCE_COMMIT={expected_commit}"],
        "exported Dockerfile must inject the provenance through exactly one Docker ARG")
require(re.search(r"git clone https://github\.com/BlueSkyXN/ImageMagickAPI-HFS\.git", template) is not None,
        "Space Dockerfile must clone the canonical GitHub source")
require("git fetch --no-tags origin \"$SOURCE_COMMIT\"" in template,
        "Space Dockerfile must fetch the pinned commit")
require("git checkout --detach \"$SOURCE_COMMIT\"" in template,
        "Space Dockerfile must detached-checkout the pinned commit")
require("test \"$(git rev-parse HEAD)\" = \"$SOURCE_COMMIT\"" in template,
        "Space Dockerfile must assert the checked-out HEAD")
copy_lines = [line.strip() for line in template.splitlines() if line.strip().startswith("COPY ")]
require(copy_lines and all("--from=source" in line for line in copy_lines),
        "runtime product files may be copied only from the source stage")
for copy_line in (
    "COPY --from=source /src/requirements.txt ./",
    "COPY --from=source /src/main.py ./",
    "COPY --from=source /src/entrypoint.sh ./",
    "COPY --from=source /src/static/ ./static/",
    "COPY --from=source /src/templates/ ./templates/",
):
    require(copy_line in copy_lines, f"Space Dockerfile missing required source-stage copy: {copy_line}")
require("COPY ." not in template and "COPY ./" not in template,
        "Space Dockerfile may not copy the Space context")

# Preserve required runtime, resource, port, and concurrency values in both Dockerfiles.
required_docker = {
    "FROM python:3.10-slim", "imagemagick", "libheif-examples",
    "libheif-plugin-aomenc", "libheif-plugin-x265", "ENV PORT=8000",
    "ENV PYTHONUNBUFFERED=1", "ENV TEMP_DIR=/app/temp",
    "ENV MAGICK_MEMORY_LIMIT=512MiB", "ENV MAGICK_MAP_LIMIT=1GiB",
    "ENV MAGICK_DISK_LIMIT=4GiB", "ENV MAGICK_TIME_LIMIT=300",
    "ENV MAGICK_THREAD_LIMIT=2", "ENV WORKERS=4", "ENV MAX_CONCURRENT_PER_WORKER=3",
    "EXPOSE 8000",
}
for dockerfile_name, dockerfile in (("Dockerfile", text("Dockerfile")), ("Dockerfile.template", template)):
    for invariant in required_docker:
        require(invariant in dockerfile, f"{dockerfile_name} missing runtime invariant: {invariant}")

# Entrypoint and health endpoint fail closed, without spawning external which.
entrypoint = text("entrypoint.sh")
for invariant in (
    "command -v magick", "command -v heif-enc", "magick --version >/dev/null 2>&1",
    "heif-enc --help >/dev/null 2>&1", "exit 1", "--workers $WORKERS",
):
    require(invariant in entrypoint, f"entrypoint missing fail-closed invariant: {invariant}")
main_source = text("main.py")
try:
    ast.parse(main_source, filename="main.py")
except SyntaxError as exc:
    require(False, f"main.py must parse: {exc}")
require("shutil.which(" in main_source, "health/dependency checks must use shutil.which")
require("'which'" not in main_source and '"which"' not in main_source,
        "main.py may not invoke an external which command")
require("JSONResponse(status_code=503" in main_source and '"status"] = "unhealthy"' in main_source,
        "health must return non-2xx unhealthy responses")
require("except OSError as exc:" in main_source,
        "subprocess creation errors must be handled")
require("['heif-enc']" in main_source and "commands[1].append('--avif')" in main_source,
        "AVIF/HEIF output must use the explicit libheif encoder path")
for invariant in (
    "asyncio.wait_for(process.communicate(), timeout=5)",
    "process.returncode != 0",
    "tempfile.NamedTemporaryFile(dir=TEMP_DIR",
):
    require(invariant in main_source, f"health must fail closed when a required runtime probe fails: {invariant}")

# Ignore boundaries and sensitive literal checks.
for ignore_name in (".gitignore", ".dockerignore"):
    ignore_text = text(ignore_name)
    for invariant in (".env", ".env.*", "!.env.example", "config.toml", "local/", "*.pem", "*.key"):
        require(invariant in ignore_text, f"{ignore_name} missing ignore boundary: {invariant}")
gitignore = text(".gitignore")
for invariant in (".hfs-smoke-*", ".hfs-export-*", "__pycache__/"):
    require(invariant in gitignore, f".gitignore missing generated-artifact boundary: {invariant}")

source_files = [
    "hfs-dev.toml", ".env.example", ".dockerignore", ".gitignore", "Dockerfile", "entrypoint.sh", "main.py",
    "cloud/hfs/Dockerfile.template", "cloud/hfs/export_space_bundle.sh", "cloud/hfs/smoke-test.sh",
    "cloud/hfs/README.md", "cloud/hfs/BUILD_SOURCE.txt", "cloud/hfs/.dockerignore",
    ".github/workflows/sync-to-hf-space.yml", ".github/workflows/hfs-verify.yml",
]
token_pattern = re.compile(r"\bhf_[A-Za-z0-9]{20,}\b|https?://[^\s/@]+:[^\s/@]+@", re.IGNORECASE)
for relative in source_files:
    candidate = root / relative
    require(candidate.is_file(), f"required contract file missing: {relative}")
    if candidate.is_file():
        require(token_pattern.search(candidate.read_text(encoding="utf-8")) is None,
                f"token literal or credential URL found in {relative}")

# Smoke test must validate content types and format-specific signatures without fixtures.
smoke = text("cloud/hfs/smoke-test.sh")
for invariant in (
    "python3 -B -", "verify_response webp image/webp", "verify_response avif image/avif",
    "verify_response heif image/heif", "RIFF/WEBP magic", "ISO-BMFF ftyp box",
    "{b\"avif\", b\"avis\"}",
):
    require(invariant in smoke, f"smoke test missing required format validation: {invariant}")

# Publish is guarded manual replacement; verification is secret-free and never publishes.
publish = text(".github/workflows/sync-to-hf-space.yml")
require("workflow_dispatch:" in publish and "confirm:" in publish and "confirm == 'PUBLISH_WRAPPER'" in publish,
        "publish workflow must require workflow_dispatch confirmation")
require("huggingface-cli" in publish or "hf upload" in publish,
        "publish workflow must use the Hugging Face CLI")
require("--delete" not in publish, "publish workflow must not delete remote files")
require("candidate Space must be private" in publish and "refusing non-wrapper Space tree" in publish,
        "publish workflow must preflight private candidate and wrapper allowlist")
require("full Space tree readback" in publish,
        "publish workflow must read back the complete wrapper tree")
require("huggingface_hub==1.24.0" in publish,
        "publish workflow must install a pinned Hugging Face CLI")
require("cmp \"$BUNDLE_DIR/$file\" \"$READBACK_DIR/$file\"" in publish,
        "publish workflow must compare the critical CLI readback files with the wrapper bundle")
require("git push" not in publish and "--force" not in publish and "git remote" not in publish,
        "publish workflow may not use Git remotes, push, or force-push")
verify = text(".github/workflows/hfs-verify.yml")
require("pull_request:" in verify and "push:" in verify and "main" in verify,
        "verify workflow must run for PRs and main")
require("static-check.sh" in verify and "docker build" in verify and "smoke-test.sh" in verify,
        "verify workflow must validate, build, and smoke test")
require("secrets." not in verify and "HF_TOKEN" not in verify,
        "verify workflow must be secret-free")
require("if: failure()" in verify and "docker logs \"$CONTAINER_NAME\"" in verify,
        "verify workflow must emit container diagnostics after a failure")
require("docker logs \"$CONTAINER_NAME\" || true" not in verify,
        "container diagnostics must not mask a build or startup failure")
require("if: always()" in verify and "docker rm --force \"$CONTAINER_NAME\"" in verify,
        "verify workflow must always clean up its container")

if errors:
    raise SystemExit("HFS contract validation failed:\n- " + "\n- ".join(errors))
print("HFS contract validation passed")
PY
