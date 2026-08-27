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
	"unicode/utf8"

	"github.com/BurntSushi/toml"
	tgbotapi "github.com/go-telegram-bot-api/telegram-bot-api/v5"
)

func validTelegramPlugin() Plugin {
	return Plugin{
		ID:                 "weather",
		Name:               "Weather",
		Enabled:            true,
		InvocationWith:     "python3",
		InvocationFile:     "weather.py",
		InvocationType:     "sync",
		InvocationTimeoutS: 1,
		Adhoc:              true,
		MinAllowedRole:     "guest",
	}
}

func TestPluginAllowed(t *testing.T) {
	tests := []struct {
		name    string
		role    string
		minimum string
		want    bool
	}{
		{name: "guest can run guest plugin", role: "guest", minimum: "guest", want: true},
		{name: "guest cannot run user plugin", role: "guest", minimum: "user"},
		{name: "user can run guest plugin", role: "user", minimum: "guest", want: true},
		{name: "user can run user plugin", role: "user", minimum: "user", want: true},
		{name: "user cannot run owner plugin", role: "user", minimum: "owner"},
		{name: "owner can run owner plugin", role: "owner", minimum: "owner", want: true},
		{name: "unknown user role is denied", role: "admin", minimum: "guest"},
		{name: "unknown minimum role is denied", role: "owner", minimum: "admin"},
		{name: "empty minimum role is denied", role: "owner", minimum: ""},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := pluginAllowed(tt.role, &Plugin{MinAllowedRole: tt.minimum}); got != tt.want {
				t.Fatalf("pluginAllowed(%q, minimum %q) = %t; want %t", tt.role, tt.minimum, got, tt.want)
			}
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
		{name: "empty ID", change: func(p *Plugin) { p.ID = " " }, wantErr: "cannot be empty"},
		{name: "long ID", change: func(p *Plugin) { p.ID = strings.Repeat("x", 97) }, wantErr: "too long"},
		{name: "empty name", change: func(p *Plugin) { p.Name = "" }, wantErr: "field 'name'"},
		{name: "invalid invocation type", change: func(p *Plugin) { p.InvocationType = "unknown" }, wantErr: "only values sync or async"},
		{name: "negative timeout", change: func(p *Plugin) { p.InvocationTimeoutS = -1 }, wantErr: "must be positive"},
		{name: "missing cron expression", change: func(p *Plugin) { p.Cron = true }, wantErr: "cron_time"},
		{name: "invalid role", change: func(p *Plugin) { p.MinAllowedRole = "admin" }, wantErr: "guest or user or owner"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			plugin := validTelegramPlugin()
			if tt.change != nil {
				tt.change(&plugin)
			}
			assertTelegramErrorContains(t, plugin.Validate(), tt.wantErr)
		})
	}
}

func TestTelegramDatabaseRoles(t *testing.T) {
	db := openTestTelegramDB(t)
	if role := getRole(db, 999); role != "guest" {
		t.Fatalf("unknown chat role = %q; want guest", role)
	}
	if _, err := db.Exec("INSERT INTO known_ids (chat_id, role, is_default) VALUES (10, 'user', 0), (20, 'owner', 1)"); err != nil {
		t.Fatal(err)
	}
	if role := getRole(db, 10); role != "user" {
		t.Fatalf("known chat role = %q; want user", role)
	}
	chatID, err := getDefaultChatID(db)
	if err != nil || chatID != 20 {
		t.Fatalf("getDefaultChatID() = %d, %v; want 20, nil", chatID, err)
	}
}

func TestGetDefaultChatIDWithoutDefault(t *testing.T) {
	db := openTestTelegramDB(t)
	_, err := getDefaultChatID(db)
	if err != sql.ErrNoRows {
		t.Fatalf("getDefaultChatID() error = %v; want sql.ErrNoRows", err)
	}
}

