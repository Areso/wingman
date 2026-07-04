package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"time"

	"github.com/BurntSushi/toml"
	_ "github.com/mattn/go-sqlite3"
	"github.com/robfig/cron/v3"
)

type Config interface {
	GetCommon() *CommonConfig
	Validate() error
}
type CommonConfig struct {
	ID      string `json:"id"`
	Enabled bool   `json:"enabled"`
	Dir     string `json:"-"`
}

func (c *CommonConfig) GetCommon() *CommonConfig { return c }

// Validate checks basic CommonConfig rules.
func (c *CommonConfig) Validate() error {
	if len(strings.TrimSpace(c.ID)) > 96 {
		return fmt.Errorf("id is too long (max 96 symbols, got %d)", len(strings.TrimSpace(c.ID)))
	}
	return nil
}

type Plugin struct {
	CommonConfig
	Name               string `json:"name"`
	InvocationWith     string `json:"invocation_with"`
	InvocationFile     string `json:"invocation_file"`
	InvocationTimeoutS int32  `json:"invocation_timeout_s"`
	Adhoc              bool   `json:"adhoc"`
	Cron               bool   `json:"cron"`
	CronTime           string `json:"cron_time"`
}

func (p *Plugin) Validate() error {
	// 1. Validate embedded common rules
	if err := p.CommonConfig.Validate(); err != nil {
		return err
	}
	// 2. Validate all structural fields are filled
	return validateFields(reflect.ValueOf(p).Elem())
}

type Channel struct {
	CommonConfig
	ID            string `json:"id"`
	Address       string `json:"address"`
	Port          int    `json:"port"`
	Endpoint      string `json:"endpoint"`
	EndpointToDef string `json:"endpoint_to_default"`
}

func (c *Channel) Validate() error {
	// 1. Validate embedded common rules
	if err := c.CommonConfig.Validate(); err != nil {
		return err
	}
	// 2. Validate custom port rules
	if c.Port < 1 || c.Port > 65000 {
		return fmt.Errorf("port %d out of bounds (must be 1-65000)", c.Port)
	}
	// 3. Validate all structural fields are filled
	return validateFields(reflect.ValueOf(c).Elem())
}

// validateFields accurately checks top-level non-boolean fields for zero values
func validateFields(v reflect.Value) error {
	for i := 0; i < v.NumField(); i++ {
		field := v.Field(i)
		fieldType := v.Type().Field(i)
		// Skip unexported fields or the explicitly ignored "Dir" field
		if !field.CanInterface() || fieldType.Name == "Dir" {
			continue
		}
		// Skip the embedded struct itself to prevent reflection access panics;
		// its fields are validated independently via c.CommonConfig.Validate()
		if fieldType.Anonymous {
			continue
		}
		// Allow booleans to be false natively
		if field.Kind() == reflect.Bool {
			continue
		}
		// Handle pointers securely if your configuration leverages them
		if field.Kind() == reflect.Ptr {
			if field.IsNil() {
				return fmt.Errorf("missing required field: %s", fieldType.Name)
			}
			field = field.Elem()
		}
		// Enforce that all other fields must contain data
		if reflect.DeepEqual(field.Interface(), reflect.Zero(field.Type()).Interface()) {
			return fmt.Errorf("field '%s' cannot be empty or zero", fieldType.Name)
		}
	}
	return nil
}

func loadConfigs[T any, PT interface {
	*T
	Config
}](dir, filename string) ([]T, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var result []T
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		path := filepath.Join(dir, entry.Name(), filename)
		data, err := os.ReadFile(path)
		if err != nil {
			log.Printf("skipping %s: %v", entry.Name(), err)
			continue
		}
		var item T
		if err := json.Unmarshal(data, &item); err != nil {
			log.Printf("skipping %s: %v", entry.Name(), err)
			continue
		}
		common := PT(&item).GetCommon()
		if !common.Enabled {
			continue
		}
		common.Dir = filepath.Join(dir, entry.Name())
		// Execute validation before appending to the results array
		if err := PT(&item).Validate(); err != nil {
			log.Printf("skipping invalid config %s: %v", entry.Name(), err)
			continue
		}
		result = append(result, item)
	}
	return result, nil
}

func isCroned(p Plugin) bool {
	return p.Cron == true
}

func matchesCron(expr string, t time.Time) bool {
	sched, err := cron.ParseStandard(expr)
	if err != nil {
		log.Printf("invalid cron expression %q: %v", expr, err)
		return false
	}
	prev := t.Add(-time.Minute)
	next := sched.Next(prev)
	return !next.After(t)
}

