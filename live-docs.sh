#!/bin/zsh
set -eu

cd -- "${0:A:h}/docs"
exec uv run --with-editable '..[docs]' make livehtml "$@"
