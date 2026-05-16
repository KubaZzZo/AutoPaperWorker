# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows semantic versioning where practical.

## [Unreleased]

### Added

- Added GitHub Actions CI for PR checks, non-integration tests, coverage artifacts, and lint reporting.
- Added Bandit security scanning workflow with uploaded scan artifacts.
- Added contribution guidance for development setup, quality checks, CI expectations, and changelog entries.

### Changed

- Documented the current lint ratchet: PRs should avoid new violations while existing full-repo lint debt is reduced over time.

### Security

- Introduced scheduled and PR-triggered Bandit scans for the `researchclaw` package.

## [0.3.1] - 2026-03-18

### Added

- OpenCode Beast Mode routing for complex code generation workloads.
- Novita AI provider support.

### Fixed

- Improved LLM output parsing robustness and thread-safety hardening.
