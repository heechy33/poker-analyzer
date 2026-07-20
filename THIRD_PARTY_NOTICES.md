# Third-party notices and distribution gate

Last reviewed: 2026-07-19. This record is a release control, not legal advice.

## Repository and retained engine license

Poker Analyzer is distributed under GNU AGPL-3.0-or-later; the full license is
in `LICENSE`. The quarantined `solver-wasm` crate links to the retained
`postflop-solver` engine and is governed by the same license.

The retained two-player CFR engine is:

- upstream: `https://github.com/b-inary/postflop-solver`;
- maintained fork: `https://github.com/heechy33/postflop-solver`;
- release-candidate fork commit (currently local; publish before distribution):
  `a67bf3d9f43b9998871a5c999717c1b72bd9e2ef`;
- upstream base before the local provenance cleanup:
  `3a64f855cf205cf7525d844c66c7f29da1bead0f`;
- license: GNU AGPL-3.0-or-later.

The local fork commit removes an ignored engine test containing ranges marked
as copied from a proprietary solver. It makes no runtime engine change.

## Source delivery obligations

Do not distribute or deploy a solver WASM/object-code bundle unless all of the
following are true:

1. The build comes from a clean, immutable Poker Analyzer release commit whose
   gitlink matches the engine commit above.
2. A no-charge source archive for that exact release is available for at least
   as long as the object code is offered. It includes `solver-wasm`, build and
   copy scripts, dependency metadata, and the exact engine submodule source.
3. `LICENSE.txt` and a release-specific `SOURCE-OFFER.txt` are available beside
   the WASM/JavaScript files. The source link is as easy to access as the object
   code and does not require credentials.
4. Any network-facing modified AGPL solver exposes a prominent source link to
   users who interact with it.
5. Copyright, modification, and no-warranty notices remain intact. A release
   owner performs a license review before public distribution.

`npm run build:wasm:release` enforces a clean root tree and an exact submodule
gitlink before creating the notices. The ordinary WASM build is development
only and labels dirty output as non-distributable.

## Range and solution provenance

No range pack or solution set is currently approved for distribution. Future
sets must be first-party generated, explicitly licensed, or privately supplied
by the user, and must store source/permission plus tree, sizing, rake, stack,
and generation-version metadata.

Ranges or charts obtained from third-party solver products may not be scraped,
transcribed, bundled, seeded, cached for reuse, or used to generate distributed
strategy output without written permission that covers the intended use.

The retired range seeds and migrations are absent from clean-install history.
Migration `013_purge_legacy_solver_data.sql` deletes the quarantined
`range_library`, `solver_runs`, and `solver_telemetry` payloads after migration
012 has revoked access and recorded row counts. The purge keeps only private
remediation/purge manifests; it does not delete hands, uploads, sessions,
statistics, or coaching analyses.

## Verification

Run these checks before a release candidate is approved:

```text
python backend/scripts/audit_p0_9_distribution.py
cd backend && pytest tests/test_p0_9_distribution_compliance.py tests/test_legacy_solver_remediation_migration.py
cd frontend && npm run build:wasm:release
```

The database owner must also apply migrations 012 and 013 and verify that all
six names below resolve to null:

```sql
SELECT to_regclass('public.range_library'),
       to_regclass('public.solver_runs'),
       to_regclass('public.solver_telemetry'),
       to_regclass('legacy_solver_archive.range_library'),
       to_regclass('legacy_solver_archive.solver_runs'),
       to_regclass('legacy_solver_archive.solver_telemetry');
```
