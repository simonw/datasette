#!/bin/bash

# Generate certificates
python -m trustme
# This creates server.pem, server.key, client.pem

cleanup () {
    rm server.pem server.key client.pem
}

# Start the server in the background
datasette --memory \
    --ssl-keyfile=server.key \
    --ssl-certfile=server.pem \
    -p 8152 &

# Store the background process ID in a variable
server_pid=$!

test_url='https://localhost:8152/_memory.json'

# Wait for the server to start

# h/t https://github.com/pouchdb/pouchdb/blob/25db22fb0ff025b8d2c698da30c6c409066baa0c/bin/run-test.sh#L102-L113
waiting=0
until $(curl --output /dev/null --silent --insecure --head --fail --max-time 2 $test_url); do
    if [ $waiting -eq 4 ]; then
        echo "$test_url can not be reached, server failed to start"
        cleanup
        exit 1
    fi
    let waiting=waiting+1
    sleep 1
done

# Make a test request using curl
curl -f --cacert client.pem $test_url

# Save curl's exit code (-f option causes it to return one on HTTP errors)
curl_exit_code=$?

# Shut down the server
kill $server_pid 2>/dev/null || true
(
    sleep 5
    if kill -0 $server_pid 2>/dev/null; then
        kill -9 $server_pid 2>/dev/null || true
    fi
) &
killer_pid=$!
wait_status=0
wait $server_pid 2>/dev/null || wait_status=$?
kill $killer_pid 2>/dev/null || true
wait $killer_pid 2>/dev/null || true
if [ $wait_status -eq 137 ]; then
    echo "$server_pid did not stop after SIGTERM, server failed to stop"
    cleanup
    exit 1
fi

# Clean up the certificates
cleanup

echo $curl_exit_code
exit $curl_exit_code
