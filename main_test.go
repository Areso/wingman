package main

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/BurntSushi/toml"
)

// validPlugin returns the smallest fully valid plugin. Individual tests copy it
// and change one field, which keeps each test focused on one rule.
func validPlugin() Plugin {
	return Plugin{
		CommonConfig:       CommonConfig{ID: "weather", Enabled: true},
		Name:               "Weather",
		InvocationWith:     "python3",
		InvocationFile:     "weather.py",
		InvocationType:     "sync",
		MinAllowedRole:     "guest",
		InvocationTimeoutS: 1,
	}
}

func TestCommonConfigValidate(t *testing.T) {
	tests := []struct {
		name    string
		id      string
		wantErr string
	}{
		{name: "valid ID", id: "weather"},
		{name: "surrounding spaces are accepted", id: "  weather  "},
		{name: "empty ID", id: "   ", wantErr: "cannot be empty"},
		{name: "ID over 96 characters", id: strings.Repeat("x", 97), wantErr: "too long"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := (&CommonConfig{ID: tt.id}).Validate()
			assertErrorContains(t, err, tt.wantErr)
		})
	}
}

func TestPluginValidate(t *testing.T) {
	tests := []struct {
		name    string
		change  func(*Plugin)
		wantErr string
	}{
		{name: "valid plugin"},
		{name: "missing name", change: func(p *Plugin) { p.Name = " " }, wantErr: "field 'name'"},
		{name: "missing command", change: func(p *Plugin) { p.InvocationWith = "" }, wantErr: "field 'invocation_with'"},
		{name: "missing file", change: func(p *Plugin) { p.InvocationFile = "" }, wantErr: "field 'invocation_file'"},
		{name: "unknown invocation type", change: func(p *Plugin) { p.InvocationType = "later" }, wantErr: "only values sync or async"},
		{name: "negative sync timeout", change: func(p *Plugin) { p.InvocationTimeoutS = -1 }, wantErr: "must be positive"},
		{name: "cron without schedule", change: func(p *Plugin) { p.Cron = true }, wantErr: "cron_time"},
		{name: "invalid cron schedule", change: func(p *Plugin) { p.Cron = true; p.CronTime = "not cron" }, wantErr: "incorrect value"},
		{name: "unknown role", change: func(p *Plugin) { p.MinAllowedRole = "admin" }, wantErr: "guest or user or owner"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			plugin := validPlugin()
			if tt.change != nil {
				tt.change(&plugin)
			}
			assertErrorContains(t, plugin.Validate(), tt.wantErr)
		})
	}
}

