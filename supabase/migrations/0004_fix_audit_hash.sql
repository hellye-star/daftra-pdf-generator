-- ============================================================================
-- Vista Client Proposal Portal — 0004 · fix the audit hash-chain function
-- ----------------------------------------------------------------------------
-- 0001/0002 used pgcrypto's digest(text,'sha256'); on Supabase pgcrypto lives
-- in the `extensions` schema, not `public`, so the trigger failed whenever a
-- row was actually inserted into portal_audit_events. Phase 1 swallowed that
-- (audit writes were best-effort); Phase 2 client inserts abort on it.
--
-- Fix: use the built-in sha256(bytea) (core Postgres 11+, no extension needed).
-- Idempotent — safe to run once, on top of 0001-0003.
-- ============================================================================

create or replace function portal_audit_hash() returns trigger
language plpgsql set search_path = public as $$
declare last_hash text;
begin
  select row_hash into last_hash from portal_audit_events
    where proposal_id is not distinct from new.proposal_id
    order by id desc limit 1;
  new.prev_hash := last_hash;
  new.row_hash  := encode(
    sha256(convert_to(
      coalesce(last_hash,'') || new.event_type || new.actor_id ||
      coalesce(new.field_token,'') || coalesce(new.old_value::text,'') ||
      coalesce(new.new_value::text,'') || new.created_at::text, 'UTF8')), 'hex');
  return new;
end $$;

drop trigger if exists portal_audit_hash_t on portal_audit_events;
create trigger portal_audit_hash_t before insert on portal_audit_events
  for each row execute function portal_audit_hash();

notify pgrst, 'reload schema';

-- verify:  select portal_log_event(null,null,'system','t','t','test.ping');
--          select event_type, row_hash from portal_audit_events order by id desc limit 1;
