package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

type executionMode string

const (
	Async executionMode = "async"
	Sync  executionMode = "sync"
)

// Invocation describes a native process independently of the queued task.
type Invocation struct {
	Executable string
	Args       []string
	Dir        string
	Timeout    time.Duration // Effective sync timeout; unused for async execution.
	Mode       executionMode
}

// ExecutionResult carries the values persisted for a task. For async execution,
// success means the process started, not that it finished successfully.
type ExecutionResult struct {
	Output string
	RC     int // Process exit code or a negative task error code.
	Err    error
}

func prepareInvocation(plugin Plugin, args []string) (Invocation, error) {
	executable := plugin.EntryPoint.Executable
	if !filepath.IsAbs(executable) && strings.ContainsRune(executable, os.PathSeparator) {
		var err error
		executable, err = filepath.Abs(filepath.Join(plugin.Dir, executable))
		if err != nil {
			return Invocation{}, fmt.Errorf("error resolving plugin executable: %w", err)
		}
	}
	commandArgs := append([]string(nil), plugin.EntryPoint.Args...)
	commandArgs = append(commandArgs, args...)

	mode := executionMode(plugin.InvocationType)
	var timeout time.Duration
	if mode == Sync {
		timeout = time.Duration(plugin.InvocationTimeoutS) * time.Second
		if timeout == 0 {
			timeout = 30 * time.Second
		}
	}

	return Invocation{
		Executable: executable,
		Args:       commandArgs,
		Dir:        plugin.Dir,
		Timeout:    timeout,
		Mode:       mode,
	}, nil
}

// executeNative owns the process lifecycle. The caller supplies task context
// through the logger rather than through the invocation specification.
func executeNative(inv Invocation, logger *log.Logger) ExecutionResult {
	if inv.Mode == Sync {
		// Core shutdown stops claiming tasks but must let running tasks finish.
		ctx, cancel := context.WithTimeout(context.Background(), inv.Timeout)
		defer cancel()

		cmd := exec.CommandContext(ctx, inv.Executable, inv.Args...)
		cmd.Dir = inv.Dir
		var stdout, stderr bytes.Buffer
		cmd.Stdout = &stdout
		cmd.Stderr = &stderr

		if verbosity >= 3 {
			logger.Printf("invoking queued task: %s", cmd.String())
		}

		// Isolate the plugin from Ctrl-C aimed at Core's process group.
		cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
		// The deadline must kill the whole group, including plugin children.
		cmd.Cancel = func() error {
			return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
		}
		cmd.WaitDelay = 5 * time.Second

		runErr := cmd.Run()
		rc := 0
		if runErr != nil {
			if errors.Is(ctx.Err(), context.DeadlineExceeded) {
				logger.Printf("Command timed out after %v", inv.Timeout)
				rc = -1 // RC for timeout (for now)
			} else {
				var exitErr *exec.ExitError
				if errors.As(runErr, &exitErr) {
					rc = exitErr.ExitCode()
					logger.Printf("Command failed with RC: %d", rc)
				} else {
					rc = -2 // RC for failed to start (for now)
					logger.Printf("Command failed to execute: %v", runErr)
				}
			}
		} else {
			logger.Println("Command finished successfully")
		}

		return ExecutionResult{
			Output: stdout.String() + "\n" + stderr.String(),
			RC:     rc,
			Err:    runErr,
		}
	}

	cmd := exec.Command(inv.Executable, inv.Args...)
	cmd.Dir = inv.Dir
	// Isolate background plugins from Ctrl-C aimed at Core's process group too.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	if verbosity >= 3 {
		logger.Printf("invoking background task: %s", cmd.String())
	}

	if err := cmd.Start(); err != nil {
		logger.Printf("Command failed to start: %v", err)
		return ExecutionResult{RC: -2, Err: err}
	}
	logger.Println("Command started in the background successfully")

	go func() {
		if err := cmd.Wait(); err != nil {
			logger.Printf("background task exited with error: %v", err)
		}
	}()

	return ExecutionResult{Output: "Task started in background"}
}
