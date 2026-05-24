package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"github.com/robfig/cron/v3"
	"github.com/BurntSushi/toml"
	_ "github.com/mattn/go-sqlite3"
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
			finished_at INTEGER
		)
	`)
	if err != nil {
		return nil, err
	}
	return db, nil
}

func invoke(db *sql.DB, p Plugin) {
	now := time.Now().UTC().Unix()

	res, err := db.Exec(
		"INSERT INTO tasks_queued (created_at, plugin_id) VALUES (?, ?)",
		now, p.ID,
	)
	if err != nil {
		log.Printf("error inserting task for %s: %v", p.ID, err)
		return
	}
	id, _ := res.LastInsertId()

	_, err = db.Exec("UPDATE tasks_queued SET invoked_at = ? WHERE id = ?", now, id)
	if err != nil {
		log.Printf("error updating invoked_at for task %d: %v", id, err)
	}

	cmd := exec.Command(p.InvocationWith, p.InvocationFile)
	cmd.Dir = p.Dir
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	log.Printf("invoking %s: %s %s", p.ID, p.InvocationWith, p.InvocationFile)

	runErr := cmd.Run()

	finishTime := time.Now().UTC().Unix()
	_, err = db.Exec("UPDATE tasks_queued SET finished_at = ? WHERE id = ?", finishTime, id)
	if err != nil {
		log.Printf("error updating finished_at for task %d: %v", id, err)
	}

	if runErr != nil {
		log.Printf("error running %s: %v", p.ID, runErr)
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

	// Set up HTTP endpoints
	pluginsMap := make(map[string]Plugin)
	for _, p := range plugins {
		pluginsMap[p.ID] = p
	}

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
				invoke(db, p)
			}
		}
	}
}
