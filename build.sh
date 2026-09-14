#!/bin/sh
set -eu

# Keep the sequence explicit: formatting, tests, static checks, then build.
unformatted=$(gofmt -l .)
if [ -n "$unformatted" ]; then
	printf 'The following Go files need formatting:\n%s\n' "$unformatted"
	exit 1
fi

go test ./...
go vet ./...
go build ./...
