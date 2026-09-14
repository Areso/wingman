# Testing Wingman

Wingman uses Go's built-in `testing` package. No separate test framework, real
Telegram token, real database, or running Wingman process is required.

## Run everything

From the repository root:

```sh
./build.sh
```

The script deliberately stops at the first failure and runs these stages in
order:

1. `gofmt -l .` checks formatting. It prints unformatted file names and fails.
2. `go test ./...` builds every package and runs all unit tests.
3. `go vet ./...` performs Go's static correctness checks.
4. `go build ./...` builds every package as the final production build check.

To run only tests, use:

```sh
go test ./...
```

To see every test and subtest as it runs:

```sh
go test -v ./...
```

To run one area or one test:

```sh
go test -v .
go test -v ./channels/telegram
go test -v . -run TestDatabaseTaskLifecycle
go test -v ./channels/telegram -run TestHandleSendMessage
```

To include Go's race detector (slower, useful before a release):

```sh
go test -race ./...
```

## How the tests are organized

- `main_test.go` tests the Core.
- `channels/telegram/main_test.go` tests the Telegram channel.
- A function named `TestSomething` is discovered automatically by `go test`.
- `t.Run(...)` creates named subtests, so a failed input reports exactly which
  scenario failed.
- Table-driven tests put several inputs through the same rule without repeating
  setup code.
- Helper functions call `t.Helper()`, which makes failures point to the test
  line rather than the helper implementation.

## Test isolation

- `t.TempDir()` creates a fresh directory for each filesystem or SQLite test
  and removes it afterward.
- `t.Setenv()` changes an environment variable only for that test and restores
  it afterward.
- `httptest.NewServer()` creates local fake Core, channel, and Telegram HTTP
  servers. Tests make no external network calls.
- `t.Cleanup()` closes databases/servers and restores package-level settings.

These are unit tests: they exercise individual rules and boundaries quickly.
They do not replace a smoke test with a real Telegram bot and real plugin
processes.

## Reading a test

Most tests follow the Arrange, Act, Assert sequence:

```go
// Arrange: create isolated input and dependencies.
plugin := validPlugin()

// Act: call the behavior under test.
err := plugin.Validate()

// Assert: compare the observed result with the expected result.
if err != nil {
	t.Fatalf("unexpected error: %v", err)
}
```

`t.Fatal` stops the current test because continuing would be meaningless.
`t.Error` records a failure but allows the current test to make more checks.