func TestChannelLoadSecret(t *testing.T) {
	secretsDir := t.TempDir()
	t.Setenv("WINGMAN_SECRETS_DIR", secretsDir)

	t.Run("no configured secret", func(t *testing.T) {
		t.Setenv("TELEGRAM_CH_REST_TOKEN", "")
		secret, source, err := (&Channel{}).loadSecret()
		if err != nil || secret != "" || source != "not set" {
			t.Fatalf("loadSecret() = %q, %q, %v; want empty, not set, nil", secret, source, err)
		}
	})

	t.Run("environment secret", func(t *testing.T) {
		t.Setenv("TELEGRAM_CH_REST_TOKEN", "  env-token  ")
		secret, source, err := (&Channel{}).loadSecret()
		if err != nil || secret != "env-token" || source != "env_var" {
			t.Fatalf("loadSecret() = %q, %q, %v; want env-token, env_var, nil", secret, source, err)
		}
	})

	t.Run("file secret takes precedence", func(t *testing.T) {
		t.Setenv("TELEGRAM_CH_REST_TOKEN", "env-token")
		if err := os.WriteFile(filepath.Join(secretsDir, "telegram-rest"), []byte(" file-token\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		secret, source, err := (&Channel{SecretLocation: "telegram-rest"}).loadSecret()
		if err != nil || secret != "file-token" || source != "secret_location" {
			t.Fatalf("loadSecret() = %q, %q, %v; want file-token, secret_location, nil", secret, source, err)
		}
	})

	t.Run("missing file falls back to environment", func(t *testing.T) {
		t.Setenv("TELEGRAM_CH_REST_TOKEN", "env-token")
		secret, source, err := (&Channel{SecretLocation: "missing"}).loadSecret()
		if err != nil || secret != "env-token" || source != "env_var" {
			t.Fatalf("loadSecret() = %q, %q, %v; want env-token, env_var, nil", secret, source, err)
		}
	})

	t.Run("missing configured secret is an error", func(t *testing.T) {
		t.Setenv("TELEGRAM_CH_REST_TOKEN", "")
		_, _, err := (&Channel{SecretLocation: "missing"}).loadSecret()
		assertErrorContains(t, err, "file could not be read")
	})
}

func TestChannelValidate(t *testing.T) {
	t.Setenv("TELEGRAM_CH_REST_TOKEN", "")
	valid := Channel{
		CommonConfig:  CommonConfig{ID: "telegram", Enabled: true},
		Address:       "127.0.0.1",
		Port:          8090,
		Endpoint:      "send_message_to_chat_id",
		EndpointToDef: "send_message_to_default",
	}
	tests := []struct {
		name    string
		change  func(*Channel)
		wantErr string
	}{
		{name: "valid channel"},
		{name: "zero port", change: func(c *Channel) { c.Port = 0 }, wantErr: "out of bounds"},
		{name: "port above maximum", change: func(c *Channel) { c.Port = 65536 }, wantErr: "out of bounds"},
		{name: "empty address", change: func(c *Channel) { c.Address = " " }, wantErr: "field 'address'"},
		{name: "empty direct endpoint", change: func(c *Channel) { c.Endpoint = "" }, wantErr: "field 'endpoint'"},
		{name: "empty default endpoint", change: func(c *Channel) { c.EndpointToDef = "" }, wantErr: "field 'endpoint_to_default'"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			channel := valid
			if tt.change != nil {
				tt.change(&channel)
			}
			assertErrorContains(t, channel.Validate(), tt.wantErr)
		})
	}
}

func TestLoadSecretForCore(t *testing.T) {
	previous := config
	t.Cleanup(func() { config = previous })

	config.IsRESTProtected = false
	secret, source, err := loadSecretForCore()
	if err != nil || secret != "" || source != NotSet {
		t.Fatalf("unprotected loadSecretForCore() = %q, %q, %v", secret, source, err)
	}

	secretsDir := t.TempDir()
	t.Setenv("WINGMAN_SECRETS_DIR", secretsDir)
	config.IsRESTProtected = true
	config.CoreRESTSecretFilename = "core-token"
	if err := os.WriteFile(filepath.Join(secretsDir, "core-token"), []byte(" secret\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	secret, source, err = loadSecretForCore()
	if err != nil || secret != "secret" || source != FromFile {
		t.Fatalf("protected loadSecretForCore() = %q, %q, %v", secret, source, err)
	}
}

func TestLoadConfigs(t *testing.T) {
	dir := t.TempDir()
	writePluginJSON(t, dir, "enabled", validPlugin())

	disabled := validPlugin()
	disabled.ID = "disabled"
	disabled.Enabled = false
	writePluginJSON(t, dir, "disabled", disabled)

	duplicate := validPlugin()
	writePluginJSON(t, dir, "duplicate", duplicate)

	malformedDir := filepath.Join(dir, "malformed")
	if err := os.Mkdir(malformedDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(malformedDir, "plugin.json"), []byte("{"), 0o600); err != nil {
		t.Fatal(err)
	}

	plugins, err := loadConfigs[Plugin](dir, "plugin*.json")
	if err != nil {
		t.Fatal(err)
	}
	if len(plugins) != 1 {
		t.Fatalf("loaded %d plugins; want only the one enabled, valid, unique plugin", len(plugins))
	}
	if plugins[0].ID != "weather" || plugins[0].Dir == "" {
		t.Fatalf("loaded plugin = %+v; want weather with its directory recorded", plugins[0])
	}
}

func TestCronHelpers(t *testing.T) {
	if !isCroned(Plugin{Cron: true}) || isCroned(Plugin{}) {
		t.Fatal("isCroned should return the plugin's Cron flag")
	}

	now := time.Date(2026, time.August, 28, 12, 30, 0, 0, time.UTC)
	if !matchesCron("30 12 * * *", now) {
		t.Fatal("expected schedule to match 12:30")
	}
	if matchesCron("31 12 * * *", now) {
		t.Fatal("did not expect 12:31 schedule to match 12:30")
	}
	if matchesCron("invalid", now) {
		t.Fatal("invalid schedules must not match")
	}
}

func TestShellQuote(t *testing.T) {
	tests := map[string]string{
		"":             "''",
		"hello world":  "'hello world'",
		"it's raining": "'it'\\''s raining'",
	}
	for input, want := range tests {
		if got := shellQuote(input); got != want {
			t.Errorf("shellQuote(%q) = %q; want %q", input, got, want)
		}
	}
}

func TestDatabaseTaskLifecycle(t *testing.T) {
	db := openTestCoreDB(t)
	plugin := validPlugin()

	id, err := create_task(db, plugin, "telegram", "123", map[string]string{"option": "today"})
	if err != nil {
		t.Fatal(err)
	}
	if id < 1 {
		t.Fatalf("task ID = %d; want a positive generated ID", id)
	}

	var pluginID, invokedWith, params string
	if err := db.QueryRow("SELECT plugin_id, invoked_with, params FROM tasks_queued WHERE id = ?", id).Scan(&pluginID, &invokedWith, &params); err != nil {
		t.Fatal(err)
	}
	if pluginID != "weather" || invokedWith != "telegram" || params != `{"option":"today"}` {
		t.Fatalf("stored task = %q, %q, %q", pluginID, invokedWith, params)
	}

	clear, err := checkClearToPlanTask(db, plugin.ID)
	if err != nil || clear {
		t.Fatalf("checkClearToPlanTask() = %t, %v; want false while queued", clear, err)
	}
	if _, err := db.Exec("UPDATE tasks_queued SET invoked_at = 1 WHERE id = ?", id); err != nil {
		t.Fatal(err)
	}
	clear, err = checkClearToPlanTask(db, plugin.ID)
	if err != nil || !clear {
		t.Fatalf("checkClearToPlanTask() = %t, %v; want true after invocation", clear, err)
	}
}

func TestRotateData(t *testing.T) {
	db := openTestCoreDB(t)
	if _, err := db.Exec("INSERT INTO tasks_queued (created_at, plugin_id) VALUES (?, ?), (?, ?)", time.Now().Add(-48*time.Hour).Unix(), "old", time.Now().Unix(), "new"); err != nil {
		t.Fatal(err)
	}

	previous := config
	t.Cleanup(func() { config = previous })
	config.TasksRetention = true
	config.TasksRetentionDays = 1
	if err := rotateData(db); err != nil {
		t.Fatal(err)
	}

	var count int
	if err := db.QueryRow("SELECT count(*) FROM tasks_queued").Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 1 {
		t.Fatalf("remaining rows = %d; want only the recent row", count)
	}
}

func TestExecutePluginTask(t *testing.T) {
	dir := t.TempDir()
	plugin := validPlugin()
	plugin.Dir = dir
	plugin.InvocationWith = "sh"
	plugin.InvocationFile = "plugin.sh"
	if err := os.WriteFile(filepath.Join(dir, "plugin.sh"), []byte("printf 'option=%s' \"$1\""), 0o700); err != nil {
		t.Fatal(err)
	}
	plugins := map[string]Plugin{plugin.ID: plugin}

	result, rc, err := executePluginTask(plugins, plugin.ID, sql.NullString{Valid: true, String: `{"option":"two words"}`}, 1)
	if err != nil || rc != 0 || !strings.Contains(result, "option=two words") {
		t.Fatalf("executePluginTask() = %q, %d, %v", result, rc, err)
	}

	if err := os.WriteFile(filepath.Join(dir, "plugin.sh"), []byte("printf '%s|%s|%s' \"$1\" \"$2\" \"$3\""), 0o700); err != nil {
		t.Fatal(err)
	}
	result, rc, err = executePluginTask(plugins, plugin.ID, sql.NullString{Valid: true, String: `["two words","-o","output; touch unsafe"]`}, 2)
	if err != nil || rc != 0 || !strings.Contains(result, "two words|-o|output; touch unsafe") {
		t.Fatalf("executePluginTask() with argument list = %q, %d, %v", result, rc, err)
	}
	if _, err := os.Stat(filepath.Join(dir, "unsafe")); !os.IsNotExist(err) {
		t.Fatal("shell metacharacters in arguments must not be executed")
	}

	_, rc, err = executePluginTask(plugins, "missing", sql.NullString{}, 3)
	if err == nil || rc != -3 {
		t.Fatalf("missing plugin rc/error = %d, %v; want -3 and an error", rc, err)
	}

	_, rc, err = executePluginTask(plugins, plugin.ID, sql.NullString{Valid: true, String: "{"}, 4)
	if err == nil || rc != -4 {
		t.Fatalf("invalid params rc/error = %d, %v; want -4 and an error", rc, err)
	}
}

func TestSendResult(t *testing.T) {
	var gotPath, gotAuth string
	var gotBody map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotAuth = r.Header.Get("Authorization")
		if err := json.NewDecoder(r.Body).Decode(&gotBody); err != nil {
			t.Errorf("decode request: %v", err)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	channel := channelForServer(t, server.URL)
	channel.Endpoint = "direct"
	channel.EndpointToDef = "default"
	channel.Secret = "channel-token"
	recipient := int64(123)
	if got := sendResult(&channel, &recipient, "hello", 1); got != 0 {
		t.Fatalf("sendResult() = %d; want 0", got)
	}
	if gotPath != "/direct" || gotAuth != "Bearer channel-token" || gotBody["message"] != "hello" || gotBody["chat_id"] != float64(123) {
		t.Fatalf("direct request path/auth/body = %q, %q, %#v", gotPath, gotAuth, gotBody)
	}

	gotBody = nil
	if got := sendResult(&channel, nil, "scheduled", 2); got != 0 {
		t.Fatalf("default sendResult() = %d; want 0", got)
	}
	if gotPath != "/default" || gotBody["chat_id"] != nil {
		t.Fatalf("default request path/body = %q, %#v", gotPath, gotBody)
	}
}

func TestReadWingmanSettings(t *testing.T) {
	db := openTestCoreDB(t)
	if _, err := db.Exec("INSERT INTO wingman_settings (s_key, s_value) VALUES ('send_empty_results', 'true'), ('default_channel', 'telegram')"); err != nil {
		t.Fatal(err)
	}
	previous := wingman_settings
	t.Cleanup(func() { wingman_settings = previous })

	read_wingman_settings(db)
	if !wingman_settings.SendEmptyResults || wingman_settings.DefaultChannel != "telegram" {
		t.Fatalf("settings = %+v", wingman_settings)
	}
}

func TestValidateAppConfig(t *testing.T) {
	valid := strings.Join([]string{
		"core_host = '127.0.0.1'",
		"core_port = 8091",
		"verbose_level = 2",
		"is_core_rest_protected = false",
		"retries_threshold = 3",
		"tasks_retention = false",
		"concurrent_tasks_limit = 2",
	}, "\n") + "\n"
	tests := []struct {
		name    string
		input   string
		wantErr string
	}{
		{name: "valid", input: valid},
		{name: "missing host", input: strings.Replace(valid, "core_host = '127.0.0.1'\n", "", 1), wantErr: "core_host' is missing"},
		{name: "invalid port", input: strings.Replace(valid, "core_port = 8091", "core_port = 80", 1), wantErr: "between 1024 and 9900"},
		{name: "invalid verbosity", input: strings.Replace(valid, "verbose_level = 2", "verbose_level = 4", 1), wantErr: "between 1 and 3"},
		{name: "invalid retries", input: strings.Replace(valid, "retries_threshold = 3", "retries_threshold = 0", 1), wantErr: "between 1 and 20"},
		{name: "invalid concurrency", input: strings.Replace(valid, "concurrent_tasks_limit = 2", "concurrent_tasks_limit = 21", 1), wantErr: "between 1 and 20"},
		{name: "protected API needs filename", input: strings.Replace(valid, "is_core_rest_protected = false", "is_core_rest_protected = true", 1), wantErr: "secret_filename' is missing"},
		{name: "retention needs days", input: strings.Replace(valid, "tasks_retention = false", "tasks_retention = true", 1), wantErr: "retention_days' is missing"},
	}

	previous := config
	t.Cleanup(func() { config = previous })
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			config = AppConfig{}
			meta, err := toml.Decode(tt.input, &config)
			if err != nil {
				t.Fatal(err)
			}
			assertErrorContains(t, validateAppconfig(meta), tt.wantErr)
		})
	}
}

func assertErrorContains(t *testing.T, err error, want string) {
	t.Helper()
	if want == "" {
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		return
	}
	if err == nil || !strings.Contains(err.Error(), want) {
		t.Fatalf("error = %v; want error containing %q", err, want)
	}
}

func writePluginJSON(t *testing.T, root, directory string, plugin Plugin) {
	t.Helper()
	dir := filepath.Join(root, directory)
	if err := os.Mkdir(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(plugin)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "plugin.json"), data, 0o600); err != nil {
		t.Fatal(err)
	}
}

func openTestCoreDB(t *testing.T) *sql.DB {
	t.Helper()
	previous := config
	config.TasksRetention = false
	t.Cleanup(func() { config = previous })
	db, err := initDB(filepath.Join(t.TempDir(), "wingman.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

func channelForServer(t *testing.T, serverURL string) Channel {
	t.Helper()
	parsed, err := url.Parse(serverURL)
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(parsed.Port())
	if err != nil {
		t.Fatal(err)
	}
	return Channel{Address: parsed.Hostname(), Port: port}
}
