# TODO

- Treat the current branch as work in progress until the guided runtime-data recommendations match what is actually execute-ready in end-to-end local testing.

- MPAS `run-smoke fetch-data official-regtests` currently fetches the filtered `input-data-20251015/MPAS/` prefix, but that bundle is not execute-ready for NORAA's current `ufsatm` MPAS smoke path. In current testing it does not provide `namelist.atmosphere`, so `run-smoke validate-data` stays `NOT READY` and `run-smoke execute` correctly refuses to run. Before pushing, either:
  - narrow the recommendation language so MPAS `official-regtests` is clearly described as discovery/metadata-only unless a UFS-compatible runtime case is confirmed, or
  - add a truly execute-ready MPAS runtime dataset path and make that the recommended guided/default flow.
