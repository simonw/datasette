#!/bin/bash
# Recreate docs/datasette-modal-example.png
#
# Takes a screenshot of the <datasette-modal> example plugin from the
# "Modal dialogs" section of docs/javascript_plugins.rst, running against
# a temporary Datasette instance that this script starts and stops.
#
# Requirements:
# - datasette importable by $PYTHON (e.g. "pip install -e ." in this repo)
# - uv, for "uvx shot-scraper" and the Pillow-based PNG quantization step
#   (shot-scraper needs a Playwright browser: "uvx shot-scraper install")
#
# Environment variable overrides:
#   PYTHON        Python command (default: python3)
#   SHOT_SCRAPER  shot-scraper command (default: uvx shot-scraper)
#   PORT          port for the temporary server (default: 8574)
set -euo pipefail

read -r -a PYTHON_CMD <<< "${PYTHON:-python3}"
read -r -a SHOT_SCRAPER_CMD <<< "${SHOT_SCRAPER:-uvx shot-scraper}"
PORT="${PORT:-8574}"

docs_dir="$(cd "$(dirname "$0")" && pwd)"
output="$docs_dir/datasette-modal-example.png"

tmp_dir=$(mktemp -d)
server_pid=""

cleanup() {
  if [ -n "$server_pid" ]; then
    kill "$server_pid" 2>/dev/null || true
  fi
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

# A small demo database for the page shown behind the dialog
"${PYTHON_CMD[@]}" - "$tmp_dir/demo.db" <<'EOF'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
conn.executescript(
    """
create table plugins (id integer primary key, name text, description text);
insert into plugins (name, description) values
  ('datasette-cluster-map', 'Renders a map of geographic data'),
  ('datasette-vega', 'Visualize data with Vega charts'),
  ('datasette-graphql', 'GraphQL endpoint for Datasette');
"""
)
conn.commit()
EOF

# The example plugin code from the "Modal dialogs" section of
# docs/javascript_plugins.rst, injected via extra_body_script()
mkdir "$tmp_dir/plugins"
cat > "$tmp_dir/plugins/modal_example.py" <<'EOF'
from datasette import hookimpl


@hookimpl
def extra_body_script():
    return {
        "script": """
document.addEventListener("datasette_init", function (event) {
  const manager = event.detail;
  const modal = manager.createModal({
    id: "my-plugin-dialog",
    className: "my-plugin-dialog",
    title: "My plugin",
    content: `
      <p style="padding: 16px 24px">Hello from a plugin!</p>
      <div class="modal-footer">
        <span class="footer-info"></span>
        <button type="button" class="btn btn-ghost" data-modal-cancel>Cancel</button>
        <button type="button" class="btn btn-primary my-plugin-save">Save</button>
      </div>
    `,
  });
  if (!modal) {
    return; // Browser does not support <dialog>
  }
  // Open it later, for example from a button click:
  // modal.showModal({trigger: button});
});
"""
    }
EOF

"${PYTHON_CMD[@]}" -m datasette "$tmp_dir/demo.db" \
  --plugins-dir "$tmp_dir/plugins" --port "$PORT" &
server_pid=$!

# Wait for the server to start responding
for _ in $(seq 1 50); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then
    break
  fi
  sleep 0.2
done

# Open the modal with animations disabled, blur the auto-focused Cancel
# button so no focus ring appears, then take a retina viewport shot
"${SHOT_SCRAPER_CMD[@]}" shot "http://127.0.0.1:$PORT/demo/plugins" \
  --javascript '
    new Promise((resolve) => {
      const style = document.createElement("style");
      style.textContent =
        "dialog, dialog::backdrop { animation: none !important; }";
      document.head.appendChild(style);
      const modal = document
        .getElementById("my-plugin-dialog")
        .closest("datasette-modal");
      modal.showModal();
      if (document.activeElement) {
        document.activeElement.blur();
      }
      setTimeout(resolve, 500);
    })' \
  --width 760 --height 460 --retina \
  --output "$tmp_dir/shot.png" --silent

kill "$server_pid" 2>/dev/null || true
wait "$server_pid" 2>/dev/null || true
server_pid=""

# Quantize to an 8-bit palette PNG to roughly halve the file size
cat > "$tmp_dir/quantize.py" <<'EOF'
import sys

from PIL import Image

img = Image.open(sys.argv[1]).convert("RGB")
img.quantize(
    colors=256,
    method=Image.Quantize.MEDIANCUT,
    dither=Image.Dither.FLOYDSTEINBERG,
).save(sys.argv[2], optimize=True)
EOF
uv run --no-project --with pillow python \
  "$tmp_dir/quantize.py" "$tmp_dir/shot.png" "$output"

echo "Wrote $output"
