-- Restore the delete action in the public, unauthenticated ABC application.
-- Any visitor with the public anon key can delete tracked dossiers.
DROP POLICY IF EXISTS "anon_delete" ON legislative_dossiers;
CREATE POLICY "anon_delete" ON legislative_dossiers
  FOR DELETE TO anon USING (true);
