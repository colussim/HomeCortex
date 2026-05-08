// kira-web — Kira Chat Web Interface
//
// Build:
//   go build -o kira-web .
//
// Run:
//   ./kira-web
//   ./kira-web --config config/kira-web.json
//
// Then open: http://localhost:3000

package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"html/template"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"time"

	// embed.FS : les fichiers templates/ et static/ sont embarqués
	// dans le binaire à la compilation — un seul binaire standalone
	_ "embed"
)

// ── Embedded files ────────────────────────────────────────────────────────────
// Ces directives embarquent les fichiers dans le binaire au moment du build.
// Modifier templates/index.html ou static/* puis recompiler.

//go:embed templates/index.html
var indexHTML string

//go:embed static/style.css
var styleCSS string

//go:embed static/app.js
var appJS string

//go:embed static/imgs
var appImgs string

// ── Config ────────────────────────────────────────────────────────────────────

type Config struct {
	BackendURL  string   `json:"backend_url"`
	Web         WebConf  `json:"web"`
	Rooms       []string `json:"rooms"`
	Version     string   `json:"version"`
	Logo        string   `json:"logo"`
	Language    string   `json:"language"`
	DefaultRoom string   `json:"default_room"`
	Token       string   `json:"token"`
}

type WebConf struct {
	Host        string `json:"host"`
	Port        int    `json:"port"`
	OpenBrowser bool   `json:"open_browser"`
}

// ── Localisation ──────────────────────────────────────────────────────────────

type Locale struct {
	Lang             string   `json:"lang"`
	AppTitle         string   `json:"app_title"`
	HeaderTitle      string   `json:"header_title"`
	HeaderSubtitle   string   `json:"header_subtitle"`
	StatusChecking   string   `json:"status_checking"`
	StatusOnline     string   `json:"status_online"`
	StatusOffline    string   `json:"status_offline"`
	WelcomeTitle     string   `json:"welcome_title"`
	WelcomeSubtitle  string   `json:"welcome_subtitle"`
	Suggestions      []string `json:"suggestions"`
	InputPlaceholder string   `json:"input_placeholder"`
	InputHint        string   `json:"input_hint"`
	SendButton       string   `json:"send_button"`
	BadgeHaOk        string   `json:"badge_ha_ok"`
	BadgeHaErr       string   `json:"badge_ha_err"`
	BadgeSpeech      string   `json:"badge_speech"`
	ErrorBackend     string   `json:"error_backend"`
	ErrorInvalid     string   `json:"error_invalid"`
}

var locale Locale

var cfg Config

// Load Config File
func loadConfig(filename string) (Config, error) {
	var config Config

	data, err := os.ReadFile(filename)
	if err != nil {
		return config, fmt.Errorf("❌ failed to read config file: %v", err)
	}

	if err := json.Unmarshal(data, &config); err != nil {
		return config, fmt.Errorf("❌ failed to parse config JSON: %v", err)
	}

	return config, nil
}

func loadLocale(lang string) error {
	// Charger depuis locales/<lang>.json
	path := fmt.Sprintf("locales/%s.json", lang)
	data, err := os.ReadFile(path)
	if err != nil {
		// Fallback français embarqué si fichier absent
		fmt.Printf("   locales/%s.json introuvable — fallback fr", lang)
		return err
	}
	if err := json.Unmarshal(data, &locale); err != nil {
		return fmt.Errorf("locales/%s.json parse error: %w", lang, err)
	}
	fmt.Printf("   langue chargée : %s", lang)
	return nil
}

func loadChatToken(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	var sats struct {
		ChatClients []struct {
			Token string `json:"token"`
			Name  string `json:"name"`
		} `json:"chat_clients"`
	}
	if err := json.Unmarshal(data, &sats); err != nil {
		return ""
	}
	for _, c := range sats.ChatClients {
		if c.Token != "" {
			fmt.Printf("   token loaded from satellites.json (client: %s)\n", c.Name)
			return c.Token
		}
	}
	return ""
}

// ── Types ─────────────────────────────────────────────────────────────────────

type ChatRequest struct {
	Text string `json:"text"`
	Room string `json:"room,omitempty"`
}

type ChatResponse struct {
	Status   string `json:"status"`
	Reply    string `json:"reply"`
	Category string `json:"category"`
	HaAck    string `json:"ha_ack"`
	Room     string `json:"room"`
}

type WebRequest struct {
	Text string `json:"text"`
	Room string `json:"room"`
}

type WebResponse struct {
	Reply    string `json:"reply"`
	Category string `json:"category"`
	HaAck    string `json:"ha_ack"`
	Elapsed  string `json:"elapsed"`
	Error    string `json:"error,omitempty"`
}

// TemplateData contient les variables injectées dans index.html
type TemplateData struct {
	Rooms       []string
	DefaultRoom string
	BackendURL  string
	Version     string
	Logo        string
	L           Locale
}

// ── Main ──────────────────────────────────────────────────────────────────────

