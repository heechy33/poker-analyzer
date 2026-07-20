-- Migration 013: permanently remove retired solver and range payloads.
--
-- Migration 012 first moves these objects out of public and records their row
-- counts. This migration is deliberately separate: it leaves the remediation
-- audit trail intact while deleting data that must not survive Phase 0.

BEGIN;

CREATE TABLE IF NOT EXISTS legacy_solver_archive.purge_manifest (
    object_name text PRIMARY KEY,
    purged_at timestamptz NOT NULL DEFAULT now(),
    purged_row_count bigint NOT NULL CHECK (purged_row_count >= 0),
    migration_version text NOT NULL
);

-- The archive schema is not exposed and all client grants are revoked, but
-- keep RLS enabled as an independent Supabase safety boundary. No policies are
-- created, so anon/authenticated roles cannot read or mutate manifest rows.
ALTER TABLE legacy_solver_archive.purge_manifest ENABLE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE
    legacy_solver_archive.purge_manifest FROM PUBLIC;

DO $migration$
DECLARE
    legacy_name text;
    role_name text;
    public_oid oid;
    archive_oid oid;
    archive_kind "char";
    purged_rows bigint;
BEGIN
    FOREACH legacy_name IN ARRAY ARRAY[
        'range_library',
        'solver_runs',
        'solver_telemetry'
    ]
    LOOP
        public_oid := to_regclass(format('public.%I', legacy_name));
        archive_oid := to_regclass(
            format('legacy_solver_archive.%I', legacy_name)
        );

        IF public_oid IS NOT NULL THEN
            RAISE EXCEPTION
                'Refusing to purge public.%. Apply migration 012 first.',
                legacy_name;
        END IF;

        IF archive_oid IS NULL THEN
            CONTINUE;
        END IF;

        SELECT relkind INTO archive_kind
        FROM pg_class
        WHERE oid = archive_oid;

        IF archive_kind NOT IN ('r', 'p') THEN
            RAISE EXCEPTION
                'Refusing to purge legacy_solver_archive.%: unexpected relkind %.',
                legacy_name,
                archive_kind;
        END IF;

        EXECUTE format(
            'SELECT count(*) FROM legacy_solver_archive.%I',
            legacy_name
        ) INTO purged_rows;

        INSERT INTO legacy_solver_archive.purge_manifest (
            object_name,
            purged_row_count,
            migration_version
        ) VALUES (
            legacy_name,
            purged_rows,
            '013'
        )
        ON CONFLICT (object_name) DO NOTHING;

        EXECUTE format(
            'DROP TABLE legacy_solver_archive.%I',
            legacy_name
        );
    END LOOP;

    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE legacy_solver_archive.purge_manifest FROM %I',
                role_name
            );
        END IF;
    END LOOP;
END
$migration$;

COMMIT;