func TestGetSecretLocation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "channel.json")
	if err := os.WriteFile(path, []byte(`{"secret_location":"channels/telegram-rest"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	location, err := getSecretLocation(path)
	if err != nil || location != "channels/telegram-rest" {
		t.Fatalf("getSecretLocation() = %q, %v", location, err)
	}
	_, err = getSecretLocation(filepath.Join(t.TempDir(), "missing.json"))
	assertTelegramErrorContains(t, err, "failed to open file")
}

func TestLoadPlugins(t *testing.T) {
	root := t.TempDir()
	writeTelegramPluginJSON(t, root, "enabled", validTelegramPlugin())

	nonAdhoc := validTelegramPlugin()
	nonAdhoc.ID = "cron-only"
	nonAdhoc.Adhoc = false
	writeTelegramPluginJSON(t, root, "non-adhoc", nonAdhoc)

	disabled := validTelegramPlugin()
	disabled.ID = "disabled"
	disabled.Enabled = false
	writeTelegramPluginJSON(t, root, "disabled", disabled)

	writeTelegramPluginJSON(t, root, "duplicate", validTelegramPlugin())

	bot := &Bot{plugins: make(map[string]*Plugin)}
	if err := bot.loadPlugins(root); err != nil {
		t.Fatal(err)
	}
	if len(bot.plugins) != 1 || bot.plugins["weather"] == nil {
		t.Fatalf("loaded plugins = %#v; want only weather", bot.plugins)
	}
	if bot.plugins["weather"].Dir == "" {
		t.Fatal("loaded plugin should retain its source directory")
	}
}

func TestValidateAppConfig(t *testing.T) {
	tests := []struct {
		name    string
		toml    string
		wantErr string
	}{
		{name: "valid", toml: "comm_telegram_host = '127.0.0.1'\ncomm_telegram_port = 8090\n"},
		{name: "missing host", toml: "comm_telegram_port = 8090\n", wantErr: "host' is missing"},
		{name: "empty host", toml: "comm_telegram_host = '  '\ncomm_telegram_port = 8090\n", wantErr: "host' cannot be empty"},
		{name: "missing port", toml: "comm_telegram_host = '127.0.0.1'\n", wantErr: "port' is missing"},
		{name: "port too low", toml: "comm_telegram_host = '127.0.0.1'\ncomm_telegram_port = 80\n", wantErr: "between 1024 and 9900"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var cfg ChannelConfig
			meta, err := toml.Decode(tt.toml, &cfg)
			if err != nil {
				t.Fatal(err)
			}
			assertTelegramErrorContains(t, validateAppconfig(cfg, meta), tt.wantErr)
		})
	}
}

func TestRESTSecretHelpers(t *testing.T) {
	secretsDir := t.TempDir()
	t.Setenv("WINGMAN_SECRETS_DIR", secretsDir)

	path, err := buildSecretPath("channels/rest-token")
	if err != nil || path != filepath.Join(secretsDir, "channels/rest-token") {
		t.Fatalf("buildSecretPath() = %q, %v", path, err)
	}

	t.Setenv("TELEGRAM_CH_REST_TOKEN", " env-token ")
	secret, err := readRESTSecret("")
	if err != nil || secret != "env-token" {
		t.Fatalf("readRESTSecret(environment) = %q, %v", secret, err)
	}

	if err := os.Mkdir(filepath.Join(secretsDir, "channels"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(secretsDir, "channels/rest-token"), []byte(" file-token\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	secret, err = readRESTSecret("channels/rest-token")
	if err != nil || secret != "file-token" {
		t.Fatalf("readRESTSecret(file) = %q, %v", secret, err)
	}
}

func TestLoadSecretForCore(t *testing.T) {
	previous := core_config
	t.Cleanup(func() { core_config = previous })

	core_config.IsRESTProtected = false
	secret, source, err := loadSecretForCore()
	if err != nil || secret != "" || source != NotSet {
		t.Fatalf("unprotected loadSecretForCore() = %q, %q, %v", secret, source, err)
	}

	secretsDir := t.TempDir()
	t.Setenv("WINGMAN_SECRETS_DIR", secretsDir)
	core_config.IsRESTProtected = true
	core_config.CoreRESTSecretFilename = "core-token"
	if err := os.WriteFile(filepath.Join(secretsDir, "core-token"), []byte(" token\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	secret, source, err = loadSecretForCore()
	if err != nil || secret != "token" || source != FromFile {
		t.Fatalf("protected loadSecretForCore() = %q, %q, %v", secret, source, err)
	}
}

func TestHandleSendMessage(t *testing.T) {
	telegram, requests := fakeTelegramServer(t)
	db := openTestTelegramDB(t)
	if _, err := db.Exec("INSERT INTO known_ids (chat_id, role, is_default) VALUES (20, 'owner', 1)"); err != nil {
		t.Fatal(err)
	}
	secret := "rest-token"
	bot := &Bot{api: telegram, db: db, rest_secret: &secret}

	t.Run("rejects wrong method", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/send_message_to_chat_id", nil)
		res := httptest.NewRecorder()
		bot.handleSendMessage(res, req)
		if res.Code != http.StatusMethodNotAllowed {
			t.Fatalf("status = %d; want 405", res.Code)
		}
	})

	t.Run("rejects missing authorization", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodPost, "/send_message_to_chat_id", strings.NewReader(`{"chat_id":10,"message":"hello"}`))
		res := httptest.NewRecorder()
		bot.handleSendMessage(res, req)
		if res.Code != http.StatusUnauthorized {
			t.Fatalf("status = %d; want 401", res.Code)
		}
	})

	t.Run("sends direct message", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodPost, "/send_message_to_chat_id", strings.NewReader(`{"chat_id":10,"message":"hello"}`))
		req.Header.Set("Authorization", "Bearer rest-token")
		res := httptest.NewRecorder()
		bot.handleSendMessage(res, req)
		if res.Code != http.StatusOK {
			t.Fatalf("status/body = %d, %q", res.Code, res.Body.String())
		}
		last := (*requests)[len(*requests)-1]
		if last.Get("chat_id") != "10" || last.Get("text") != "hello" {
			t.Fatalf("Telegram form = %v", last)
		}
	})

	t.Run("resolves default chat and normalizes empty output", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodPost, "/send_message_to_default", strings.NewReader(`{"message":"  "}`))
		req.Header.Set("Authorization", "Bearer rest-token")
		res := httptest.NewRecorder()
		bot.handleSendMessage(res, req)
		if res.Code != http.StatusOK {
			t.Fatalf("status/body = %d, %q", res.Code, res.Body.String())
		}
		last := (*requests)[len(*requests)-1]
		if last.Get("chat_id") != "20" || last.Get("text") != "(plugin produced no output)" {
			t.Fatalf("Telegram form = %v", last)
		}
	})

	t.Run("rejects invalid JSON", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodPost, "/send_message_to_chat_id", strings.NewReader("{"))
		req.Header.Set("Authorization", "Bearer rest-token")
		res := httptest.NewRecorder()
		bot.handleSendMessage(res, req)
		if res.Code != http.StatusBadRequest {
			t.Fatalf("status = %d; want 400", res.Code)
		}
	})

	t.Run("truncates oversized output on a UTF-8 boundary", func(t *testing.T) {
		longMessage := strings.Repeat("a", 3999) + "é" + strings.Repeat("b", 10)
		body, err := json.Marshal(SendMsgRequest{ChatID: 10, Message: longMessage})
		if err != nil {
			t.Fatal(err)
		}
		req := httptest.NewRequest(http.MethodPost, "/send_message_to_chat_id", strings.NewReader(string(body)))
		req.Header.Set("Authorization", "Bearer rest-token")
		res := httptest.NewRecorder()
		bot.handleSendMessage(res, req)
		if res.Code != http.StatusOK {
			t.Fatalf("status/body = %d, %q", res.Code, res.Body.String())
		}
		text := (*requests)[len(*requests)-1].Get("text")
		if !strings.HasSuffix(text, "...[truncated due to size, limit is 4000 bytes]") || !utf8.ValidString(text) {
			t.Fatalf("truncated text has unexpected boundary or suffix: %q", text[len(text)-80:])
		}
	})
}

func TestInvokePlugin(t *testing.T) {
	var gotAuth string
	var gotRequest QueueTaskRequest
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		if err := json.NewDecoder(r.Body).Decode(&gotRequest); err != nil {
			t.Errorf("decode request: %v", err)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	previousConfig, previousSecret := core_config, core_rest_secret
	t.Cleanup(func() { core_config, core_rest_secret = previousConfig, previousSecret })
	parsed, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(parsed.Port())
	if err != nil {
		t.Fatal(err)
	}
	core_config = CoreConfig{Host: parsed.Hostname(), Port: port}
	core_rest_secret = "core-token"

	bot := &Bot{}
	req := PluginInvocationRequest{ID: "weather", Params: map[string]string{"option": "today"}}
	if err := bot.invokePlugin("weather", req, "telegram", "123"); err != nil {
		t.Fatal(err)
	}
	if gotAuth != "Bearer core-token" || gotRequest.PluginID != "weather" || gotRequest.InvWith != "telegram" || gotRequest.InvBy != "123" || gotRequest.Params["option"] != "today" {
		t.Fatalf("Core auth/request = %q, %+v", gotAuth, gotRequest)
	}
}

func assertTelegramErrorContains(t *testing.T, err error, want string) {
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

func openTestTelegramDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := initTelegramDB(filepath.Join(t.TempDir(), "telegram.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

func writeTelegramPluginJSON(t *testing.T, root, directory string, plugin Plugin) {
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

func fakeTelegramServer(t *testing.T) (*tgbotapi.BotAPI, *[]url.Values) {
	t.Helper()
	requests := make([]url.Values, 0)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := r.ParseForm(); err != nil {
			t.Errorf("parse Telegram form: %v", err)
		}
		requests = append(requests, r.Form)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true,"result":{"message_id":1,"date":0,"chat":{"id":1,"type":"private"},"text":"sent"}}`))
	}))
	t.Cleanup(server.Close)

	api := &tgbotapi.BotAPI{Token: "test-token", Client: server.Client()}
	api.SetAPIEndpoint(server.URL + "/bot%s/%s")
	return api, &requests
}
