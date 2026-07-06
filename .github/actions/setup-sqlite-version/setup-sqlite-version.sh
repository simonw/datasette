#!/usr/bin/env bash
set -euo pipefail

version_spec="${SQLITE_VERSION:?SQLITE_VERSION is required}"
cflags="${SQLITE_CFLAGS:-}"
skip_activate="${SQLITE_SKIP_ACTIVATE:-false}"
extra_fallback_urls="${SQLITE_EXTRA_FALLBACK_URLS:-}"

case "$version_spec" in
  3.46 | 3.46.0)
    sqlite_version="3.46.0"
    sqlite_year="2024"
    amalgamation_id="3460000"
    builtin_fallback_urls="https://static.simonwillison.net/static/2026/sqlite-amalgamation-3460000.zip"
    ;;
  3.25 | 3.25.0)
    sqlite_version="3.25.0"
    sqlite_year="2018"
    amalgamation_id="3250000"
    builtin_fallback_urls="https://static.simonwillison.net/static/2026/sqlite-amalgamation-3250000.zip?v=1"
    ;;
  *)
    echo "::error::Unsupported SQLite version '$version_spec'. Add its release year and amalgamation id to $GITHUB_ACTION_PATH/setup-sqlite-version.sh."
    exit 1
    ;;
esac

case "$(uname -s)" in
  Linux)
    library_name="libsqlite3.so.0"
    library_path_var="LD_LIBRARY_PATH"
    ;;
  Darwin)
    library_name="libsqlite3.dylib"
    library_path_var="DYLD_LIBRARY_PATH"
    ;;
  *)
    echo "::error::Unsupported platform $(uname -s)"
    exit 1
    ;;
esac

runner_temp="${RUNNER_TEMP:-}"
if [ -z "$runner_temp" ]; then
  runner_temp="$(mktemp -d)"
fi

filename="sqlite-amalgamation-${amalgamation_id}"
official_url="https://www.sqlite.org/${sqlite_year}/${filename}.zip"
download_dir="${runner_temp}/sqlite-versions/downloads"
source_root="${runner_temp}/sqlite-versions/source"
source_dir="${source_root}/${filename}"
build_dir="${runner_temp}/sqlite-versions/build/${sqlite_version}"
archive_path="${download_dir}/${filename}.zip"

mkdir -p "$download_dir" "$source_root" "$build_dir"

download_archive() {
  local url
  local candidate_path="${archive_path}.tmp"
  local urls=("$official_url")

  for url in $builtin_fallback_urls $extra_fallback_urls; do
    urls+=("$url")
  done

  rm -f "$candidate_path"
  for url in "${urls[@]}"; do
    echo "Downloading SQLite ${sqlite_version} amalgamation from ${url}"
    if curl \
      --fail \
      --location \
      --show-error \
      --retry 5 \
      --retry-delay 2 \
      --retry-max-time 180 \
      --retry-all-errors \
      --connect-timeout 20 \
      --max-time 240 \
      --output "$candidate_path" \
      "$url"; then
      mv "$candidate_path" "$archive_path"
      return 0
    fi

    echo "::warning::Download failed from ${url}"
    rm -f "$candidate_path"
  done

  echo "::error::Could not download SQLite ${sqlite_version} amalgamation"
  return 1
}

if [ ! -f "${source_dir}/sqlite3.c" ]; then
  if [ ! -f "$archive_path" ]; then
    download_archive
  fi

  rm -rf "$source_dir"
  unzip -q "$archive_path" -d "$source_root"
fi

if [ ! -f "${source_dir}/sqlite3.c" ]; then
  echo "::error::Expected ${source_dir}/sqlite3.c after extracting ${archive_path}"
  exit 1
fi

read -r -a cflag_args <<< "$cflags"

echo "Compiling SQLite ${sqlite_version} to ${build_dir}/${library_name}"
gcc \
  -fPIC \
  -shared \
  "${cflag_args[@]}" \
  "${source_dir}/sqlite3.c" \
  "-I${source_dir}" \
  -o "${build_dir}/${library_name}"

if [ "$library_name" = "libsqlite3.so.0" ]; then
  ln -sf "$library_name" "${build_dir}/libsqlite3.so"
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "sqlite-location=${build_dir}" >> "$GITHUB_OUTPUT"
else
  echo "sqlite-location=${build_dir}"
fi

case "$(printf '%s' "$skip_activate" | tr '[:upper:]' '[:lower:]')" in
  true | 1 | yes)
    echo "Skipping ${library_path_var} activation"
    ;;
  *)
    existing_value="${!library_path_var:-}"
    if [ -n "${GITHUB_ENV:-}" ]; then
      if [ -n "$existing_value" ]; then
        echo "${library_path_var}=${build_dir}:${existing_value}" >> "$GITHUB_ENV"
      else
        echo "${library_path_var}=${build_dir}" >> "$GITHUB_ENV"
      fi
    fi
    echo "Added ${build_dir} to ${library_path_var}"
    ;;
esac
