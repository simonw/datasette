#!/bin/bash

# Generate certificates
python -m trustme
# This creates server.pem, server.key, client.pem

# Start the server in the background
datasette --memory \
    --ssl-keyfile=server.key \
    --ssl-certfile=server.pem \
    -p 8152 &

# Store the background process ID in a variable
server_pid=$!

# Wait for the server to start
sleep 2

# Make a test request using curl
curl -f --cacert client.pem 'https://localhost:8152/_memory.json'

# Save curl's exit code (-f option causes it to return one on HTTP errors)
curl_exit_code=$?

# Shut down the server
kill $server_pid
sleep 1

# Clean up the certificates
rm server.pem server.key client.pem

echo $curl_exit_code
exit $curl_exit_code
