package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMaskSecrets(t *testing.T) {
	input := `token=sk_example-secret authorization="Bearer abcdef" github=ghp_example`
	masked := maskSecrets(input)
	for _, secret := range []string{"example-secret", "abcdef", "example"} {
		if strings.Contains(masked, secret) {
			t.Fatalf("secret %q was not masked: %s", secret, masked)
		}
	}
}

func TestPathWithinRoot(t *testing.T) {
	root := t.TempDir()
	if !pathWithinRoot(root, filepath.Join(root, "logs", "core.log")) {
		t.Fatal("expected child path to be accepted")
	}
	if pathWithinRoot(root, filepath.Join(root, "..", "secret")) {
		t.Fatal("expected parent traversal to be rejected")
	}
}

func TestAtomicWriteWithBackup(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "kira.yaml")
	if err := os.WriteFile(path, []byte("version: old\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	if err := atomicWriteWithBackup(path, []byte("version: new\n"), 0o640); err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(content) != "version: new\n" {
		t.Fatalf("unexpected content: %s", content)
	}
	backups, err := filepath.Glob(path + ".backup-*")
	if err != nil || len(backups) != 1 {
		t.Fatalf("expected one backup, got %v (%v)", backups, err)
	}
}
