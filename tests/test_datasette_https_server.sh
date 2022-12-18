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
kill $server_pid
waiting=0
#         show all pids
#         |       find just the $server_pid
#         |       |                  don’t match on the previous grep
#         |       |                  |            we don’t need the output
#         |       |                  |            |
until ( ! ps ax | grep $server_pid | grep -v grep > /dev/null ); do
    if [ $waiting -eq 4 ]; then
        echo "$server_pid does still exist, server failed to stop"
        cleanup
        exit 1
    fi
    let waiting=waiting+1
    sleep 1
done

# Clean up the certificates
cleanup

echo $curl_exit_code
exit $curl_exit_code
