#!/bin/sh
set -eu

# This package-only script mirrors the repository build sequence.
unformatted=$(gofmt -l .)
if [ -n "$unformatted" ]; then
	printf 'The following Go files need formatting:\n%s\n' "$unformatted"
	exit 1
fi

go test .
go vet .
go build .
