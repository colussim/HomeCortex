package main

import (
	"bufio"
	"bytes"
	"context"
	"embed"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"log"
	"mime"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
)

//go:embed web/dist/*
var webFiles embed.FS

type serviceDefinition struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	HealthURL   string `json:"health_url,omitempty"`
	Managed     bool   `json:"managed"`
	ManagerID   string `json:"-"`
	LogFile     string `json:"-"`
	Description string `json:"description"`
}

type serviceStatus struct {
	serviceDefinition
	State       string         `json:"state"`
	Healthy     bool           `json:"healthy"`
	LatencyMS   int64          `json:"latency_ms,omitempty"`
	LastChecked time.Time      `json:"last_checked"`
	Details     map[string]any `json:"details,omitempty"`
	Error       string         `json:"error,omitempty"`
}

type server struct {
	root       string
	addr       string
	coreURL    string
	httpClient *http.Client
	chatClient *http.Client
	services   []serviceDefinition
	mu         sync.RWMutex
	cache      map[string]serviceStatus
	resourceMu sync.Mutex
	lastNetAt  time.Time
	lastNetIn  uint64
	lastNetOut uint64
	lastDiskAt time.Time
	lastDisk   uint64
}

func main() {
	defaultRoot := os.Getenv("HOME_CORTEX_ROOT")
	if defaultRoot == "" {
		home, _ := os.UserHomeDir()
		defaultRoot = filepath.Join(home, "Library", "Application Support", "HomeCortex")
	}

	root := flag.String("root", defaultRoot, "HomeCortex runtime root")
	addr := flag.String("addr", "127.0.0.1:3210", "Control Plane listen address")
	coreURL := flag.String("core-url", "http://127.0.0.1:8000", "Kira Core URL")
	flag.Parse()

	s := newServer(*root, *addr, strings.TrimRight(*coreURL, "/"))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go s.healthLoop(ctx)

	log.Printf("HomeCortex Control Plane listening on http://%s", *addr)
	if err := http.ListenAndServe(*addr, s.routes()); err != nil {
		log.Fatal(err)
	}
}

func newServer(root, addr, coreURL string) *server {
	return &server{
		root:       root,
		addr:       addr,
		coreURL:    coreURL,
		httpClient: &http.Client{Timeout: 4 * time.Second},
		chatClient: &http.Client{Timeout: 2 * time.Minute},
		cache:      make(map[string]serviceStatus),
		services: []serviceDefinition{
			{
				ID: "homecortex-core", Name: "Kira Core", HealthURL: coreURL + "/health",
				Managed: true, ManagerID: "io.homecortex.core",
				LogFile: "logs/core.log", Description: "Pipeline vocal, Home Assistant et mémoire",
			},
			{
				ID: "ollama", Name: "Ollama", HealthURL: "http://127.0.0.1:11434/api/tags",
				Managed: false, Description: "Moteur d’inférence LLM local",
			},
			{
				ID: "home-assistant", Name: "Home Assistant",
				Managed: false, Description: "Plateforme domotique observée",
			},
		},
	}
}

func (s *server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/v1/system", s.handleSystem)
	mux.HandleFunc("GET /api/v1/services", s.handleServices)
	mux.HandleFunc("POST /api/v1/services/{id}/{action}", s.handleServiceAction)
	mux.HandleFunc("GET /api/v1/events", s.handleEvents)
	mux.HandleFunc("GET /api/v1/logs/stream", s.handleLogs)
	mux.HandleFunc("GET /api/v1/config", s.handleGetConfig)
	mux.HandleFunc("PUT /api/v1/config", s.handlePutConfig)
	mux.HandleFunc("GET /api/v1/files/{id}", s.handleGetEditableFile)
	mux.HandleFunc("PUT /api/v1/files/{id}", s.handlePutEditableFile)
	mux.HandleFunc("GET /api/v1/ollama/model", s.handleOllamaModel)
	mux.HandleFunc("POST /api/v1/ollama/model/{action}", s.handleOllamaModelAction)
	mux.HandleFunc("GET /api/v1/diagnostics", s.handleDiagnostics)
	mux.HandleFunc("GET /api/v1/resources", s.handleResources)
	mux.HandleFunc("GET /api/v1/backups", s.handleListBackups)
	mux.HandleFunc("POST /api/v1/backups", s.handleCreateBackup)
	mux.HandleFunc("POST /api/v1/backups/{name}/restore", s.handleRestoreBackup)
	mux.HandleFunc("POST /api/v1/chat", s.handleChat)
	mux.HandleFunc("/", s.handleWeb)
	return s.securityHeaders(s.localOnly(mux))
}

func (s *server) handleSystem(w http.ResponseWriter, _ *http.Request) {
	host, _ := os.Hostname()
	writeJSON(w, http.StatusOK, map[string]any{
		"hostname": host,
		"os":       runtime.GOOS,
		"arch":     runtime.GOARCH,
		"root":     s.root,
		"version":  "1.2.0-dev",
		"time":     time.Now(),
	})
}

