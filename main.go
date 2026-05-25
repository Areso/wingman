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
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS tasks_queued (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			created_at INTEGER NOT NULL,
			invoked_at INTEGER,
			plugin_id TEXT NOT NULL,
			finished_at INTEGER,
			result TEXT
		)
	`)
	if err != nil {
		return nil, err
	}
	return db, nil
}

func create_task(db *sql.DB, p Plugin) {
	now := time.Now().UTC().Unix()
	_, err := db.Exec( //res, err:
		"INSERT INTO tasks_queued (created_at, plugin_id) VALUES (?, ?)",
		now, p.ID,
	)
	if err != nil {
		log.Printf("error inserting task for %s: %v", p.ID, err)
		return
	}
	// id, _ := res.LastInsertId()
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
		cmd := exec.Command(p.InvocationWith, p.InvocationFile)
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

func main() {
	// Read config
	var config struct {
		Host string `toml:"host"`
		Port int    `toml:"port"`
	}
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

	// Start the queued task processor
	go processQueuedTasks(db, pluginsMap)

	// Set up HTTP endpoints

	http.HandleFunc("/queue_add_task", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var req struct {
			PluginID string `json:"plugin_id"`
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

		now := time.Now().UTC().Unix()
		res, err := db.Exec(
			"INSERT INTO tasks_queued (created_at, plugin_id) VALUES (?, ?)",
			now, p.ID,
		)
		if err != nil {
			log.Printf("error inserting task for %s: %v", p.ID, err)
			http.Error(w, "Internal error", http.StatusInternalServerError)
			return
		}
		id, _ := res.LastInsertId()

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
				create_task(db, p)
			}
		}
	}
}
