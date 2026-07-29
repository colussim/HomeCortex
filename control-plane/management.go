package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"
)

type editableFile struct {
	ID       string `json:"id"`
	Label    string `json:"label"`
	Path     string `json:"path"`
	Language string `json:"language,omitempty"`
	YAML     bool   `json:"-"`
}

var editableFiles = map[string]editableFile{
	"config":           {ID: "config", Label: "Kira configuration", Path: "config/kira.yaml", YAML: true},
	"prompt_fr":        {ID: "prompt_fr", Label: "Prompt FR", Path: "prompt_fr.txt", Language: "fr"},
	"prompt_suffix_fr": {ID: "prompt_suffix_fr", Label: "Prompt suffix FR", Path: "prompt_suffix_fr.txt", Language: "fr"},
	"prompt_en":        {ID: "prompt_en", Label: "Prompt EN", Path: "prompt_en.txt", Language: "en"},
	"prompt_suffix_en": {ID: "prompt_suffix_en", Label: "Prompt suffix EN", Path: "prompt_suffix_en.txt", Language: "en"},
}

func (s *server) editableFile(id string) (editableFile, string, bool) {
	definition, ok := editableFiles[id]
	if !ok {
		return editableFile{}, "", false
	}
	path := filepath.Join(s.root, definition.Path)
	if !pathWithinRoot(s.root, path) {
		return editableFile{}, "", false
	}
	return definition, path, true
}

func (s *server) handleGetEditableFile(w http.ResponseWriter, r *http.Request) {
	definition, path, ok := s.editableFile(r.PathValue("id"))
	if !ok {
		writeError(w, http.StatusNotFound, "unknown editable file")
		return
	}
	content, err := os.ReadFile(path)
	if err != nil {
		writeError(w, http.StatusNotFound, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"file": definition, "content": string(content),
	})
}

