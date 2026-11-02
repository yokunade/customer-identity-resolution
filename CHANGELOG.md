# Changelog

All notable changes to this project. Versions follow [Semantic Versioning](https://semver.org);
before 1.0, a minor version may change behaviour. Database migrations are listed
because they run automatically on start-up.

## [Unreleased]

## [0.2.0] - 2026-11-02

Fixes from an external review of 0.1.0, ahead of a first public release.

### Fixed
- Merging a customer that had already been merged elsewhere corrupted redirects
  (`POST /v1/customers/merge`). Both IDs are now resolved to their survivors
  under lock, and the merge applies to those (#1).
- `possible`-tier rules (name + address, no DOB) outranked DOB-anchored
  `probable` rules. They are renumbered 200–250; import-time checks keep tiers
  in order (#1).
- Cleansing: a title in both `title` and `first_name` became the forename;
  "John Jr" lost its generation suffix; "Na" and "Nil" were dropped as nulls;
  explicit gender X was dropped and then overridden by inference; "St" after a
  street name became SAINT ("High St, Leeds", "Main St North") (#2).

### Changed
- A customer matched below the winning rule through an auto-linkable tier is
  now raised for review and listed in `competing_customer_ids`. On the 200k
  benchmark this surfaces about 1,600 real duplicates (97.5% of new cases are
  the same person); the demo queue grows from 1,038 to 1,136 cases (#1).
- Masked AI-reviewer exports leave reviewer notes out unless `--notes` /
  `notes=true` is given, and the scrubber catches reformatted DOBs, compact
  postcodes, street names and two-letter name parts. `PROMPT_VERSION`
  2026-11-02.1 (#3).
- Inbound fields have length limits, and request bodies over
  `MAX_REQUEST_BYTES` (default 2 MB) are refused with 413 (#3).
- The review app sends a strict Content-Security-Policy; every response sends
  `nosniff` and `no-referrer` (#3).
- Sign-in throttling is shared across workers (#3).
- The Docker image installs exact, hashed versions from `requirements.lock`.

### Added
- Migration `006_login_failure`: failed sign-ins, as email hashes.
- MIT licence, `CONTRIBUTING.md`, this changelog, issue templates.
- CI: `ruff check`, a Docker build, and an API smoke test.

### Documentation
- The privacy section no longer claims stored data is pseudonymous: only
  `blocking_key` is. `source_record` holds plain personal data (#3).
- Tier precision table in `docs/MATCHING.md`; benchmark round 5 (#1).

## [0.1.0] - 2026-10-28

First version: cleansing to UK standards, deterministic matching with
blocking and advisory locks, the review app, AI-reviewer training data, the
200k and scale benchmarks, and the Docker demo. Migrations 001–005.

[Unreleased]: https://github.com/yokunade/customer-identity-resolution/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/yokunade/customer-identity-resolution/compare/485474d...v0.2.0
[0.1.0]: https://github.com/yokunade/customer-identity-resolution/tree/485474d
