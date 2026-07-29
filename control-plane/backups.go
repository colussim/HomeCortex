package main

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type backupManifest struct {
	Format          int       `json:"format"`
	CreatedAt       time.Time `json:"created_at"`
	HomeCortex      string    `json:"homecortex_version"`
	IncludesSecrets bool      `json:"includes_secrets"`
	IncludesTTS     bool      `json:"includes_tts_cache"`
	Reason          string    `json:"reason"`
}

type backupInfo struct {
	Name            string    `json:"name"`
	Size            int64     `json:"size"`
	CreatedAt       time.Time `json:"created_at"`
	IncludesTTS     bool      `json:"includes_tts_cache"`
	IncludesSecrets bool      `json:"includes_secrets"`
}

func (s *server) backupDirectory() string {
	return filepath.Join(s.root, "backups", "manual")
}

func allowedBackupPath(relative string) bool {
	relative = filepath.ToSlash(filepath.Clean(relative))
	if relative == ".env" || relative == "prompt_fr.txt" || relative == "prompt_en.txt" ||
		relative == "prompt_suffix_fr.txt" || relative == "prompt_suffix_en.txt" {
		return true
	}
	return strings.HasPrefix(relative, "config/") ||
		strings.HasPrefix(relative, "prompts/") ||
		strings.HasPrefix(relative, "data/")
}

func (s *server) backupFiles(includeTTS bool) ([]string, error) {
	var files []string
	roots := []string{
		".env", "config", "prompts", "data",
		"prompt_fr.txt", "prompt_en.txt", "prompt_suffix_fr.txt", "prompt_suffix_en.txt",
	}
	for _, relative := range roots {
		path := filepath.Join(s.root, relative)
		info, err := os.Lstat(path)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return nil, err
		}
		if !info.IsDir() {
			files = append(files, relative)
			continue
		}
		err = filepath.WalkDir(path, func(current string, entry fs.DirEntry, walkErr error) error {
			if walkErr != nil {
				return walkErr
			}
			if entry.IsDir() {
				return nil
			}
			if entry.Type()&os.ModeSymlink != 0 {
				return nil
			}
			item, err := filepath.Rel(s.root, current)
			if err != nil {
				return err
			}
			if strings.HasSuffix(item, "-wal") || strings.HasSuffix(item, "-shm") {
				return nil
			}
			if strings.Contains(filepath.Base(item), ".backup-") {
				return nil
			}
			if filepath.Base(item) == ".DS_Store" {
				return nil
			}
			if !includeTTS && filepath.Base(item) == "tts_cache.db" {
				return nil
			}
			if allowedBackupPath(item) {
				files = append(files, item)
			}
			return nil
		})
		if err != nil {
			return nil, err
		}
	}
	sort.Strings(files)
	return files, nil
}

func sqliteSnapshot(ctx context.Context, source string) ([]byte, error) {
	temp, err := os.CreateTemp("", "homecortex-sqlite-*.db")
	if err != nil {
		return nil, err
	}
	tempPath := temp.Name()
	_ = temp.Close()
	defer os.Remove(tempPath)
	command := exec.CommandContext(ctx, "sqlite3", source, ".backup "+tempPath)
	if output, err := command.CombinedOutput(); err != nil {
		return nil, fmt.Errorf("sqlite backup %s: %s", filepath.Base(source), strings.TrimSpace(string(output)))
	}
	return os.ReadFile(tempPath)
}

func (s *server) createBackup(ctx context.Context, includeTTS bool, reason string) (backupInfo, error) {
	files, err := s.backupFiles(includeTTS)
	if err != nil {
		return backupInfo{}, err
	}
	directory := s.backupDirectory()
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return backupInfo{}, err
	}
	now := time.Now()
	name := "homecortex-" + now.Format("20060102-150405.000000000") + ".zip"
	path := filepath.Join(directory, name)
	temp, err := os.CreateTemp(directory, ".homecortex-backup-*.zip")
	if err != nil {
		return backupInfo{}, err
	}
	tempPath := temp.Name()
	defer os.Remove(tempPath)
	writer := zip.NewWriter(temp)
	manifest := backupManifest{
		Format: 1, CreatedAt: now, HomeCortex: "1.2.1",
		IncludesSecrets: true, IncludesTTS: includeTTS, Reason: reason,
	}
	manifestData, _ := json.MarshalIndent(manifest, "", "  ")
	entry, _ := writer.Create("manifest.json")
	_, _ = entry.Write(manifestData)
	for _, relative := range files {
		source := filepath.Join(s.root, relative)
		var content []byte
		if filepath.Ext(source) == ".db" {
			content, err = sqliteSnapshot(ctx, source)
		} else {
			content, err = os.ReadFile(source)
		}
		if err != nil {
			_ = writer.Close()
			_ = temp.Close()
			return backupInfo{}, err
		}
		header := &zip.FileHeader{Name: filepath.ToSlash(relative), Method: zip.Deflate}
		header.SetMode(0o600)
		header.SetModTime(now)
		item, err := writer.CreateHeader(header)
		if err != nil {
			return backupInfo{}, err
		}
		if _, err := io.Copy(item, bytes.NewReader(content)); err != nil {
			return backupInfo{}, err
		}
	}
	if err := writer.Close(); err != nil {
		_ = temp.Close()
		return backupInfo{}, err
	}
	if err := temp.Sync(); err != nil {
		_ = temp.Close()
		return backupInfo{}, err
	}
	if err := temp.Close(); err != nil {
		return backupInfo{}, err
	}
	if err := os.Chmod(tempPath, 0o600); err != nil {
		return backupInfo{}, err
	}
	if err := os.Rename(tempPath, path); err != nil {
		return backupInfo{}, err
	}
	info, err := os.Stat(path)
	if err != nil {
		return backupInfo{}, err
	}
	return backupInfo{
		Name: name, Size: info.Size(), CreatedAt: now,
		IncludesTTS: includeTTS, IncludesSecrets: true,
	}, nil
}

