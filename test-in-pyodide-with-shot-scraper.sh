#!/bin/bash
set -e
# So the script fails if there are any errors

# Build the wheel
python3 -m build

# Find name of wheel, strip off the dist/
wheel=$(basename $(ls dist/*.whl) | head -n 1)

# Create a blank index page
echo '
<script src="https://cdn.jsdelivr.net/pyodide/v0.20.0/full/pyodide.js"></script>
' > dist/index.html

# Run a server for that dist/ folder
cd dist
python3 -m http.server 8529 &
cd ..

# Register the kill_server function to be called on script exit
kill_server() {
  pkill -f 'http.server 8529'
}
trap kill_server EXIT


shot-scraper javascript http://localhost:8529/ "
async () => {
  let pyodide = await loadPyodide();
  await pyodide.loadPackage(['micropip', 'ssl', 'setuptools']);
  let output = await pyodide.runPythonAsync(\`
    import micropip
    await micropip.install('h11==0.12.0')
    await micropip.install('httpx==0.23')
    # To avoid 'from typing_extensions import deprecated' error:
    await micropip.install('typing-extensions>=4.12.2')
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
