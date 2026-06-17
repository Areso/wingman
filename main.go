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
	"strconv"
	"strings"
	"time"

	"github.com/BurntSushi/toml"
	_ "github.com/mattn/go-sqlite3"
	"github.com/robfig/cron/v3"
)

type Plugin struct {
	ID                 string `json:"id"`
	Name               string `json:"name"`
	InvocationWith     string `json:"invocation_with"`
	InvocationFile     string `json:"invocation_file"`
	InvocationTimeoutS int32  `json:"invocation_timeout_s"`
	Adhoc              string `json:"adhoc"`
	Crone              string `json:"crone"`
	CroneTime          string `json:"crone_time"`
	Dir                string
}

type Channel struct {
	ID            string `json:"id"`
	Address       string `json:"address"`
	Port          int    `json:"port"`
	Endpoint      string `json:"endpoint"`
	EndpointToDef string `json:"endpoint_to_default"`
	Dir           string
}

func loadPlugins(dir string) ([]Plugin, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}

	var plugins []Plugin
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		path := filepath.Join(dir, entry.Name(), "plugin.json")
		data, err := os.ReadFile(path)
		if err != nil {
			log.Printf("skipping %s: %v", entry.Name(), err)
			continue
		}
		var p Plugin
		if err := json.Unmarshal(data, &p); err != nil {
			log.Printf("skipping %s: %v", entry.Name(), err)
			continue
		}
		p.Dir = filepath.Join(dir, entry.Name())
		plugins = append(plugins, p)
	}
	return plugins, nil
}

func loadChannels(dir string) ([]Channel, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var channels []Channel
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		path := filepath.Join(dir, entry.Name(), "channel.json")
		data, err := os.ReadFile(path)
		if err != nil {
			log.Printf("skipping %s: %v", entry.Name(), err)
			continue
		}
		var p Channel
		if err := json.Unmarshal(data, &p); err != nil {
			log.Printf("skipping %s: %v", entry.Name(), err)
			continue
		}
		p.Dir = filepath.Join(dir, entry.Name())
		channels = append(channels, p)
	}
	return channels, nil
}

func isCroned(p Plugin) bool {
	return p.Crone == "true"
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
			s_key TEXT NOT NULL,
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
			timeout = 30
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
			// 6. Check if the error was caused by a timeout
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

func processFinishedTasks(db *sql.DB, config *Config, channels map[string]Channel) {
	for range time.NewTicker(time.Second * 5).C {
		var id int64
		var invokedWith string
		var invokedByID string
		var result string

		err := db.QueryRow(`
			SELECT id, invoked_with, invoked_by_id, result 
			FROM tasks_queued 
			WHERE finished_at IS NOT NULL
			AND result_sent_at IS NULL 
			LIMIT 1
		`).Scan(&id, &invokedWith, &invokedByID, &result)
		if err != nil {
			if err == sql.ErrNoRows {
				// No tasks ready to process right now; safely skip
				continue
			}
			log.Printf("Some error from Quering inside processFinishedTasks: %v", err)
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
				WHERE s_key='default_channel';
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
			invokedByID_int, err := strconv.ParseInt(invokedByID, 10, 64)
			c, ok := channels[channel_to_use]
			if !ok {
				log.Printf("we have no real target to send the result to, including no default channel defined")
				now := time.Now().UTC().Unix()
				// otherwise we would get this exactly task indefinetly
				_, err = db.Exec("UPDATE tasks_queued SET result_sent_at = ? WHERE id = ?", now, id)
				if err != nil {
					log.Printf("error updating result_sent_at for task %d: %v", id, err)
				} else {
					// otherwise we would get this exactly task indefinetly by the SELECT in the start of this function
					log.Printf("updated result_sent_at successfully %d", id)
				}
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
				now := time.Now().UTC().Unix()
				_, err = db.Exec("UPDATE tasks_queued SET result_sent_at = ? WHERE id = ?", now, id)
				if err != nil {
					log.Printf("error updating result_sent_at for task %d: %v", id, err)
				} else {
					log.Printf("successfully sent telegram result for task %d", id)
				}
			} else {
				log.Printf("the call to channels/telegram/ service returned error")
				continue
			}
		} else {
			log.Printf("we have no real target to send the result to, including no default channel defined")
			now := time.Now().UTC().Unix()
			// otherwise we would get this exactly task indefinetly
			_, err = db.Exec("UPDATE tasks_queued SET result_sent_at = ? WHERE id = ?", now, id)
			if err != nil {
				log.Printf("error updating result_sent_at for task %d: %v", id, err)
			} else {
				// otherwise we would get this exactly task indefinetly by the SELECT in the start of this function
				log.Printf("updated result_sent_at successfully %d", id)
			}
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

type Config struct {
	Host string `toml:"host"`
	Port int    `toml:"port"`
}

func main() {
	// Read config
	var config Config
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

	plugins, err := loadPlugins("plugins")
	if err != nil {
		log.Fatalf("failed to load plugins: %v", err)
	}
	log.Printf("loaded %d plugin(s)", len(plugins))
	// Create plugins map for easy lookup
	pluginsMap := make(map[string]Plugin)
	for _, p := range plugins {
		pluginsMap[p.ID] = p
	}

	channels, err := loadChannels("channels")
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
	go processFinishedTasks(db, &config, channelsMap)

	// Set up HTTP endpoints

	http.HandleFunc("/queue_add_task", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var req struct {
			PluginID string            `json:"plugin_id"`
			Chat_ID  int               `json:"chat_id"`
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
			if matchesCron(p.CroneTime, now) {
				if _, err := create_task(db, p, "cron", "n/a", nil); err != nil {
					log.Printf("error creating cron task for %s: %v", p.ID, err)
				}
			}
		}
	}
}