func initDB(path string) (*sql.DB, error) {
	db, err := sql.Open("sqlite3", path)
	if err != nil {
		return nil, err
	}
	// FORCE Go to actually open the sqlite file right now
	if err := db.Ping(); err != nil {
		return nil, fmt.Errorf("database ping failed: %w", err)
	}

	// invoked_with cron, telegram, email
	// cron N/A,chat_id, email
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS tasks_queued (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			created_at INTEGER NOT NULL,
			invoked_at INTEGER,
			invoked_with TEXT,
			invoked_by_id TEXT,
			plugin_id TEXT NOT NULL,
			params TEXT,
			finished_at INTEGER,
			result TEXT,
			rc INTEGER,
			result_sent_at INTEGER
		)
	`)
	if err != nil {
		return nil, err
	}

	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS wingman_settings (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			s_key TEXT NOT NULL UNIQUE,
			s_value TEXT NOT NULL
		);
	`)
	if err != nil {
		return nil, err
	}
	return db, nil
}

func create_task(db *sql.DB, p Plugin, inv_with string, inv_id string, params map[string]string) (int64, error) {
	now := time.Now().UTC().Unix()
	if params == nil {
		params = make(map[string]string)
	}
	paramsJSON, err := json.Marshal(params)
	if err != nil {
		return 0, fmt.Errorf("error marshaling parmas for %s: %w", p.ID, err)
	}
	res, err := db.Exec(
		"INSERT INTO tasks_queued (created_at, plugin_id, invoked_with, invoked_by_id, params) VALUES (?, ?, ?, ?, ?)",
		now, p.ID, inv_with, inv_id, string(paramsJSON),
	)
	if err != nil {
		log.Printf("error inserting task for %s: %v", p.ID, err)
		return 0, err
	}
	id, err := res.LastInsertId()
	if err != nil {
		log.Printf("error getting last insert ID for %s: %v", p.ID, err)
		return 0, err
	}
	return id, nil
}

func shellQuote(value string) string {
	if value == "" {
		return "''"
	}
	return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'"
}

func processQueuedTasks(db *sql.DB, plugins map[string]Plugin) {
	for range time.NewTicker(time.Second).C {
		// Find queued tasks that haven't been invoked yet
		var id int64
		var pluginID string
		var paramsRaw sql.NullString
		err := db.QueryRow("SELECT id, plugin_id, params FROM tasks_queued WHERE invoked_at IS NULL LIMIT 1").Scan(&id, &pluginID, &paramsRaw)
		if err != nil {
			if err != sql.ErrNoRows {
				log.Printf("error querying queued tasks: %v", err)
			}
			continue
		}
		// Get plugin
		p, ok := plugins[pluginID]
		if !ok {
			log.Printf("plugin %s not found for queued task %d", pluginID, id)
			continue
		}
		// Mark as invoked
		now := time.Now().UTC().Unix()
		_, err = db.Exec("UPDATE tasks_queued SET invoked_at = ? WHERE id = ?", now, id)
		if err != nil {
			log.Printf("error updating invoked_at for task %d: %v", id, err)
			continue
		}
		// Execute plugin
		params := make(map[string]string)
		if paramsRaw.Valid && paramsRaw.String != "" {
			if err := json.Unmarshal([]byte(paramsRaw.String), &params); err != nil {
				log.Printf("error unmarshalling params for task %d: %v", id, err)
			}
		}
		fullCommand := fmt.Sprintf("%s %s", p.InvocationWith, p.InvocationFile)
		if option := params["option"]; option != "" {
			fullCommand = fmt.Sprintf("%s %s", fullCommand, shellQuote(option))
		}
		timeout := time.Duration(p.InvocationTimeoutS) * time.Second
		log.Printf("timeout from the plugin.json is %v", timeout)
		if timeout == 0 {
			// 0 if plugin.json doesn't have invocation_timeout_s property
			timeout = 30 * time.Second
		}
		ctx, cancel := context.WithTimeout(context.Background(), timeout)
		cmd := exec.CommandContext(ctx, "bash", "-c", fullCommand)
		cmd.Dir = p.Dir
		var stdout, stderr bytes.Buffer
		cmd.Stdout = &stdout
		cmd.Stderr = &stderr
		log.Printf("invoking queued task %d (plugin %s): %s", id, p.ID, fullCommand)
		runErr := cmd.Run()
		rc := 0
		if runErr != nil {
			// Check if the error was caused by a timeout
			if errors.Is(ctx.Err(), context.DeadlineExceeded) {
				log.Printf("Command timed out after %v", timeout)
				rc = -1 // RC for timeout (for now)
			} else {
				var exitErr *exec.ExitError
				if errors.As(runErr, &exitErr) {
					// The command finished with a non-zero exit code
					rc = exitErr.ExitCode()
					log.Printf("Command failed with RC: %d", rc)
				} else {
					// The command failed to start, or another issue occurred
					rc = -2 // RC for failed to start (for now)
					log.Printf("Command failed to execute: %v", runErr)
				}
			}
		} else {
			log.Println("Command finished successfully")
		}
		finishTime := time.Now().UTC().Unix()
		result := stdout.String() + "\n" + stderr.String()
		query := `
		UPDATE tasks_queued 
		SET finished_at = ?, 
			result = ?,
			rc     = ? 
		WHERE id = ?`
		_, err = db.Exec(query, finishTime, result, rc, id)
		if err != nil {
			log.Printf("error updating finished_at for task %d: %v", id, err)
		}
		if runErr != nil {
			log.Printf("error running queued task %d (plugin %s): %v", id, p.ID, runErr)
		}
		cancel() // Always call cancel to release resources!
	}
}
func markTaskAsSended(db *sql.DB, taskID int64) {
	now := time.Now().UTC().Unix()
	// otherwise we would get this exactly task indefinetly
	_, err := db.Exec("UPDATE tasks_queued SET result_sent_at = ? WHERE id = ?", now, taskID)
	if err != nil {
		log.Printf("error updating result_sent_at for task %d: %v", taskID, err)
	} else {
		// otherwise we would get this exactly task indefinetly by the SELECT in the start of this function
		log.Printf("updated result_sent_at successfully %d", taskID)
	}
}

func processFinishedTasks(db *sql.DB, channels map[string]Channel) {
	for range time.NewTicker(time.Second * 5).C {
		var id int64
		var invokedWith string
		var invokedByID string
		var result string
		var rc int32

		var str_send_empty_results string
		var bl_send_empty_results bool

		err := db.QueryRow(`
			SELECT id, invoked_with, invoked_by_id, result, rc 
			FROM   tasks_queued 
			WHERE  finished_at IS NOT NULL
			AND    result_sent_at IS NULL 
			LIMIT  1
		`).Scan(&id, &invokedWith, &invokedByID, &result, &rc)
		if err != nil {
			if err == sql.ErrNoRows {
				// No tasks ready to process right now; safely skip
				continue
			}
			log.Printf("Some error from Quering inside processFinishedTasks: %v", err)
			continue
		}

		err1 := db.QueryRow(`
			SELECT s_value 
			FROM   wingman_settings 
			WHERE  s_key = 'send_empty_results';
		`).Scan(&str_send_empty_results)
		log.Printf("send_empty_results value is %s", str_send_empty_results)
		if err1 != nil {
			bl_send_empty_results = false
		} else {
			bl_send_empty_results_t, err2 := strconv.ParseBool(str_send_empty_results)
			bl_send_empty_results = bl_send_empty_results_t
			if err2 != nil {
				// Handle the error if the string isn't a valid boolean representation
				fmt.Println("Error parsing string:", err2)
				bl_send_empty_results = false
			}
		}

		if bl_send_empty_results == false &&
			len(strings.TrimSpace(result)) == 0 &&
			rc == 0 &&
			invokedWith == "cron" {
			// it means the result is empty after removing whitespace
			// rc 0 - finished correctly
			// and was invoked by Cron
			markTaskAsSended(db, id)
			continue
		}
		channel_to_use_obj, ok := channels[invokedWith]
		channel_to_use := ""
		useDefaultRecipient := false
		if !ok {
			log.Printf("we couldn't find the channel_to_use which was written down as invokedWith, %s", invokedWith)
			var default_channel string
			err := db.QueryRow(`
				SELECT s_value FROM wingman_settings 
				WHERE  s_key='default_channel';
			`).Scan(&default_channel)
			if err != nil {
				if err == sql.ErrNoRows {
					log.Printf("There is no record in t wingman_settings for s_key is default_channel: %v", err)
				} else {
					log.Printf("Can't select default_channel from t wingman_settings: %v", err)
				}
				channel_to_use = "devnull"
			} else {
				channel_to_use = default_channel
				useDefaultRecipient = true
			}
		} else {
			log.Printf("channel_to_use_obj was found by the invokedWith, %s", invokedWith)
			channel_to_use = channel_to_use_obj.ID
		}
		if channel_to_use != "devnull" {
			log.Printf("channel_to_use is %s", channel_to_use)
			//TODO FIX THAT _ , it would be needed when ID can become not Int but Str (email, discord etc)
			invokedByID_int, _ := strconv.ParseInt(invokedByID, 10, 64)
			c, ok := channels[channel_to_use]
			if !ok {
				log.Printf("we have no real target to send the result to, including no default channel defined")
				markTaskAsSended(db, id)
				continue
			}
			log.Printf("useDefaultRecipient flag value is %t", useDefaultRecipient)
			var tg_call_res int
			if useDefaultRecipient == false {
				tg_call_res = sendResult(&c, &invokedByID_int, result, id)
			} else {
				tg_call_res = sendResult(&c, nil, result, id)
			}
			if tg_call_res == 0 {
				markTaskAsSended(db, id)
			} else {
				log.Printf("the call to channels/telegram/ service returned error")
				continue
			}
		} else {
			log.Printf("we have no real target to send the result to, including no default channel defined")
			markTaskAsSended(db, id)
			continue
		}
	}
}