func (s *server) handlePutEditableFile(w http.ResponseWriter, r *http.Request) {
	definition, path, ok := s.editableFile(r.PathValue("id"))
	if !ok {
		writeError(w, http.StatusNotFound, "unknown editable file")
		return
	}
	var request struct {
		Content string `json:"content"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&request); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request")
		return
	}
	if strings.TrimSpace(request.Content) == "" {
		writeError(w, http.StatusBadRequest, "file cannot be empty")
		return
	}
	if definition.YAML {
		if err := s.validateYAML(r.Context(), request.Content); err != nil {
			writeError(w, http.StatusUnprocessableEntity, err.Error())
			return
		}
	}
	if err := atomicWriteWithBackup(path, []byte(request.Content), 0o640); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status": "saved", "restart_required": true, "file": definition,
	})
}

func configuredOllamaModel(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()

	inLLM := false
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := scanner.Text()
		trimmed := strings.TrimSpace(strings.SplitN(line, "#", 2)[0])
		if trimmed == "" {
			continue
		}
		if !strings.HasPrefix(line, " ") && strings.HasSuffix(trimmed, ":") {
			inLLM = trimmed == "llm:"
			continue
		}
		if inLLM && strings.HasPrefix(trimmed, "model:") {
			model := strings.Trim(strings.TrimSpace(strings.TrimPrefix(trimmed, "model:")), `"'`)
			if model != "" {
				return model, nil
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return "", err
	}
	return "", fmt.Errorf("llm.model is not configured")
}

func (s *server) ollamaRequest(ctx context.Context, method, path string, body any) (*http.Response, error) {
	var reader io.Reader
	if body != nil {
		payload, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		reader = bytes.NewReader(payload)
	}
	request, err := http.NewRequestWithContext(ctx, method, "http://127.0.0.1:11434"+path, reader)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Content-Type", "application/json")
	return s.chatClient.Do(request)
}

func (s *server) ollamaModelStatus(ctx context.Context) (map[string]any, error) {
	model, err := configuredOllamaModel(filepath.Join(s.root, "config", "kira.yaml"))
	if err != nil {
		return nil, err
	}
	result := map[string]any{"model": model, "loaded": false}
	response, err := s.ollamaRequest(ctx, http.MethodGet, "/api/ps", nil)
	if err != nil {
		result["engine_online"] = false
		return result, nil
	}
	defer response.Body.Close()
	result["engine_online"] = response.StatusCode == http.StatusOK
	var payload struct {
		Models []struct {
			Name       string `json:"name"`
			Model      string `json:"model"`
			Size       int64  `json:"size"`
			SizeVRAM   int64  `json:"size_vram"`
			ExpiresAt  string `json:"expires_at"`
			ContextLen int64  `json:"context_length"`
		} `json:"models"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		return result, nil
	}
	for _, loaded := range payload.Models {
		if loaded.Name == model || loaded.Model == model || strings.TrimSuffix(loaded.Name, ":latest") == model {
			result["loaded"] = true
			result["size_bytes"] = loaded.Size
			result["vram_bytes"] = loaded.SizeVRAM
			result["expires_at"] = loaded.ExpiresAt
			result["context_length"] = loaded.ContextLen
			break
		}
	}
	return result, nil
}

func (s *server) handleOllamaModel(w http.ResponseWriter, r *http.Request) {
	status, err := s.ollamaModelStatus(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, status)
}

func (s *server) setOllamaModelState(ctx context.Context, model string, load bool) error {
	keepAlive := any(0)
	if load {
		keepAlive = -1
	}
	response, err := s.ollamaRequest(ctx, http.MethodPost, "/api/generate", map[string]any{
		"model": model, "prompt": "", "stream": false, "keep_alive": keepAlive,
	})
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		message, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		return fmt.Errorf("Ollama returned %s: %s", response.Status, strings.TrimSpace(string(message)))
	}
	return nil
}

func (s *server) waitOllamaModelState(ctx context.Context, expected bool) map[string]any {
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	var latest map[string]any
	for {
		status, _ := s.ollamaModelStatus(ctx)
		if status != nil {
			latest = status
			if loaded, ok := status["loaded"].(bool); ok && loaded == expected {
				return status
			}
		}
		select {
		case <-ctx.Done():
			return latest
		case <-deadline.C:
			return latest
		case <-ticker.C:
		}
	}
}

func (s *server) handleOllamaModelAction(w http.ResponseWriter, r *http.Request) {
	action := r.PathValue("action")
	if action != "load" && action != "unload" && action != "restart" {
		writeError(w, http.StatusBadRequest, "unsupported model action")
		return
	}
	model, err := configuredOllamaModel(filepath.Join(s.root, "config", "kira.yaml"))
	if err != nil {
		writeError(w, http.StatusUnprocessableEntity, err.Error())
		return
	}
	if action == "unload" || action == "restart" {
		if err := s.setOllamaModelState(r.Context(), model, false); err != nil {
			writeError(w, http.StatusBadGateway, err.Error())
			return
		}
		s.waitOllamaModelState(r.Context(), false)
	}
	if action == "load" || action == "restart" {
		if err := s.setOllamaModelState(r.Context(), model, true); err != nil {
			writeError(w, http.StatusBadGateway, err.Error())
			return
		}
	}
	status := s.waitOllamaModelState(r.Context(), action != "unload")
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "action": action, "model_status": status})
}

func commandValue(ctx context.Context, name string, args ...string) string {
	output, err := exec.CommandContext(ctx, name, args...).Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(output))
}

func ollamaVersion(ctx context.Context) string {
	for _, executable := range []string{"ollama", "/usr/local/bin/ollama", "/opt/homebrew/bin/ollama"} {
		if value := commandValue(ctx, executable, "--version"); value != "" {
			return value
		}
	}
	return ""
}

func (s *server) handleDiagnostics(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()

	memoryBytes, _ := strconv.ParseInt(commandValue(ctx, "sysctl", "-n", "hw.memsize"), 10, 64)
	cpuCores, _ := strconv.Atoi(commandValue(ctx, "sysctl", "-n", "hw.ncpu"))
	modelName := commandValue(ctx, "sysctl", "-n", "machdep.cpu.brand_string")
	if modelName == "" {
		modelName = commandValue(ctx, "uname", "-m")
	}
	profile := "generic"
	recommendation := "Use the platform-specific HomeCortex profile."
	if runtime.GOOS == "darwin" && runtime.GOARCH == "arm64" {
		switch {
		case memoryBytes >= 32<<30:
			profile, recommendation = "apple-silicon-performance", "Models up to 7B are suitable; larger models depend on available unified memory."
		case memoryBytes >= 16<<30:
			profile, recommendation = "apple-silicon-balanced", "Prefer 3B models; 7B models may be slower with long contexts."
		default:
			profile, recommendation = "apple-silicon-light", "Prefer compact models and a reduced context window."
		}
	}
	metal := runtime.GOOS == "darwin" && runtime.GOARCH == "arm64"
	writeJSON(w, http.StatusOK, map[string]any{
		"os": runtime.GOOS, "arch": runtime.GOARCH, "model": modelName,
		"memory_bytes": memoryBytes, "cpu_cores": cpuCores, "metal_compatible": metal,
		"profile": profile, "recommendation": recommendation,
		"ollama_version": ollamaVersion(ctx),
	})
}

type resourceSnapshot struct {
	Time                   time.Time `json:"time"`
	CPUPercent             float64   `json:"cpu_percent"`
	MemoryBytes            uint64    `json:"memory_bytes"`
	MemoryTotalBytes       uint64    `json:"memory_total_bytes"`
	ModelMemoryBytes       uint64    `json:"model_memory_bytes"`
	StorageUsedBytes       uint64    `json:"storage_used_bytes"`
	StorageTotalBytes      uint64    `json:"storage_total_bytes"`
	HomeCortexStorageBytes uint64    `json:"homecortex_storage_bytes"`
	NetworkReceiveBPS      float64   `json:"network_receive_bps"`
	NetworkTransmitBPS     float64   `json:"network_transmit_bps"`
	NetworkScope           string    `json:"network_scope"`
	ProcessScope           string    `json:"process_scope"`
}

func (s *server) homeCortexProcessUsage(ctx context.Context) (float64, uint64) {
	output := commandValue(ctx, "ps", "-axo", "%cpu=,rss=,command=")
	var cpu float64
	var memoryKB uint64
	for _, line := range strings.Split(output, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 3 {
			continue
		}
		command := strings.Join(fields[2:], " ")
		homeCortexProcess := strings.Contains(command, s.root) ||
			strings.Contains(command, "homecortex-control")
		ollamaProcess := strings.Contains(command, "ollama serve") ||
			strings.Contains(command, "llama-server")
		if !homeCortexProcess && !ollamaProcess {
			continue
		}
		value, cpuErr := strconv.ParseFloat(strings.ReplaceAll(fields[0], ",", "."), 64)
		rss, rssErr := strconv.ParseUint(fields[1], 10, 64)
		if cpuErr == nil {
			cpu += value
		}
		if homeCortexProcess && rssErr == nil {
			memoryKB += rss
		}
	}
	return cpu, memoryKB * 1024
}

func storageVolumeUsage(path string) (uint64, uint64) {
	var stat syscall.Statfs_t
	if err := syscall.Statfs(path, &stat); err != nil {
		return 0, 0
	}
	total := uint64(stat.Blocks) * uint64(stat.Bsize)
	available := uint64(stat.Bavail) * uint64(stat.Bsize)
	return total - available, total
}

func (s *server) homeCortexStorageUsage(ctx context.Context, now time.Time) uint64 {
	s.resourceMu.Lock()
	if !s.lastDiskAt.IsZero() && now.Sub(s.lastDiskAt) < 30*time.Second {
		value := s.lastDisk
		s.resourceMu.Unlock()
		return value
	}
	s.resourceMu.Unlock()

	fields := strings.Fields(commandValue(ctx, "du", "-sk", s.root))
	var value uint64
	if len(fields) > 0 {
		kb, _ := strconv.ParseUint(fields[0], 10, 64)
		value = kb * 1024
	}
	s.resourceMu.Lock()
	s.lastDiskAt, s.lastDisk = now, value
	s.resourceMu.Unlock()
	return value
}

func networkCounters(ctx context.Context) (uint64, uint64) {
	if runtime.GOOS == "linux" {
		content, err := os.ReadFile("/proc/net/dev")
		if err != nil {
			return 0, 0
		}
		var received, transmitted uint64
		for _, line := range strings.Split(string(content), "\n") {
			if !strings.Contains(line, ":") {
				continue
			}
			parts := strings.SplitN(line, ":", 2)
			if strings.TrimSpace(parts[0]) == "lo" {
				continue
			}
			fields := strings.Fields(parts[1])
			if len(fields) >= 9 {
				in, _ := strconv.ParseUint(fields[0], 10, 64)
				out, _ := strconv.ParseUint(fields[8], 10, 64)
				received += in
				transmitted += out
			}
		}
		return received, transmitted
	}

	output := commandValue(ctx, "netstat", "-ibn")
	type counters struct{ in, out uint64 }
	interfaces := map[string]counters{}
	for _, line := range strings.Split(output, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 10 || fields[0] == "Name" || fields[0] == "lo0" {
			continue
		}
		in, inErr := strconv.ParseUint(fields[6], 10, 64)
		out, outErr := strconv.ParseUint(fields[9], 10, 64)
		if inErr != nil || outErr != nil {
			continue
		}
		current := interfaces[fields[0]]
		if in > current.in {
			current.in = in
		}
		if out > current.out {
			current.out = out
		}
		interfaces[fields[0]] = current
	}
	var received, transmitted uint64
	for _, value := range interfaces {
		received += value.in
		transmitted += value.out
	}
	return received, transmitted
}

func (s *server) handleResources(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 4*time.Second)
	defer cancel()
	now := time.Now()
	cpu, memory := s.homeCortexProcessUsage(ctx)
	var modelMemory uint64
	if status, err := s.ollamaModelStatus(ctx); err == nil {
		switch value := status["vram_bytes"].(type) {
		case int64:
			if value > 0 {
				modelMemory = uint64(value)
			}
		case uint64:
			modelMemory = value
		case float64:
			if value > 0 {
				modelMemory = uint64(value)
			}
		}
	}
	memory += modelMemory
	memoryTotal, _ := strconv.ParseUint(commandValue(ctx, "sysctl", "-n", "hw.memsize"), 10, 64)
	if runtime.GOOS == "linux" {
		if content, err := os.ReadFile("/proc/meminfo"); err == nil {
			for _, line := range strings.Split(string(content), "\n") {
				if strings.HasPrefix(line, "MemTotal:") {
					fields := strings.Fields(line)
					if len(fields) >= 2 {
						kb, _ := strconv.ParseUint(fields[1], 10, 64)
						memoryTotal = kb * 1024
					}
				}
			}
		}
	}
	homeCortexStorage := s.homeCortexStorageUsage(ctx, now)
	storageUsed, storageTotal := storageVolumeUsage(s.root)
	netIn, netOut := networkCounters(ctx)
	if cores := runtime.NumCPU(); cores > 0 {
		cpu /= float64(cores)
	}

	s.resourceMu.Lock()
	var receiveBPS, transmitBPS float64
	if !s.lastNetAt.IsZero() {
		seconds := now.Sub(s.lastNetAt).Seconds()
		if seconds > 0 && netIn >= s.lastNetIn && netOut >= s.lastNetOut {
			receiveBPS = float64(netIn-s.lastNetIn) / seconds
			transmitBPS = float64(netOut-s.lastNetOut) / seconds
		}
	}
	s.lastNetAt, s.lastNetIn, s.lastNetOut = now, netIn, netOut
	s.resourceMu.Unlock()

	writeJSON(w, http.StatusOK, resourceSnapshot{
		Time: now, CPUPercent: cpu, MemoryBytes: memory, MemoryTotalBytes: memoryTotal,
		ModelMemoryBytes: modelMemory,
		StorageUsedBytes: storageUsed, StorageTotalBytes: storageTotal,
		HomeCortexStorageBytes: homeCortexStorage,
		NetworkReceiveBPS:      receiveBPS, NetworkTransmitBPS: transmitBPS,
		NetworkScope: "host", ProcessScope: "homecortex",
	})
}
