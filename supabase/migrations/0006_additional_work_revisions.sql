-- ============================================================================
-- Vista Client Proposal Portal — 0006 · independent Additional Work revisions
-- ----------------------------------------------------------------------------
-- Additional Work (work carried out beyond the original quoted/signed scope)
-- must be publishable and versioned completely independently of the Main
-- Proposal, while sharing the same portal_proposals / portal_clients /
-- portal_proposal_grants row — so the same client access covers both.
--
-- Approach: reuse portal_revisions as-is (immutability, RLS, Storage read
-- policy, audit trail are all already scoped by proposal_id only, with zero
-- awareness of "what kind" of revision a row is — verified by inspection of
-- 0001/0002/0003 before writing this migration). Add one discriminator
-- column so the SAME proposal_id can hold two independent revision streams:
--   document_type = 'proposal'         -> the existing Main Proposal chain
--                                          (R1, R1-a, R1-b, ...)
--   document_type = 'additional_work'  -> a brand new, separate chain
--                                          (AW-R1, AW-R2, AW-R3, ...)
--
-- What this migration does NOT touch:
--   * RLS policies (pr_read / prp_read / pfd_read / storage read policy) —
--     all already gate purely on proposal_id, so both document_types are
--     already covered by the existing policies without any change.
--   * Immutability triggers — already bound table-wide on portal_revisions
--     and portal_revision_photos; both document_types are covered.
--   * The audit hash chain — already keyed by proposal_id only.
--   * portal_proposals.current_revision_id — its semantics are UNCHANGED:
--     it continues to mean "the current MAIN PROPOSAL revision" only.
--     Additional Work publishing must never read or write this column.
--   * portal_approvals / portal_field_defs / portal_change_requests — no
--     schema change needed. An Additional Work revision simply never gets
--     any portal_field_defs rows and always publishes an empty
--     approvalTargets list, so the existing 0005 trigger (which requires
--     approval_token to be present in that revision's frozen
--     approvalTargets) already makes it impossible for any approval to
--     attach to an Additional Work revision. No new guard required.
--
-- Idempotent. Run AFTER 0001-0005.
-- Run in: Supabase Dashboard -> SQL Editor -> paste -> Run.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1 · portal_revisions.document_type
-- ---------------------------------------------------------------------------
-- Existing rows have no way to be anything other than a Main Proposal
-- revision today, so DEFAULT 'proposal' both backfills every current row
-- correctly and requires no data migration/UPDATE statement.
do $$ begin
  alter table portal_revisions
    add column document_type text not null default 'proposal';
exception when duplicate_column then null; end $$;

do $$ begin
  alter table portal_revisions
    add constraint portal_revisions_document_type_chk
    check (document_type in ('proposal', 'additional_work'));
exception when duplicate_object then null; end $$;

-- Revision labels are unique per (proposal_id, revision_label) already, and
-- the two chains use disjoint label prefixes at the application layer
-- ('R1','R1-a',... vs 'AW-R1','AW-R2',...) — no constraint change needed
-- there. This index makes the now-mandatory document_type filter on every
-- "latest/previous revision for this proposal" lookup an index scan instead
-- of a filtered sequential scan of portal_revisions_proposal.
create index if not exists portal_revisions_proposal_doctype
  on portal_revisions(proposal_id, document_type, published_at desc);

-- ---------------------------------------------------------------------------
-- 2 · portal_revision_photos.slot — add a real 'additional_work' value
-- ---------------------------------------------------------------------------
-- Phase-1 code temporarily reused slot='existing' for Additional Work photos
-- to avoid a migration. Now that we are already touching this schema, give
-- Additional Work its own honest slot value instead of pretending to be an
-- item's "Existing Condition" photo. All three original values are preserved
-- unchanged; this only WIDENS the allowed set.
--
-- Identify the CHECK constraint(s) actually ATTACHED TO THE slot COLUMN via
-- catalog metadata (pg_constraint.conkey / pg_attribute), not by pattern-
-- matching the constraint's text definition — a text search could also match
-- an unrelated constraint that merely mentions the word "slot" in its
-- expression. conkey is the array of attnums a constraint references; for a
-- simple column-level check like the 0001 original
-- (slot text not null check (slot in (...))) that array contains exactly the
-- slot column's attnum. Scoped explicitly to schema public. table
-- public.portal_revision_photos. Drops only constraints that reference that
-- exact column; every other constraint on the table (including the
-- unrelated NOT NULL / FK / PK constraints) is left untouched.
do $$
declare
  slot_attnum smallint;
  c text;
begin
  select a.attnum into slot_attnum
    from pg_attribute a
    join pg_class t on t.oid = a.attrelid
    join pg_namespace n on n.oid = t.relnamespace
    where n.nspname = 'public'
      and t.relname = 'portal_revision_photos'
      and a.attname = 'slot'
      and a.attnum > 0
      and not a.attisdropped;

  if slot_attnum is not null then
    for c in
      select con.conname
        from pg_constraint con
        join pg_class t on t.oid = con.conrelid
        join pg_namespace n on n.oid = t.relnamespace
        where n.nspname = 'public'
          and t.relname = 'portal_revision_photos'
          and con.contype = 'c'                        -- CHECK constraints only
          and slot_attnum = any(con.conkey)             -- must reference the slot column
    loop
      execute format('alter table public.portal_revision_photos drop constraint %I', c);
    end loop;
  end if;
end $$;

do $$ begin
  alter table public.portal_revision_photos
    add constraint portal_revision_photos_slot_check
    check (slot in ('existing', 'simulation', 'sample', 'additional_work'));
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------------------
-- 3 · refresh PostgREST so the new column is visible immediately
-- ---------------------------------------------------------------------------
notify pgrst, 'reload schema';

-- ============================================================================
-- verify (run as service_role in the SQL editor):
--   select column_name, data_type, column_default from information_schema.columns
--     where table_name = 'portal_revisions' and column_name = 'document_type';
--   select document_type, count(*) from portal_revisions group by 1;   -- all 'proposal' today
--   select conname from pg_constraint where conname in
--     ('portal_revisions_document_type_chk','portal_revision_photos_slot_check');
--   select indexname from pg_indexes where tablename = 'portal_revisions';
-- ============================================================================