func (s *server) handleServices(w http.ResponseWriter, _ *http.Request) {
	statuses := s.refreshHealth(context.Background())
	writeJSON(w, http.StatusOK, map[string]any{"services": statuses})
}

func (s *server) handleServiceAction(w http.ResponseWriter, r *http.Request) {
	id, action := r.PathValue("id"), r.PathValue("action")
	if action != "start" && action != "stop" && action != "restart" {
		writeError(w, http.StatusBadRequest, "unsupported action")
		return
	}
	definition, ok := s.findService(id)
	if !ok {
		writeError(w, http.StatusNotFound, "unknown service")
		return
	}
	if !definition.Managed {
		writeError(w, http.StatusConflict, "service is observed but not managed")
		return
	}
	if err := manageService(r.Context(), definition, action); err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]string{"status": "accepted", "action": action})
}

func manageService(ctx context.Context, service serviceDefinition, action string) error {
	var command *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		uid := strconv.Itoa(os.Getuid())
		domain := "gui/" + uid + "/" + service.ManagerID
		switch action {
		case "start":
			command = exec.CommandContext(ctx, "launchctl", "kickstart", domain)
		case "stop":
			command = exec.CommandContext(ctx, "launchctl", "kill", "SIGTERM", domain)
		case "restart":
			command = exec.CommandContext(ctx, "launchctl", "kickstart", "-k", domain)
		}
	case "linux":
		command = exec.CommandContext(ctx, "systemctl", action, service.ID+".service")
	default:
		return fmt.Errorf("service management is unsupported on %s", runtime.GOOS)
	}
	output, err := command.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func (s *server) handleEvents(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	for {
		statuses := s.refreshHealth(r.Context())
		payload, _ := json.Marshal(map[string]any{"services": statuses, "time": time.Now()})
		fmt.Fprintf(w, "event: services\ndata: %s\n\n", payload)
		flusher.Flush()
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
		}
	}
}

func (s *server) handleLogs(w http.ResponseWriter, r *http.Request) {
	id := r.URL.Query().Get("service")
	if id == "" {
		id = "homecortex-core"
	}
	definition, ok := s.findService(id)
	if !ok || definition.LogFile == "" {
		writeError(w, http.StatusNotFound, "no file log is configured for this service")
		return
	}
	path := filepath.Join(s.root, definition.LogFile)
	if !pathWithinRoot(s.root, path) {
		writeError(w, http.StatusBadRequest, "invalid log path")
		return
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")

	var offset int64
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		file, err := os.Open(path)
		if err == nil {
			info, statErr := file.Stat()
			if statErr == nil {
				if info.Size() < offset {
					offset = 0
				}
				_, _ = file.Seek(offset, io.SeekStart)
				scanner := bufio.NewScanner(file)
				for scanner.Scan() {
					data, _ := json.Marshal(map[string]string{"line": maskSecrets(scanner.Text())})
					fmt.Fprintf(w, "data: %s\n\n", data)
				}
				offset, _ = file.Seek(0, io.SeekCurrent)
			}
			_ = file.Close()
			flusher.Flush()
		}
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
		}
	}
}

func (s *server) handleGetConfig(w http.ResponseWriter, _ *http.Request) {
	path := filepath.Join(s.root, "config", "kira.yaml")
	content, err := os.ReadFile(path)
	if err != nil {
		writeError(w, http.StatusNotFound, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"content": string(content), "path": "config/kira.yaml"})
}

func (s *server) handlePutConfig(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Content string `json:"content"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&request); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request")
		return
	}
	if strings.TrimSpace(request.Content) == "" {
		writeError(w, http.StatusBadRequest, "configuration cannot be empty")
		return
	}
	if err := s.validateYAML(r.Context(), request.Content); err != nil {
		writeError(w, http.StatusUnprocessableEntity, err.Error())
		return
	}
	path := filepath.Join(s.root, "config", "kira.yaml")
	if err := atomicWriteWithBackup(path, []byte(request.Content), 0o640); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "saved", "restart_required": true})
}

func (s *server) validateYAML(ctx context.Context, content string) error {
	python := filepath.Join(s.root, ".venv", "bin", "python")
	if _, err := os.Stat(python); err != nil {
		python = "python3"
	}
	command := exec.CommandContext(ctx, python, "-c", "import sys,yaml; yaml.safe_load(sys.stdin.read())")
	command.Stdin = strings.NewReader(content)
	if output, err := command.CombinedOutput(); err != nil {
		return fmt.Errorf("invalid YAML: %s", strings.TrimSpace(string(output)))
	}
	return nil
}

func atomicWriteWithBackup(path string, content []byte, mode fs.FileMode) error {
	if existing, err := os.ReadFile(path); err == nil {
		backup := fmt.Sprintf("%s.backup-%s", path, time.Now().Format("20060102-150405"))
		if err := os.WriteFile(backup, existing, mode); err != nil {
			return err
		}
	}
	temp, err := os.CreateTemp(filepath.Dir(path), ".homecortex-config-*")
	if err != nil {
		return err
	}
	tempPath := temp.Name()
	defer os.Remove(tempPath)
	if err := temp.Chmod(mode); err != nil {
		_ = temp.Close()
		return err
	}
	if _, err := temp.Write(content); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return os.Rename(tempPath, path)
}

func (s *server) handleChat(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 1<<20))
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid request")
		return
	}
	request, err := http.NewRequestWithContext(r.Context(), http.MethodPost, s.coreURL+"/chat", bytes.NewReader(body))
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	request.Header.Set("Content-Type", "application/json")
	if token := readEnvValue(filepath.Join(s.root, ".env"), "KIRA_API_TOKEN"); token != "" {
		request.Header.Set("X-Token", token)
	}
	response, err := s.chatClient.Do(request)
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	defer response.Body.Close()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(response.StatusCode)
	_, _ = io.Copy(w, response.Body)
}

func (s *server) handleWeb(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(filepath.Clean(r.URL.Path), "/")
	if path == "." || path == "" {
		path = "index.html"
	}
	content, err := webFiles.ReadFile("web/dist/" + path)
	if err != nil {
		content, err = webFiles.ReadFile("web/dist/index.html")
		if err != nil {
			http.NotFound(w, r)
			return
		}
		path = "index.html"
	}
	if mediaType := mime.TypeByExtension(filepath.Ext(path)); mediaType != "" {
		w.Header().Set("Content-Type", mediaType)
	}
	_, _ = w.Write(content)
}

func (s *server) healthLoop(ctx context.Context) {
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	for {
		s.refreshHealth(ctx)
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (s *server) refreshHealth(ctx context.Context) []serviceStatus {
	statuses := make([]serviceStatus, 0, len(s.services))
	for _, definition := range s.services {
		status := s.checkService(ctx, definition)
		s.mu.Lock()
		s.cache[definition.ID] = status
		s.mu.Unlock()
		statuses = append(statuses, status)
	}
	return statuses
}

func (s *server) checkService(ctx context.Context, definition serviceDefinition) serviceStatus {
	status := serviceStatus{
		serviceDefinition: definition,
		State:             "unknown", LastChecked: time.Now(),
	}
	healthURL := definition.HealthURL
	if definition.ID == "home-assistant" {
		base := readEnvValue(filepath.Join(s.root, ".env"), "HA_URL_C")
		if base == "" {
			status.State = "unconfigured"
			return status
		}
		healthURL = strings.TrimRight(base, "/") + "/api/"
	}
	if healthURL == "" {
		return status
	}
	request, _ := http.NewRequestWithContext(ctx, http.MethodGet, healthURL, nil)
	if definition.ID == "home-assistant" {
		if token := readEnvValue(filepath.Join(s.root, ".env"), "HA_TOKEN"); token != "" {
			request.Header.Set("Authorization", "Bearer "+token)
		}
	}
	start := time.Now()
	response, err := s.httpClient.Do(request)
	status.LatencyMS = time.Since(start).Milliseconds()
	if err != nil {
		status.State = "offline"
		status.Error = err.Error()
		return status
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		status.State = "degraded"
		status.Error = response.Status
		return status
	}
	status.State, status.Healthy = "healthy", true
	if definition.ID == "homecortex-core" {
		var details map[string]any
		if json.NewDecoder(response.Body).Decode(&details) == nil {
			status.Details = details
		}
	}
	return status
}

func (s *server) findService(id string) (serviceDefinition, bool) {
	for _, definition := range s.services {
		if definition.ID == id {
			return definition, true
		}
	}
	return serviceDefinition{}, false
}

func readEnvValue(path, key string) string {
	file, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, key+"=") {
			return strings.Trim(strings.TrimSpace(strings.TrimPrefix(line, key+"=")), `"'`)
		}
	}
	return ""
}

func maskSecrets(line string) string {
	for _, marker := range []string{"sk_", "ghp_", "Bearer "} {
		if index := strings.Index(line, marker); index >= 0 {
			end := index + len(marker)
			for end < len(line) && !strings.ContainsRune(" \t\r\n\"'", rune(line[end])) {
				end++
			}
			line = line[:index] + marker + "***" + line[end:]
		}
	}
	return line
}

func pathWithinRoot(root, candidate string) bool {
	relative, err := filepath.Rel(root, candidate)
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}

func (s *server) localOnly(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host, _, err := net.SplitHostPort(r.RemoteAddr)
		if err == nil {
			ip := net.ParseIP(host)
			if ip != nil && !ip.IsLoopback() {
				writeError(w, http.StatusForbidden, "Control Plane is local-only")
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

func (s *server) securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'")
		next.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, message string) {
	if message == "" {
		message = http.StatusText(status)
	}
	writeJSON(w, status, map[string]string{"error": message})
}
