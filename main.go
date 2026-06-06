package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"time"

	"github.com/BurntSushi/toml"
	_ "github.com/mattn/go-sqlite3"
	"github.com/robfig/cron/v3"
)

type Plugin struct {
	ID             string `json:"id"`
	Name           string `json:"name"`
	InvocationWith string `json:"invocation_with"`
	InvocationFile string `json:"invocation_file"`
	Adhoc          string `json:"adhoc"`
	Crone          string `json:"crone"`
	CroneTime      string `json:"crone_time"`
	Dir            string
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
			finished_at INTEGER,
			result TEXT,
			result_sent_at INTEGER
		)
	`)
	if err != nil {
		return nil, err
	}
	return db, nil
}

func create_task(db *sql.DB, p Plugin, inv_with string, inv_id string) (int64, error) {
	now := time.Now().UTC().Unix()
	res, err := db.Exec( //res, err:
		"INSERT INTO tasks_queued (created_at, plugin_id, invoked_with, invoked_by_id) VALUES (?, ?, ?, ?)",
		now, p.ID, inv_with, inv_id,
	)
	if err != nil {
		log.Printf("error inserting task for %s: %v", p.ID, err)
		return 0, err
	}
	id, _ := res.LastInsertId()
	if err != nil {
		log.Printf("error getting last insert ID for %s: %v", p.ID, err)
		return 0, err
	}
	return id, nil
}

func processQueuedTasks(db *sql.DB, plugins map[string]Plugin) {
	for range time.NewTicker(time.Second).C {
		// Find queued tasks that haven't been invoked yet
		var id int64
		var pluginID string
		err := db.QueryRow("SELECT id, plugin_id FROM tasks_queued WHERE invoked_at IS NULL LIMIT 1").Scan(&id, &pluginID)
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
		fullCommand := fmt.Sprintf("%s %s", p.InvocationWith, p.InvocationFile)
		cmd := exec.Command("bash", "-c", fullCommand)
		cmd.Dir = p.Dir
		var stdout, stderr bytes.Buffer
		cmd.Stdout = &stdout
		cmd.Stderr = &stderr
		log.Printf("invoking queued task %d (plugin %s): %s %s", id, p.ID, p.InvocationWith, p.InvocationFile)
		runErr := cmd.Run()
		finishTime := time.Now().UTC().Unix()
		result := stdout.String() + "\n" + stderr.String()
		_, err = db.Exec("UPDATE tasks_queued SET finished_at = ?, result = ? WHERE id = ?", finishTime, result, id)
		if err != nil {
			log.Printf("error updating finished_at for task %d: %v", id, err)
		}
		if runErr != nil {
			log.Printf("error running queued task %d (plugin %s): %v", id, p.ID, runErr)
		}
	}
}

func processFinishedTasks(db *sql.DB, config *Config) {
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
		if invokedWith == "comms_tg_menu" {
			log.Printf("invoked with telegram dialog")
			// send to tg
			invokedByID_int, err := strconv.ParseInt(invokedByID, 10, 64)
			tg_call_res := sendResultToTelegram(db, config, invokedByID_int, result, id)
			if tg_call_res == 0 {
				now := time.Now().UTC().Unix()
				_, err = db.Exec("UPDATE tasks_queued SET result_sent_at = ? WHERE id = ?", now, id)
				if err != nil {
					log.Printf("error updating result_sent_at for task %d: %v", id, err)
				} else {
					log.Printf("successfully sent telegram result for task %d", id)
				}
			} else {
				log.Printf("the call to comms/telegram/ service returned error")
				continue
			}
		} else {
			// Skip non-telegram tasks
			log.Printf("invoked with NON telegram dialog")
			now := time.Now().UTC().Unix()
			_, err = db.Exec("UPDATE tasks_queued SET result_sent_at = ? WHERE id = ?", now, id)
			if err != nil {
				log.Printf("error updating result_sent_at for task %d: %v", id, err)
			} else {
				log.Printf("successfully sent telegram result for task %d", id)
			}
			continue
		}
	}
}

func sendResultToTelegram(db *sql.DB, config *Config, invokedByID int64, result string, taskID int64) int {
	// Prepare request to send message via telegram
	req := map[string]interface{}{
		"chat_id": invokedByID,
		"message": result,
	}
	jsonData, err := json.Marshal(req)
	if err != nil {
		log.Printf("error marshaling telegram request for task %d: %v", taskID, err)
		return -1
	}
	url := fmt.Sprintf("http://%s:%d/send_message_to_chat_id", config.CommTelegramHost, config.CommTelegramPort)
	httpReq, err := http.NewRequest("POST", url, bytes.NewReader(jsonData))
	if err != nil {
		log.Printf("error creating HTTP request for task %d: %v", taskID, err)
		return -1
	}
	httpReq.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(httpReq)
	if err != nil {
		log.Printf("error sending telegram message for task %d: %v", taskID, err)
		return -1
	}
	resp.Body.Close()
	if resp.StatusCode == http.StatusOK {
		return 0
	} else {
		log.Printf("telegram API returned status %d for task %d", resp.StatusCode, taskID)
		return -1
	}
}

type Config struct {
	Host             string `toml:"host"`
	Port             int    `toml:"port"`
	CommTelegramHost string `toml:"comm_telegram_host"`
	CommTelegramPort int    `toml:"comm_telegram_port"`
}

func main() {
	// Read config
	var config Config
	if _, err := toml.DecodeFile("config.toml", &config); err != nil {
		log.Printf("Failed to read config.toml, using defaults: %v", err)
		config.Host = "127.0.0.1"
		config.Port = 8089
		config.CommTelegramHost = "127.0.0.1"
		config.CommTelegramPort = 8085
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

	// Start the queued task processor
	go processQueuedTasks(db, pluginsMap)

	// Start the telegram results sender
	go processFinishedTasks(db, &config)

	// Set up HTTP endpoints

	http.HandleFunc("/queue_add_task", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var req struct {
			PluginID string `json:"plugin_id"`
			Chat_ID  int    `json:"chat_id"`
			Inv_With string `json:"inv_with"`
			Inv_By   string `json:"inv_by"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "Invalid JSON", http.StatusBadRequest)
			return
		}

		p, ok := pluginsMap[req.PluginID]
		if !ok {
			http.Error(w, "Plugin not found", http.StatusNotFound)
			return
		}

		id, _ := create_task(db, p, req.Inv_With, req.Inv_By)

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
				create_task(db, p, "cron", "n/a")
			}
		}
	}
}