func main() {
	var err error
	configPath := flag.String("config", "config/config.json", "Path to config file")
	flag.Parse()

	fmt.Printf("\n🎙️  Kira Web Chat\n")

	cfg, err = loadConfig(*configPath)
	if err != nil {
		log.Fatalf("Config error: %v", err)
		os.Exit(1)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/", handleIndex)
	mux.HandleFunc("/static/style.css", handleCSS)
	mux.HandleFunc("/static/app.js", handleJS)
	mux.HandleFunc("/static/imgs/", handleImgs)
	mux.HandleFunc("/api/chat", handleChat)
	mux.HandleFunc("/api/health", handleHealth)

	addr := fmt.Sprintf("%s:%d", cfg.Web.Host, cfg.Web.Port)
	webURL := fmt.Sprintf("http://localhost:%d", cfg.Web.Port)

	lang := cfg.Language
	if lang == "" {
		lang = "fr"
	}
	if err := loadLocale(lang); err != nil {
		log.Printf("Locale Load error: %v", err)
		os.Exit(1)
	}

	fmt.Printf("   Backend : %s\n", cfg.BackendURL)
	fmt.Printf("   Token   : %s\n", maskToken(cfg.Token))
	fmt.Printf("   Open    : %s\n\n", webURL)

	if cfg.Web.OpenBrowser {
		go func() {
			time.Sleep(400 * time.Millisecond)
			openBrowser(webURL)
		}()
	}

	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatal(err)
	}
}

// ── Handlers ──────────────────────────────────────────────────────────────────

func handleIndex(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}

	tmpl, err := template.New("index").Parse(indexHTML)
	if err != nil {
		http.Error(w, "template error: "+err.Error(), 500)
		return
	}

	data := TemplateData{
		Rooms:       cfg.Rooms,
		DefaultRoom: cfg.DefaultRoom,
		BackendURL:  cfg.BackendURL,
		Version:     cfg.Version,
		Logo:        cfg.Logo,
		L:           locale,
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := tmpl.Execute(w, data); err != nil {
		log.Printf("Template execute error: %v", err)
	}
}

func handleCSS(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/css; charset=utf-8")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	fmt.Fprint(w, styleCSS)
}

func handleJS(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	fmt.Fprint(w, appJS)
}

func handleImgs(w http.ResponseWriter, r *http.Request) {
	// Sert les images depuis static/imgs/ sur le disque
	// Les images ne sont pas embarquées dans le binaire — modifiables sans recompiler
	filePath := "." + r.URL.Path // ex: ./static/imgs/kira.png
	http.ServeFile(w, r, filePath)
}

func handleChat(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Error(w, "POST only", 405)
		return
	}
	var req WebRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, WebResponse{Error: "invalid request"}, 400)
		return
	}
	if req.Text == "" {
		writeJSON(w, WebResponse{Error: "empty text"}, 400)
		return
	}
	if req.Room == "" {
		req.Room = cfg.DefaultRoom
	}

	start := time.Now()
	resp, err := forwardToKira(req.Text, req.Room)
	elapsed := time.Since(start)

	if err != nil {
		writeJSON(w, WebResponse{Error: err.Error()}, 502)
		return
	}

	writeJSON(w, WebResponse{
		Reply:    resp.Reply,
		Category: resp.Category,
		HaAck:    resp.HaAck,
		Elapsed:  fmt.Sprintf("%.2fs", elapsed.Seconds()),
	}, 200)
}

func handleHealth(w http.ResponseWriter, r *http.Request) {
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get(cfg.BackendURL + "/health")
	if err != nil || resp.StatusCode != 200 {
		writeJSON(w, map[string]string{"status": "offline"}, 502)
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	w.Header().Set("Content-Type", "application/json")
	w.Write(body)
}

// ── Kira proxy ────────────────────────────────────────────────────────────────

func forwardToKira(text, room string) (*ChatResponse, error) {
	payload, _ := json.Marshal(ChatRequest{Text: text, Room: room})
	req, _ := http.NewRequest("POST", cfg.BackendURL+"/chat", bytes.NewReader(payload))
	req.Header.Set("Content-Type", "application/json")
	if cfg.Token != "" {
		req.Header.Set("X-Token", cfg.Token)
	}

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("backend %d: %s", resp.StatusCode, string(body))
	}

	var cr ChatResponse
	if err := json.Unmarshal(body, &cr); err != nil {
		return nil, err
	}
	return &cr, nil
}

// ── Helpers ───────────────────────────────────────────────────────────────────

func writeJSON(w http.ResponseWriter, v any, code int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func maskToken(t string) string {
	if len(t) < 8 {
		return "not set"
	}
	return t[:8] + "..."
}

func openBrowser(url string) {
	var cmd string
	switch runtime.GOOS {
	case "darwin":
		cmd = "open"
	case "windows":
		cmd = "start"
	default:
		cmd = "xdg-open"
	}
	exec.Command(cmd, url).Start()
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
