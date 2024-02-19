#!/bin/bash
# This should only run in environments where both
# datasette-init and datasette-json-html are installed

PLUGINS=$(datasette plugins)
if ! echo "$PLUGINS" | jq 'any(.[]; .name == "datasette-json-html")' | grep -q true; then
  echo "Test failed: datasette-json-html not found"
  exit 1
fi

PLUGINS2=$(DATASETTE_LOAD_PLUGINS=datasette-init datasette plugins)
if ! echo "$PLUGINS2" | jq 'any(.[]; .name == "datasette-json-html")' | grep -q false; then
  echo "Test failed: datasette-json-html should not have been loaded"
  exit 1
fi

if ! echo "$PLUGINS2" | jq 'any(.[]; .name == "datasette-init")' | grep -q true; then
  echo "Test failed: datasette-init should have been loaded"
  exit 1
fi

PLUGINS3=$(DATASETTE_LOAD_PLUGINS='' datasette plugins)
if ! echo "$PLUGINS3" | grep -q '\[\]'; then
  echo "Test failed: datasette plugins should have returned []"
  exit 1
fi
