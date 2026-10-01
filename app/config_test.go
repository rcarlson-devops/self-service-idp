package main

import (
	"testing"
	"time"
)

// fakeGetenv builds a getenv func backed by a plain map, so tests don't
// touch real process environment variables and can run in parallel safely.
func fakeGetenv(vals map[string]string) func(string) string {
	return func(key string) string { return vals[key] }
}

func TestLoadConfig(t *testing.T) {
	tests := []struct {
		name        string
		env         map[string]string
		wantPort    string
		wantEnv     string
		wantTimeout time.Duration
		wantErr     bool
	}{
		{
			name:        "defaults with nothing set",
			env:         map[string]string{},
			wantPort:    "8080",
			wantEnv:     "unknown",
			wantTimeout: 10 * time.Second,
		},
		{
			name:        "valid dev environment",
			env:         map[string]string{"ENVIRONMENT": "dev"},
			wantPort:    "8080",
			wantEnv:     "dev",
			wantTimeout: 10 * time.Second,
		},
		{
			name:        "valid staging with custom port",
			env:         map[string]string{"ENVIRONMENT": "staging", "PORT": "9090"},
			wantPort:    "9090",
			wantEnv:     "staging",
			wantTimeout: 10 * time.Second,
		},
		{
			name:        "valid prod with custom shutdown timeout",
			env:         map[string]string{"ENVIRONMENT": "prod", "SHUTDOWN_TIMEOUT_SECONDS": "30"},
			wantPort:    "8080",
			wantEnv:     "prod",
			wantTimeout: 30 * time.Second,
		},
		{
			name:    "invalid environment name",
			env:     map[string]string{"ENVIRONMENT": "prd"},
			wantErr: true,
		},
		{
			name:    "non-numeric shutdown timeout",
			env:     map[string]string{"SHUTDOWN_TIMEOUT_SECONDS": "soon"},
			wantErr: true,
		},
		{
			name:    "zero shutdown timeout is rejected",
			env:     map[string]string{"SHUTDOWN_TIMEOUT_SECONDS": "0"},
			wantErr: true,
		},
		{
			name:    "negative shutdown timeout is rejected",
			env:     map[string]string{"SHUTDOWN_TIMEOUT_SECONDS": "-5"},
			wantErr: true,
		},
		{
			name:        "zero prestop delay is valid (hook disabled)",
			env:         map[string]string{"PRESTOP_DELAY_SECONDS": "0"},
			wantPort:    "8080",
			wantEnv:     "unknown",
			wantTimeout: 10 * time.Second,
		},
		{
			name:    "negative prestop delay is rejected",
			env:     map[string]string{"PRESTOP_DELAY_SECONDS": "-1"},
			wantErr: true,
		},
		{
			name:    "non-numeric prestop delay is rejected",
			env:     map[string]string{"PRESTOP_DELAY_SECONDS": "soon"},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cfg, err := LoadConfig(fakeGetenv(tt.env), "test-version")

			if tt.wantErr {
				if err == nil {
					t.Fatalf("expected an error, got nil (cfg: %+v)", cfg)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if cfg.Port != tt.wantPort {
				t.Errorf("Port = %q, want %q", cfg.Port, tt.wantPort)
			}
			if cfg.Environment != tt.wantEnv {
				t.Errorf("Environment = %q, want %q", cfg.Environment, tt.wantEnv)
			}
			if cfg.ShutdownTimeout != tt.wantTimeout {
				t.Errorf("ShutdownTimeout = %v, want %v", cfg.ShutdownTimeout, tt.wantTimeout)
			}
			if cfg.Version != "test-version" {
				t.Errorf("Version = %q, want %q", cfg.Version, "test-version")
			}
		})
	}
}
