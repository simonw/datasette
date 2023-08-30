#!/bin/bash
# This should only run in environemnts where both
# datasette-init and datasette-json-html are installed

PLUGINS=$(datasette plugins)
echo "$PLUGINS" | jq 'any(.[]; .name == "datasette-json-html")' | \
  grep -q true || ( \
    echo "Test failed: datasette-json-html not found" && \
    exit 1 \
  )
# With the DATASETTE_LOAD_PLUGINS we should not see that
PLUGINS2=$(DATASETTE_LOAD_PLUGINS=datasette-init datasette plugins)
echo "$PLUGINS2" | jq 'any(.[]; .name == "datasette-json-html")' | \
  grep -q false || ( \
    echo "Test failed: datasette-json-html should not have been loaded" && \
    exit 1 \
  )
echo "$PLUGINS2" | jq 'any(.[]; .name == "datasette-init")' | \
  grep -q true || ( \
    echo "Test failed: datasette-init should have been loaded" && \
    exit 1 \
  )
# With DATASETTE_LOAD_PLUGINS='' we should see no plugins
PLUGINS3=$(DATASETTE_LOAD_PLUGINS='' datasette plugins)
echo "$PLUGINS3"| \
  grep -q '\[\]' || ( \
    echo "Test failed: datasette plugins should have returned []" && \
    exit 1 \
  )
