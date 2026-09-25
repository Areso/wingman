package main

import (
	"io"
	"log"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestPrepareInvocation(t *testing.T) {
	cwd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join("plugins", "weather")
	absExecutable := filepath.Join(t.TempDir(), "python3")
	tests := []struct {
		name           string
		executable     string
		mode           executionMode
		timeoutS       int32
		wantExecutable string
		wantTimeout    time.Duration
	}{
		{
			name: "PATH executable and default timeout", executable: "python3", mode: Sync,
			wantExecutable: "python3", wantTimeout: 30 * time.Second,
		},
		{
			name: "relative executable and explicit timeout", executable: ".venv/bin/python3", mode: Sync, timeoutS: 7,
			wantExecutable: filepath.Join(cwd, dir, ".venv", "bin", "python3"), wantTimeout: 7 * time.Second,
		},
		{
			name: "absolute executable", executable: absExecutable, mode: Sync, timeoutS: 1,
			wantExecutable: absExecutable, wantTimeout: time.Second,
		},
		{
			name: "async ignores configured timeout", executable: "python3", mode: Async, timeoutS: 7,
			wantExecutable: "python3",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			plugin := validPlugin()
			plugin.Dir = dir
			plugin.EntryPoint.Executable = tt.executable
			plugin.InvocationType = string(tt.mode)
			plugin.InvocationTimeoutS = tt.timeoutS
			inv, err := prepareInvocation(plugin, []string{"two words", "-o", "output; touch unsafe"})
			if err != nil {
				t.Fatal(err)
			}
			if inv.Executable != tt.wantExecutable || inv.Dir != dir || inv.Mode != tt.mode || inv.Timeout != tt.wantTimeout {
				t.Fatalf("prepareInvocation() = %+v; want executable %q, dir %q, mode %q, timeout %v", inv, tt.wantExecutable, dir, tt.mode, tt.wantTimeout)
			}
			if want := []string{"weather.py", "two words", "-o", "output; touch unsafe"}; !slices.Equal(inv.Args, want) {
				t.Fatalf("args = %q; want %q", inv.Args, want)
			}
		})
	}
}

func TestPrepareInvocationOwnsArguments(t *testing.T) {
	// Spare capacity exposes an append that accidentally reuses manifest storage.
	manifestArgs := []string{"weather.py", "reserved", "reserved"}
	plugin := validPlugin()
	plugin.EntryPoint.Args = manifestArgs[:1]
	runtimeArgs := []string{"today"}
	inv, err := prepareInvocation(plugin, runtimeArgs)
	if err != nil {
		t.Fatal(err)
	}
	inv.Args[0] = "changed.py"
	inv.Args[1] = "tomorrow"
	if !slices.Equal(manifestArgs, []string{"weather.py", "reserved", "reserved"}) {
		t.Fatalf("preparation modified manifest arguments: %q", manifestArgs)
	}
	if runtimeArgs[0] != "today" {
		t.Fatalf("invocation shares runtime argument storage: %q", runtimeArgs)
	}

	plugin.EntryPoint.Args = nil
	inv, err = prepareInvocation(plugin, runtimeArgs)
	if err != nil {
		t.Fatal(err)
	}
	inv.Args[0] = "tomorrow"
	if runtimeArgs[0] != "today" {
		t.Fatalf("invocation without manifest args shares runtime argument storage: %q", runtimeArgs)
	}
}