func readBackupInfo(path string) (backupInfo, error) {
	info, err := os.Stat(path)
	if err != nil {
		return backupInfo{}, err
	}
	reader, err := zip.OpenReader(path)
	if err != nil {
		return backupInfo{}, err
	}
	defer reader.Close()
	result := backupInfo{Name: filepath.Base(path), Size: info.Size(), CreatedAt: info.ModTime()}
	for _, file := range reader.File {
		if file.Name != "manifest.json" {
			continue
		}
		stream, err := file.Open()
		if err != nil {
			return backupInfo{}, err
		}
		var manifest backupManifest
		err = json.NewDecoder(io.LimitReader(stream, 64<<10)).Decode(&manifest)
		_ = stream.Close()
		if err != nil || manifest.Format != 1 {
			return backupInfo{}, fmt.Errorf("invalid backup manifest")
		}
		result.CreatedAt = manifest.CreatedAt
		result.IncludesTTS = manifest.IncludesTTS
		result.IncludesSecrets = manifest.IncludesSecrets
		return result, nil
	}
	return backupInfo{}, fmt.Errorf("backup manifest is missing")
}

func (s *server) listBackups() ([]backupInfo, error) {
	entries, err := os.ReadDir(s.backupDirectory())
	if os.IsNotExist(err) {
		return []backupInfo{}, nil
	}
	if err != nil {
		return nil, err
	}
	var backups []backupInfo
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".zip") {
			continue
		}
		info, err := readBackupInfo(filepath.Join(s.backupDirectory(), entry.Name()))
		if err == nil {
			backups = append(backups, info)
		}
	}
	sort.Slice(backups, func(i, j int) bool { return backups[i].CreatedAt.After(backups[j].CreatedAt) })
	return backups, nil
}

func validBackupName(name string) bool {
	return name == filepath.Base(name) && strings.HasPrefix(name, "homecortex-") &&
		strings.HasSuffix(name, ".zip") && !strings.Contains(name, "..")
}

func atomicRestoreWrite(path string, content []byte) error {
	temp, err := os.CreateTemp(filepath.Dir(path), ".homecortex-restore-*")
	if err != nil {
		return err
	}
	tempPath := temp.Name()
	defer os.Remove(tempPath)
	if err := temp.Chmod(0o600); err != nil {
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

func (s *server) restoreBackup(ctx context.Context, name string) error {
	if !validBackupName(name) {
		return fmt.Errorf("invalid backup name")
	}
	path := filepath.Join(s.backupDirectory(), name)
	if _, err := readBackupInfo(path); err != nil {
		return err
	}
	if _, err := s.createBackup(ctx, true, "automatic pre-restore backup"); err != nil {
		return fmt.Errorf("pre-restore backup failed: %w", err)
	}
	reader, err := zip.OpenReader(path)
	if err != nil {
		return err
	}
	defer reader.Close()
	for _, file := range reader.File {
		if file.Name == "manifest.json" {
			continue
		}
		relative := filepath.Clean(filepath.FromSlash(file.Name))
		if !allowedBackupPath(relative) || file.FileInfo().IsDir() {
			return fmt.Errorf("backup contains a forbidden path: %s", file.Name)
		}
		target := filepath.Join(s.root, relative)
		if !pathWithinRoot(s.root, target) {
			return fmt.Errorf("backup path escapes runtime root")
		}
		stream, err := file.Open()
		if err != nil {
			return err
		}
		content, err := io.ReadAll(io.LimitReader(stream, (256<<20)+1))
		_ = stream.Close()
		if err != nil {
			return err
		}
		if len(content) > 256<<20 {
			return fmt.Errorf("backup entry is too large: %s", file.Name)
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o750); err != nil {
			return err
		}
		if err := atomicRestoreWrite(target, content); err != nil {
			return err
		}
	}
	if definition, ok := s.findService("homecortex-core"); ok && definition.Managed {
		if err := manageService(ctx, definition, "restart"); err != nil {
			return fmt.Errorf("restored but Kira restart failed: %w", err)
		}
	}
	return nil
}

func (s *server) handleListBackups(w http.ResponseWriter, _ *http.Request) {
	backups, err := s.listBackups()
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"backups": backups})
}

func (s *server) handleCreateBackup(w http.ResponseWriter, r *http.Request) {
	var request struct {
		IncludeTTS bool `json:"include_tts_cache"`
	}
	if r.ContentLength > 0 {
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10)).Decode(&request); err != nil {
			writeError(w, http.StatusBadRequest, "invalid request")
			return
		}
	}
	info, err := s.createBackup(r.Context(), request.IncludeTTS, "manual dashboard backup")
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, info)
}

func (s *server) handleRestoreBackup(w http.ResponseWriter, r *http.Request) {
	if err := s.restoreBackup(r.Context(), r.PathValue("name")); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "restored", "restart_required": false})
}
