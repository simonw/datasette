export DATASETTE_SECRET := "not_a_secret"

# Run tests and linters
@default: test lint

# Setup project
@init:
  uv sync

# Run pytest with supplied options
@test *options: init
  uv run pytest -n auto {{options}}

@codespell:
  uv run codespell README.md --ignore-words docs/codespell-ignore-words.txt
  uv run codespell docs/*.rst --ignore-words docs/codespell-ignore-words.txt
  uv run codespell datasette -S datasette/static --ignore-words docs/codespell-ignore-words.txt
  uv run codespell tests --ignore-words docs/codespell-ignore-words.txt

# Run linters: black, flake8, mypy, cog
@lint: codespell
  uv run black . --check
  uv run flake8
  uv run cog --check README.md docs/*.rst

# Rebuild docs with cog
@cog:
  uv run cog -r README.md docs/*.rst

# Serve live docs on localhost:8000
@docs: cog blacken-docs
  uv run make -C docs livehtml

# Build docs as static HTML
@docs-build: cog blacken-docs
  rm -rf docs/_build && cd docs && uv run make html

# Apply Black
@black:
  uv run black .

# Apply blacken-docs
@blacken-docs:
  uv run blacken-docs -l 60 docs/*.rst

# Apply prettier
@prettier:
  npm run fix

# Format code with both black and prettier
@format: black prettier blacken-docs

@serve *options:
  uv run sqlite-utils create-database data.db
  uv run sqlite-utils create-table data.db docs id integer title text --pk id --ignore
  uv run python -m datasette data.db --root --reload {{options}}
