#!/bin/bash
# Target host (change 'localhost' to your target IP/domain if needed)
HOST="localhost"
PORT="3306"

# Run nc with -z (zero-I/O mode) and -v (verbose). 
# Redirect both stdout and stderr to /dev/null to keep it quiet.
if nc -zv "$HOST" "$PORT" > /dev/null 2>&1; then
    # Success: Print nothing and exit 0
    exit 0
else
    # Failure: Print error to stderr and exit 1
    echo "Error: Cannot connect to $HOST on port $PORT" >&2
    exit 1
fi