func sendResult(channel *Channel, recipient *int64, result string, taskID int64) int {
	req := map[string]interface{}{
		"message": result,
	}
	endpoint := channel.EndpointToDef
	if recipient != nil {
		req["chat_id"] = *recipient
		endpoint = channel.Endpoint
	}
	jsonData, err := json.Marshal(req)
	if err != nil {
		log.Printf("error marshaling telegram request for task %d: %v", taskID, err)
		return -1
	}
	url := fmt.Sprintf("http://%s:%d/%s", channel.Address, channel.Port, endpoint)
	httpReq, err := http.NewRequest("POST", url, bytes.NewReader(jsonData))
	if err != nil {
		log.Printf("error creating HTTP request for task %d: %v", taskID, err)
		return -1
	}
	httpReq.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(httpReq)
	if err != nil {
		log.Printf("error sending message to the channel for task %d: %v", taskID, err)
		return -1
	}
	resp.Body.Close()
	if resp.StatusCode == http.StatusOK {
		return 0
	} else {
		log.Printf("channel API returned status %d for task %d", resp.StatusCode, taskID)
		return -1
	}
}

type AppConfig struct {
	Host string `toml:"host"`
	Port int    `toml:"port"`
}

func main() {
	// Read config
	var config AppConfig
	if _, err := toml.DecodeFile("config.toml", &config); err != nil {
		log.Printf("Failed to read config.toml, using defaults: %v", err)
		config.Host = "127.0.0.1"
		config.Port = 8089
	}

	db, err := initDB("wingman.db")
	if err != nil {
		log.Fatalf("failed to init db: %v", err)
	}
	defer db.Close()

	plugins, err := loadConfigs[Plugin]("plugins", "plugin.json")
	if err != nil {
		log.Fatalf("failed to load plugins: %v", err)
	}
	log.Printf("loaded %d plugin(s)", len(plugins))
	// Create plugins map for easy lookup
	pluginsMap := make(map[string]Plugin)
	for _, p := range plugins {
		pluginsMap[p.ID] = p
	}

	channels, err := loadConfigs[Channel]("channels", "channel.json")
	if err != nil {
		log.Fatalf("failed to load channels: %v", err)
	}
	log.Printf("loaded %d channel(s)", len(channels))
	// Create channels map for easy lookup
	channelsMap := make(map[string]Channel)
	for _, c := range channels {
		channelsMap[c.ID] = c
	}

	// Start the queued task processor
	go processQueuedTasks(db, pluginsMap)

	// Start the telegram results sender
	go processFinishedTasks(db, channelsMap)

	// Set up HTTP endpoints

	http.HandleFunc("/queue_add_task", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var req struct {
			PluginID string            `json:"plugin_id"`
			Inv_With string            `json:"inv_with"`
			Inv_By   string            `json:"inv_by"`
			Params   map[string]string `json:"params"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "Invalid JSON", http.StatusBadRequest)
			return
		}
		log.Printf("/queue_add_task req: %+v", req)

		p, ok := pluginsMap[req.PluginID]
		if !ok {
			http.Error(w, "Plugin not found", http.StatusNotFound)
			return
		}

		id, err := create_task(db, p, req.Inv_With, req.Inv_By, req.Params)
		if err != nil {
			http.Error(w, "Failed to queue task", http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]int64{"id": id})
	})

	go func() {
		addr := config.Host + ":" + fmt.Sprint(config.Port)
		log.Printf("Starting HTTP server on %s", addr)
		if err := http.ListenAndServe(addr, nil); err != nil {
			log.Printf("HTTP server error: %v", err)
		}
	}()

	for range time.NewTicker(time.Minute).C {
		now := time.Now()
		for _, p := range plugins {
			if !isCroned(p) {
				continue
			}
			if matchesCron(p.CronTime, now) {
				if _, err := create_task(db, p, "cron", "n/a", nil); err != nil {
					log.Printf("error creating cron task for %s: %v", p.ID, err)
				}
			}
		}
	}
}
