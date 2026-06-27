#!/bin/bash
set -euo pipefail

read -r -a PYTHON_CMD <<< "${PYTHON:-python3}"
read -r -a SHOT_SCRAPER_CMD <<< "${SHOT_SCRAPER:-shot-scraper}"

# Build the wheel
"${PYTHON_CMD[@]}" -m build

# Find name of most recently built wheel, strip off the dist/
wheel=$(basename "$(ls -t dist/*.whl | head -n 1)")

# Create a blank index page
echo '
<script src="https://cdn.jsdelivr.net/pyodide/v314.0.0/full/pyodide.js"></script>
' > dist/index.html

# Run a server for that dist/ folder
"${PYTHON_CMD[@]}" -m http.server 8529 --directory dist &
server_pid=$!

# Register the kill_server function to be called on script exit
kill_server() {
  kill "$server_pid" 2>/dev/null || true
}
trap kill_server EXIT


"${SHOT_SCRAPER_CMD[@]}" javascript http://localhost:8529/ "
async () => {
  let pyodide = await loadPyodide();
  await pyodide.loadPackage(['micropip', 'setuptools']);
  let output = await pyodide.runPythonAsync(\`
    import micropip
    await micropip.install('http://localhost:8529/$wheel')
    import ssl
    import setuptools
    from datasette.app import Datasette
    ds = Datasette(memory=True, settings={'num_sql_threads': 0})
    (await ds.client.get('/_memory/-/query.json?sql=select+55+as+itworks&_shape=array')).text
  \`);
  if (JSON.parse(output)[0].itworks != 55) {
    throw 'Got ' + output + ', expected itworks: 55';
  }
  return 'Test passed!';
}
"
