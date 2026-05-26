package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/BurntSushi/toml"
	tgbotapi "github.com/go-telegram-bot-api/telegram-bot-api/v5"
)

// Plugin represents a loaded plugin
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

// PluginInvocationRequest represents the request to invoke a plugin
type PluginInvocationRequest struct {
	ID     string            `json:"id"`
	Params map[string]string `json:"params"`
}

// SendMsgRequest represents the request to send a message to a chat
type SendMsgRequest struct {
	chatID  int64  `json:"chat_id"`
	message string `json:"message"`
}

// Config holds the bot configuration
type Config struct {
	BotToken string `toml:"bot_token"`
	Port     int    `toml:"port"`
}

// Bot holds the telegram bot state
type Bot struct {
	api     *tgbotapi.BotAPI
	plugins map[string]*Plugin
	port    int
}

// newBot creates a new bot instance
func newBot(token string, port int) (*Bot, error) {
	api, err := tgbotapi.NewBotAPI(token)
	if err != nil {
		return nil, fmt.Errorf("failed to create bot: %w", err)
	}

	return &Bot{
		api:     api,
		plugins: make(map[string]*Plugin),
		port:    port,
	}, nil
}

// loadBotToken loads the bot token from systemd credentials
func loadBotToken() (string, error) {
	// Try to load from systemd credential
	credPath := "/Users/areso/.wingman/tg"
	if _, err := os.Stat(credPath); err == nil {
		data, err := os.ReadFile(credPath)
		if err != nil {
			return "", fmt.Errorf("failed to read systemd credential: %w", err)
		}
		return strings.TrimSpace(string(data)), nil
	}

	// Fallback to reading from config.toml
	if _, err := os.Stat("config.toml"); err == nil {
		// This would require a TOML parser - for now, return error
		return "", fmt.Errorf("config.toml not supported for token, use systemd credentials")
	}

	// Fallback to environment variable
	token := os.Getenv("TELEGRAM_BOT_TOKEN")
	if token != "" {
		return token, nil
	}

	return "", fmt.Errorf("no bot token found in systemd credentials, config.toml, or environment variables")
}

// loadPlugins reads and filters plugins from plugins directory
func (b *Bot) loadPlugins(pluginsDir string) error {
	entries, err := os.ReadDir(pluginsDir)
	if err != nil {
		return fmt.Errorf("failed to read plugins directory: %w", err)
	}

	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		path := filepath.Join(pluginsDir, entry.Name(), "plugin.json")
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

		// Only register plugins with ad_hoc: true
		if p.Adhoc == "true" {
			p.Dir = filepath.Join(pluginsDir, entry.Name())
			b.plugins[p.ID] = &p
			log.Printf("Registered ad_hoc plugin: %s (%s)", p.Name, p.ID)
		}
	}

	return nil
}

// start initializes and starts the bot
func (b *Bot) start() error {
	// Set up HTTP endpoint for plugin invocation
	http.HandleFunc("/invoke_plugin", b.handlePluginInvoke)

	// Set up HTTP endpoint for sending a message
	http.HandleFunc("/send_message_to_chat_id", b.send_message_to_chat_id)

	go func() {
		log.Printf("Starting HTTP server on :%d", b.port)
		if err := http.ListenAndServe(fmt.Sprintf(":%d", b.port), nil); err != nil {
			log.Printf("HTTP server error: %v", err)
		}
	}()

	// Set up message handler
	u := tgbotapi.NewUpdate(0)
	u.Timeout = 60

	updates := b.api.GetUpdatesChan(u)

	for update := range updates {
		if update.Message != nil {
			b.handleMessage(update.Message)
		} else if update.CallbackQuery != nil {
			b.handleCallback(update.CallbackQuery)
		}
	}

	return nil
}

// handleSendMessageToChatID handles the /send_message_to_chat_id <chat_id> <message> command
func (b *Bot) handleSendMessageToChatID(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "Failed to read request body", http.StatusBadRequest)
		return
	}
	defer r.Body.Close()
	var req SendMsgRequest
	if err := json.Unmarshal(body, &req); err != nil {
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}
	// send message to a telegram chat
	msg := tgbotapi.NewMessage(req.chatID, req.message)
	b.api.Send(msg)

	// end of block sending message to a telegram chat
	w.WriteHeader(http.StatusOK)
	w.Write([]byte("Plugin invoked successfully"))
}

// sendMainMenu sends the main menu with plugin options
func (b *Bot) sendMainMenu(chatID int64) {
	log.Printf("Chat ID is %d", chatID)
	var rows [][]tgbotapi.InlineKeyboardButton
	var pluginButtons []tgbotapi.InlineKeyboardButton

	// Create buttons for each plugin
	for _, plugin := range b.plugins {
		// Use plugin ID as callback data and name as button text
		pluginButtons = append(pluginButtons, tgbotapi.NewInlineKeyboardButtonData(plugin.Name, plugin.ID))
	}

	// Create 2-column grid for buttons
	for i, button := range pluginButtons {
		if i%2 == 0 {
			rows = append(rows, []tgbotapi.InlineKeyboardButton{})
		}
		rows[len(rows)-1] = append(rows[len(rows)-1], button)
	}

	keyboard := tgbotapi.NewInlineKeyboardMarkup(rows...)

	msg := tgbotapi.NewMessage(chatID, "Select a plugin to run:")
	msg.ReplyMarkup = keyboard
	b.api.Send(msg)
}

// sendPluginList sends a list of available plugins
func (b *Bot) sendPluginList(chatID int64) {
	var message strings.Builder
	message.WriteString("Available plugins:\n\n")

	for _, plugin := range b.plugins {
		message.WriteString(fmt.Sprintf("• %s (%s)\n", plugin.Name, plugin.ID))
	}

	msg := tgbotapi.NewMessage(chatID, message.String())
	b.api.Send(msg)
}

