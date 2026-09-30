#!/bin/sh
printf 'Hello from an isolated shell\n'
printf 'Working directory: %s\n' "$PWD"
printf 'Temporary workspace is writable\n' > /tmp/hello.txt
cat /tmp/hello.txt
