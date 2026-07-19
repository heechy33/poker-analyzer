-- Migration 012: quarantine database objects retired by Phase 0.
--
-- Migrations 003/004/008/009/010 were removed from clean-install history, but
-- an existing database may still contain their tables and untrusted data. This
-- forward migration preserves every row while removing the objects from the
-- public/PostgREST schema. It is intentionally limited to the three retired
-- table names below; a conflicting or unexpected object aborts the transaction.

BEGIN;

CREATE SCHEMA IF NOT EXISTS legacy_solver_archive;
ALTER SCHEMA legacy_solver_archive OWNER TO CURRENT_USER;
COMMENT ON SCHEMA legacy_solver_archive IS
  'Quarantined Phase 0 solver/range tables. Not part of the application schema.';

REVOKE ALL PRIVILEGES ON SCHEMA legacy_solver_archive FROM PUBLIC;

DO $migration$
DECLARE
    legacy_name text;
    role_name text;
    source_oid oid;
    archive_oid oid;
    source_kind "char";
    archived_rows bigint;
    policy_name text;
BEGIN
    -- Supabase creates these roles, while plain PostgreSQL test databases may
    -- not. Revoke only when a role exists so the migration remains portable.
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SCHEMA legacy_solver_archive FROM %I',
                role_name
            );
        END IF;
    END LOOP;

    CREATE TABLE IF NOT EXISTS legacy_solver_archive.remediation_manifest (
        object_name text PRIMARY KEY,
        archived_at timestamptz NOT NULL DEFAULT now(),
        archived_row_count bigint NOT NULL CHECK (archived_row_count >= 0),
        migration_version text NOT NULL
    );

    FOREACH legacy_name IN ARRAY ARRAY[
        'range_library',
        'solver_runs',
        'solver_telemetry'
    ]
    LOOP
        source_oid := to_regclass(format('public.%I', legacy_name));
        archive_oid := to_regclass(
            format('legacy_solver_archive.%I', legacy_name)
        );

        IF source_oid IS NOT NULL AND archive_oid IS NOT NULL THEN
            RAISE EXCEPTION
                'Refusing to archive %. Both public and archive objects exist.',
                legacy_name;
        END IF;

        IF source_oid IS NOT NULL THEN
            SELECT relkind INTO source_kind
            FROM pg_class
            WHERE oid = source_oid;

            IF source_kind NOT IN ('r', 'p') THEN
                RAISE EXCEPTION
                    'Refusing to archive public.%: unexpected relkind %.',
                    legacy_name,
                    source_kind;
            END IF;

            -- Access is revoked before the table leaves public. The transaction
            -- keeps revocation and relocation atomic for concurrent clients.
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM PUBLIC',
                legacy_name
            );
            FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated']
            LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
                    EXECUTE format(
                        'REVOKE ALL PRIVILEGES ON TABLE public.%I FROM %I',
                        legacy_name,
                        role_name
                    );
                END IF;
            END LOOP;

            EXECUTE format('SELECT count(*) FROM public.%I', legacy_name)
                INTO archived_rows;
            EXECUTE format(
                'ALTER TABLE public.%I SET SCHEMA legacy_solver_archive',
                legacy_name
            );

            -- Remove permissive legacy policies such as USING (true). The
            -- archive has no application grants, and RLS remains a second lock.
            FOR policy_name IN
                SELECT pol.polname
                FROM pg_policy AS pol
                WHERE pol.polrelid = to_regclass(
                    format('legacy_solver_archive.%I', legacy_name)
                )
            LOOP
                EXECUTE format(
                    'DROP POLICY %I ON legacy_solver_archive.%I',
                    policy_name,
                    legacy_name
                );
            END LOOP;

            EXECUTE format(
                'ALTER TABLE legacy_solver_archive.%I ENABLE ROW LEVEL SECURITY',
                legacy_name
            );
            EXECUTE format(
                'ALTER TABLE legacy_solver_archive.%I FORCE ROW LEVEL SECURITY',
                legacy_name
            );

            INSERT INTO legacy_solver_archive.remediation_manifest (
                object_name,
                archived_row_count,
                migration_version
            ) VALUES (
                legacy_name,
                archived_rows,
                '012'
            )
            ON CONFLICT (object_name) DO NOTHING;
        ELSIF archive_oid IS NOT NULL THEN
            -- Idempotent reruns preserve the original timestamp and row count.
            CONTINUE;
        END IF;
    END LOOP;
END
$migration$;

-- Defense in depth for the manifest, moved tables, their owned sequences, and
-- any future object the archive owner deliberately creates in this schema.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA legacy_solver_archive FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA legacy_solver_archive FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA legacy_solver_archive
    REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA legacy_solver_archive
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC;

DO $migration$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA legacy_solver_archive FROM %I',
                role_name
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA legacy_solver_archive FROM %I',
                role_name
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA legacy_solver_archive REVOKE ALL PRIVILEGES ON TABLES FROM %I',
                role_name
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA legacy_solver_archive REVOKE ALL PRIVILEGES ON SEQUENCES FROM %I',
                role_name
            );
        END IF;
    END LOOP;
END
$migration$;

COMMIT;
