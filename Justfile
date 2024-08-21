export DATASETTE_SECRET := "not_a_secret"

# Run tests and linters
@default: test lint

# Setup project
@init:
  pipenv run pip install -e '.[test,docs]'

# Run pytest with supplied options
@test *options:
  pipenv run pytest {{options}}

@codespell:
  pipenv run codespell README.md --ignore-words docs/codespell-ignore-words.txt
  pipenv run codespell docs/*.rst --ignore-words docs/codespell-ignore-words.txt
  pipenv run codespell datasette -S datasette/static --ignore-words docs/codespell-ignore-words.txt
  pipenv run codespell tests --ignore-words docs/codespell-ignore-words.txt

# Run linters: black, flake8, mypy, cog
@lint: codespell
  pipenv run black . --check
  pipenv run flake8
  pipenv run cog --check README.md docs/*.rst

# Rebuild docs with cog
@cog:
  pipenv run cog -r README.md docs/*.rst

# Serve live docs on localhost:8000
@docs: cog
  pipenv run blacken-docs -l 60 docs/*.rst
  cd docs && pipenv run make livehtml

# Apply Black
@black:
  pipenv run black .

@serve:
  pipenv run sqlite-utils create-database data.db
  pipenv run sqlite-utils create-table data.db docs id integer title text --pk id --ignore
  pipenv run python -m datasette data.db --root --reload
