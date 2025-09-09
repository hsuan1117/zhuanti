#!/bin/bash

TOTAL=10000
PARALLEL=10
EACH=$((TOTAL / PARALLEL))
SERVER=radius-service
SECRET=testing123

START=$(date +%s)

for ((c=1; c<=PARALLEL; c++)); do
    (
        for ((i=1; i<=EACH; i++)); do
            echo "User-Name = testuser, User-Password = testpassword" | \
            radclient $SERVER auth $SECRET >/dev/null 2>&1
        done
    ) &
done

wait

END=$(date +%s)
ELAPSED=$((END - START))
echo "Total time for $TOTAL radclient requests with $PARALLEL parallel clients: $ELAPSED seconds"
