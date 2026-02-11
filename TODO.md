# TODO

- Make CLI help output style consistent across commands and subcommands.
- Refactor `noraa run-smoke fetch-data --help` UX to look command-oriented like top-level `noraa --help`.
- Reduce dense option-heavy screens where possible; prefer clearer subcommand flows and concise descriptions.
- Ensure help text presents "what to run next" in a consistent format across CLI sections.
- Replace generic `TEXT` type labels in subcommand help with cleaner UX (prefer command menus or domain-specific metavars).
- Add `run-smoke fetch-data official-ufs` source for vetted UFS/MPAS runtime-compatible HTF datasets.
- Add `aws` CLI prerequisite detection for HTF/S3 fetch paths with clear NORAA action message and install guidance.
- Add docs prerequisites for optional HTF data fetch (`awscli`) and platform-specific install paths.
- Add `run-smoke validate-data` command to check required runtime files and report exact missing items.
- Extend dataset validation to parse `streams.atmosphere` and verify referenced files exist before execute.
- Improve `run-smoke execute` to stage/run from detected case directory (not empty exec dir) when dataset is runtime-compatible.
- Add clear compatibility labels in `fetch-data` output: `runtime-ready` vs `metadata-only`.
- Add first vetted runtime-ready dataset entry from `noaa-ufs-htf-pds` once identified and tested.