// handleCallback handles callback queries (button clicks)
func (b *Bot) handleCallback(callback *tgbotapi.CallbackQuery) {
	// Acknowledge callback
	ack := tgbotapi.NewCallback(callback.ID, "Processing...")
	b.api.Send(ack)

	// Extract plugin ID from callback data (this should be the plugin ID)
	pluginID := callback.Data

	// Check if plugin exists
	plugin, exists := b.plugins[pluginID]
	if !exists {
		msg := tgbotapi.NewMessage(callback.Message.Chat.ID, "Plugin not found")
		b.api.Send(msg)
		return
	}

	// Prepare the invocation request
	req := PluginInvocationRequest{
		ID:     pluginID,
		Params: make(map[string]string),
	}

	// Send a message indicating the plugin is being invoked
	msg := tgbotapi.NewMessage(callback.Message.Chat.ID, fmt.Sprintf("Invoking %s...", plugin.Name))
	b.api.Send(msg)

	// Make HTTP request to wingman to invoke the plugin
	if err := b.invokePlugin(pluginID, req); err != nil {
		log.Printf("Error invoking plugin %s: %v", pluginID, err)
		msg := tgbotapi.NewMessage(callback.Message.Chat.ID, fmt.Sprintf("Error invoking plugin: %v", err))
		b.api.Send(msg)
		return
	}

	// Confirm successful invocation
	msg = tgbotapi.NewMessage(callback.Message.Chat.ID, fmt.Sprintf("Plugin %s invoked successfully!", plugin.Name))
	b.api.Send(msg)
}

// invokePlugin sends a request to queue a plugin task on the wingman core
func (b *Bot) invokePlugin(pluginID string, req PluginInvocationRequest) error {
	var wingman_config struct {
		Host string `toml:"wingman_host"`
		Port int    `toml:"wingman_port"`
	}
	if _, err := toml.DecodeFile("config.toml", &wingman_config); err != nil {
		log.Printf("Failed to read config.toml: %v", err)
		log.Print("Using default host 127.0.0.1 and port 8089")
		wingman_config.Host = "127.0.0.1"
		wingman_config.Port = 8089
	}
	// Queue the task on the wingman core
	queueReq := map[string]string{"plugin_id": pluginID}
	jsonData, err := json.Marshal(queueReq)
	if err != nil {
		return fmt.Errorf("failed to marshal request: %w", err)
	}
	url := fmt.Sprintf("http://%s:%d/queue_add_task", wingman_config.Host, wingman_config.Port)
	log.Printf("Queuing task on wingman: %s", url)
	httpReq, err := http.NewRequest("POST", url, strings.NewReader(string(jsonData)))
	if err != nil {
		return fmt.Errorf("failed to create HTTP request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(httpReq)
	if err != nil {
		return fmt.Errorf("failed to send HTTP request: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("HTTP request failed with status %d: %s", resp.StatusCode, string(body))
	}
	return nil
}

// handlePluginInvoke handles HTTP requests to invoke plugins
func (b *Bot) handlePluginInvoke(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "Failed to read request body", http.StatusBadRequest)
		return
	}
	defer r.Body.Close()
	var req PluginInvocationRequest
	if err := json.Unmarshal(body, &req); err != nil {
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}
	// Find plugin
	_, exists := b.plugins[req.ID]
	if !exists {
		http.Error(w, "Plugin not found", http.StatusNotFound)
		return
	}
	// For demonstration purposes, we just log the invocation
	log.Printf("Plugin %s invoked with params: %v", req.ID, req.Params)
	// Here you would actually invoke the plugin

	// Make HTTP request to wingman to invoke the plugin
	if err := b.invokePlugin(req.ID, req); err != nil {
		log.Printf("Error invoking plugin %s: %v", req.ID, err)
		http.Error(w, fmt.Sprintf("Error invoking plugin: %v", err), http.StatusInternalServerError)
		return
	}
	// For now, we'll simulate it with a message
	// Note: Since this is an HTTP endpoint, we don't have direct access to user context
	// This is a simplified version - in practice, you'd want to implement proper user tracking
	log.Printf("Plugin %s invoked with parameters: %v", req.ID, req.Params)
	w.WriteHeader(http.StatusOK)
	w.Write([]byte("Plugin invoked successfully"))
}

func main() {
	// Load config from file or use defaults
	var config struct {
		Host string `toml:"comm_telegram_host"`
		Port int    `toml:"comm_telegram_port"`
	}
	if _, err := toml.DecodeFile("config.toml", &config); err != nil {
		log.Printf("Failed to read config.toml: %v", err)
		log.Print("Using default host 127.0.0.1 and port 8085")
		config.Host = "127.0.0.1"
		config.Port = 8085
	}

	// Load bot token from systemd credential
	log.Println("Loading bot token from systemd credentials...")
	token, err := loadBotToken()
	if err != nil {
		log.Fatalf("Failed to load bot token: %v", err)
	}

	// Create bot instance
	bot, err := newBot(token, config.Port)
	if err != nil {
		log.Fatalf("Failed to create bot: %v", err)
	}

	// Load plugins
	log.Println("Loading plugins...")
	if err := bot.loadPlugins("../../plugins"); err != nil {
		log.Fatalf("Failed to load plugins: %v", err)
	}

	log.Printf("Loaded %d ad_hoc plugins", len(bot.plugins))

	// Start bot
	log.Println("Starting Telegram bot...")
	if err := bot.start(); err != nil {
		log.Fatalf("Failed to start bot: %v", err)
	}
}