func TestExecuteNativeResults(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "input.txt"), []byte("from plugin directory"), 0o600); err != nil {
		t.Fatal(err)
	}
	missingExecutable := filepath.Join(dir, "missing")
	tests := []struct {
		name       string
		executable string
		args       []string
		mode       executionMode
		wantOutput string
		wantRC     int
	}{
		{
			name: "working directory and separate output streams", executable: "sh", mode: Sync,
			args: []string{"-c", "printf 'stderr first' >&2; cat input.txt"}, wantOutput: "from plugin directory\nstderr first",
		},
		{
			name: "nonzero exit retains output", executable: "sh", mode: Sync,
			args: []string{"-c", "printf 'partial output'; printf 'failure' >&2; exit 7"}, wantOutput: "partial output\nfailure", wantRC: 7,
		},
		{name: "sync start failure", executable: missingExecutable, mode: Sync, wantOutput: "\n", wantRC: -2},
		{name: "async start failure", executable: missingExecutable, mode: Async, wantRC: -2},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := executeNative(Invocation{
				Executable: tt.executable,
				Args:       tt.args,
				Dir:        dir,
				Mode:       tt.mode,
				Timeout:    5 * time.Second,
			}, log.New(io.Discard, "", 0))
			if result.Output != tt.wantOutput || result.RC != tt.wantRC || (result.Err != nil) != (tt.wantRC != 0) {
				t.Fatalf("executeNative() = %+v; want output %q, RC %d", result, tt.wantOutput, tt.wantRC)
			}
		})
	}
}

func TestExecuteNativeTimeoutKillsProcessGroup(t *testing.T) {
	start := time.Now()
	result := executeNative(Invocation{
		Executable: "sh",
		// The child inherits the output pipes and keeps them open until it dies.
		Args:    []string{"-c", "sleep 30 &\nprintf '%s' \"$!\"\nprintf 'before timeout' >&2\nwait"},
		Mode:    Sync,
		Timeout: time.Second,
	}, log.New(io.Discard, "", 0))
	elapsed := time.Since(start)

	stdout, stderr, _ := strings.Cut(result.Output, "\n")
	childPID, err := strconv.Atoi(stdout)
	if err != nil || childPID <= 1 {
		t.Fatalf("child did not report a valid PID before the timeout: %+v", result)
	}
	// Also clean up the child if a regression kills only the shell.
	t.Cleanup(func() { _ = syscall.Kill(childPID, syscall.SIGKILL) })
	if result.RC != -1 || result.Err == nil || stderr != "before timeout" {
		t.Fatalf("timeout result = %+v; want RC -1, an error, and partial output", result)
	}
	// If the child survives, its open pipes force cmd.Wait to use the five-second
	// WaitDelay. Prompt completion demonstrates that the group was terminated,
	// without relying on how quickly the OS reaps orphaned child processes.
	if elapsed >= 4*time.Second {
		t.Fatalf("timeout took %v; child output pipes should close before WaitDelay", elapsed)
	}
}

type executionLogMessages chan string

func (messages executionLogMessages) Write(p []byte) (int, error) {
	messages <- string(p)
	return len(p), nil
}

func TestExecuteNativeAsyncReturnsBeforeExit(t *testing.T) {
	dir := t.TempDir()
	releasePath := filepath.Join(dir, "release")
	// Release the process on failure too. The loop also stops if TempDir cleanup
	// removes the directory before the process observes the release file.
	t.Cleanup(func() { _ = os.WriteFile(releasePath, nil, 0o600) })
	messages := make(executionLogMessages, 10)
	logger := log.New(messages, "task 42 (plugin weather): ", 0)
	results := make(chan ExecutionResult, 1)
	go func() {
		results <- executeNative(Invocation{
			Executable: "sh",
			Args:       []string{"-c", "while [ -d \"$PWD\" ] && [ ! -f release ]; do sleep 0.01; done; exit 7"},
			Dir:        dir,
			Mode:       Async,
			Timeout:    -time.Second, // Even an expired timeout must be ignored.
		}, logger)
	}()

	select {
	case result := <-results:
		if result.Err != nil || result.RC != 0 || result.Output != "Task started in background" {
			t.Fatalf("async start result = %+v", result)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("async execution waited for the process to finish")
	}
	if err := os.WriteFile(releasePath, nil, 0o600); err != nil {
		t.Fatal(err)
	}

	deadline := time.After(5 * time.Second)
	for {
		select {
		case message := <-messages:
			if strings.Contains(message, "task 42 (plugin weather): background task exited with error: exit status 7") {
				return
			}
		case <-deadline:
			t.Fatal("background Wait did not report the exit error with task context")
		}
	}
}
