#!/bin/bash

# Target host
HOST="localhost"
# Define an array of ports to check
PORTS=(3306)


ANY_FAILED=0

for PORT in "${PORTS[@]}"; do
    # Run nc with -z (zero-I/O mode) and -v (verbose). 
    # Redirect both stdout and stderr to /dev/null to keep it quiet.
    if nc -zv "$HOST" "$PORT" > /dev/null 2>&1; then
        # Success: Do nothing (or echo "Connected to $PORT" if you want to track success)
        :
    else
        # Failure: Print error to stderr and flag the failure
        echo "Error: Cannot connect to $HOST on port $PORT" >&2
        ANY_FAILED=1
    fi
done

# Exit with 1 if any port failed, otherwise exit 0
if [ $ANY_FAILED -eq 1 ]; then
    exit 1
else
    exit 0
fi
