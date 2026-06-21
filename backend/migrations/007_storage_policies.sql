-- Migration 007: Supabase Storage policies for hand-histories bucket
--
-- Prerequisites (Supabase Dashboard → Storage):
--   1. Create a private bucket named "hand-histories" (not public).
--   2. Run this file in the SQL Editor.
--
-- Object paths: {user_id}/{upload_id}/{filename}

CREATE POLICY hand_histories_insert_own ON storage.objects
  FOR INSERT
  TO authenticated
  WITH CHECK (
    bucket_id = 'hand-histories'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );

CREATE POLICY hand_histories_select_own ON storage.objects
  FOR SELECT
  TO authenticated
  USING (
    bucket_id = 'hand-histories'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );
