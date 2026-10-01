package main

import (
	"fmt"
	"strconv"
	"time"
)

// Config holds every environment-driven runtime setting in one place, so
// it's obvious what the app can be configured with and easy to unit test
// loading/validation independently of starting a real server.
type Config struct {
	Port            string
	Environment     string
	Version         string
	ShutdownTimeout time.Duration
	PreStopDelay    time.Duration
}

var validEnvironments = map[string]bool{
	"dev":     true,
	"staging": true,
	"prod":    true,
}

// LoadConfig reads settings via getenv (pass os.Getenv in main, a fake map
// lookup in tests) and validates them. It fails fast on invalid input
// rather than starting a server with settings nobody meant to ship — an
// unrecognized ENVIRONMENT or a garbage timeout should crash at startup in
// dev, not silently misbehave in prod.
func LoadConfig(getenv func(string) string, version string) (Config, error) {
	cfg := Config{
		Port:        firstNonEmpty(getenv("PORT"), "8080"),
		Environment: firstNonEmpty(getenv("ENVIRONMENT"), "unknown"),
		Version:     version,
	}

	timeout := 10 * time.Second
	if raw := getenv("SHUTDOWN_TIMEOUT_SECONDS"); raw != "" {
		secs, err := strconv.Atoi(raw)
		if err != nil || secs <= 0 {
			return cfg, fmt.Errorf("invalid SHUTDOWN_TIMEOUT_SECONDS %q: must be a positive integer", raw)
		}
		timeout = time.Duration(secs) * time.Second
	}
	cfg.ShutdownTimeout = timeout

	// PRESTOP_DELAY_SECONDS backs the /internal/prestop endpoint that
	// Kubernetes' preStop hook calls before sending SIGTERM. Unlike the
	// shutdown timeout, 0 is a valid value here — it just means the hook
	// returns immediately (effectively disabled).
	preStopDelay := time.Duration(0)
	if raw := getenv("PRESTOP_DELAY_SECONDS"); raw != "" {
		secs, err := strconv.Atoi(raw)
		if err != nil || secs < 0 {
			return cfg, fmt.Errorf("invalid PRESTOP_DELAY_SECONDS %q: must be a non-negative integer", raw)
		}
		preStopDelay = time.Duration(secs) * time.Second
	}
	cfg.PreStopDelay = preStopDelay

	// "unknown" is allowed (e.g. running locally with no ENVIRONMENT set at
	// all) but anything set must be one of the platform's real environments,
	// so a typo like "prd" fails at startup instead of quietly reporting the
	// wrong environment in every log line and health check.
	if cfg.Environment != "unknown" && !validEnvironments[cfg.Environment] {
		return cfg, fmt.Errorf("invalid ENVIRONMENT %q: must be one of dev, staging, prod", cfg.Environment)
	}

	return cfg, nil
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}
