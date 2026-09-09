-- ============================================================================
-- Vista Client Proposal Portal — 0002  ·  Phase 1: grants, RLS, triggers, storage
-- ----------------------------------------------------------------------------
-- Run AFTER 0001_portal_foundation.sql. Fully idempotent — safe to re-run.
-- If you ran an earlier copy of this file, RE-RUN this one: it adds the
-- explicit role GRANTs that the API roles (service_role, authenticated) need,
-- and finishes with a PostgREST schema reload.
--
-- Run in: Supabase Dashboard -> SQL Editor -> paste -> Run.  Watch for errors.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 0 · role grants
--     The Supabase API roles do not automatically get privileges on tables
--     created by raw SQL. Grant explicitly. anon gets nothing.
-- ---------------------------------------------------------------------------
grant usage on schema public to anon, authenticated, service_role;

-- service_role bypasses RLS but still needs base table privileges
grant select, insert, update, delete on
  portal_clients, portal_client_users, portal_proposals, portal_proposal_grants,
  portal_revisions, portal_revision_photos, portal_field_defs, portal_comments,
  portal_change_requests, portal_approvals, portal_audit_events
to service_role;
grant usage, select on all sequences in schema public to service_role;
alter default privileges in schema public grant all on tables    to service_role;
alter default privileges in schema public grant all on sequences to service_role;

-- authenticated: SELECT only, and only on the tables the read-only portal shows.
-- RLS (section 4) then decides which ROWS each user sees.
grant select on
  portal_client_users, portal_proposals, portal_proposal_grants,
  portal_revisions, portal_revision_photos, portal_field_defs
to authenticated;

-- anon: explicitly nothing on any portal_* table
revoke all on
  portal_clients, portal_client_users, portal_proposals, portal_proposal_grants,
  portal_revisions, portal_revision_photos, portal_field_defs, portal_comments,
  portal_change_requests, portal_approvals, portal_audit_events
from anon;

-- ---------------------------------------------------------------------------
-- 1 · helper functions  (+ execute grants to the roles that call them)
-- ---------------------------------------------------------------------------
create or replace function portal_has_grant(p_proposal uuid)
returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from portal_proposal_grants g
    where g.proposal_id   = p_proposal
      and g.client_user_id = auth.uid()
      and g.revoked_at is null
  );
$$;
revoke all on function portal_has_grant(uuid) from public, anon;
grant execute on function portal_has_grant(uuid) to authenticated, service_role;

create or replace function portal_grant_flag(p_proposal uuid, p_flag text)
returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce((
    select case p_flag
             when 'comment' then can_comment
             when 'propose' then can_propose
             when 'approve' then can_approve
           end
    from portal_proposal_grants
    where proposal_id = p_proposal and client_user_id = auth.uid() and revoked_at is null
  ), false);
$$;
revoke all on function portal_grant_flag(uuid, text) from public, anon;
grant execute on function portal_grant_flag(uuid, text) to authenticated, service_role;

-- append-only audit writer (the only privileged write path used in Phase 1)
create or replace function portal_log_event(
  p_proposal uuid, p_revision uuid, p_actor_type portal_actor_type,
  p_actor_id text, p_actor_name text, p_event text,
  p_object_type text default null, p_object_id text default null,
  p_field_token text default null, p_old jsonb default null, p_new jsonb default null,
  p_note text default null, p_ip inet default null, p_ua text default null,
  p_request_id text default null
) returns bigint
language plpgsql security definer set search_path = public as $$
declare new_id bigint;
begin
  insert into portal_audit_events(
    proposal_id, revision_id, actor_type, actor_id, actor_name, event_type,
    object_type, object_id, field_token, old_value, new_value, note, ip, user_agent, request_id)
  values (p_proposal, p_revision, p_actor_type, p_actor_id, p_actor_name, p_event,
    p_object_type, p_object_id, p_field_token, p_old, p_new, p_note, p_ip, p_ua, p_request_id)
  returning id into new_id;
  return new_id;
end $$;
revoke all on function portal_log_event(uuid,uuid,portal_actor_type,text,text,text,text,text,text,jsonb,jsonb,text,inet,text,text)
  from public, anon, authenticated;
grant execute on function portal_log_event(uuid,uuid,portal_actor_type,text,text,text,text,text,text,jsonb,jsonb,text,inet,text,text)
  to service_role;

-- ---------------------------------------------------------------------------
-- 2 · immutability triggers  (bind everyone — service_role bypasses RLS, not triggers)
-- ---------------------------------------------------------------------------
create or replace function portal_deny_mutation() returns trigger
language plpgsql as $$
begin
  raise exception 'portal: % on % is not permitted (append-only)', tg_op, tg_table_name
        using errcode = 'insufficient_privilege';
end $$;

do $$
declare t text;
begin
  foreach t in array array['portal_revisions','portal_revision_photos','portal_field_defs',
                           'portal_approvals','portal_audit_events']
  loop
    execute format('drop trigger if exists %I_immutable on %I;', t, t);
    execute format('create trigger %I_immutable before update or delete on %I
                    for each row execute function portal_deny_mutation();', t, t);
  end loop;
end $$;

-- audit hash chain (per proposal) — tamper-evidence
create or replace function portal_audit_hash() returns trigger
language plpgsql set search_path = public as $$
declare last_hash text;
begin
  select row_hash into last_hash from portal_audit_events
    where proposal_id is not distinct from new.proposal_id
    order by id desc limit 1;
  new.prev_hash := last_hash;
  new.row_hash  := encode(sha256(convert_to(
      coalesce(last_hash,'') || new.event_type || new.actor_id ||
      coalesce(new.field_token,'') || coalesce(new.old_value::text,'') ||
      coalesce(new.new_value::text,'') || new.created_at::text, 'UTF8')), 'hex');
  return new;
end $$;
drop trigger if exists portal_audit_hash_t on portal_audit_events;
create trigger portal_audit_hash_t before insert on portal_audit_events
  for each row execute function portal_audit_hash();

-- ---------------------------------------------------------------------------
-- 3 · RLS — enable on every portal_* table
-- ---------------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array[
    'portal_clients','portal_client_users','portal_proposals','portal_proposal_grants',
    'portal_revisions','portal_revision_photos','portal_field_defs','portal_comments',
    'portal_change_requests','portal_approvals','portal_audit_events']
  loop
    execute format('alter table %I enable row level security;', t);
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- 4 · policies  (Phase 1 = read-only; only SELECT policies for `authenticated`)
-- ---------------------------------------------------------------------------
drop policy if exists pcu_self on portal_client_users;
create policy pcu_self on portal_client_users
  for select to authenticated using (id = auth.uid());

drop policy if exists pgr_self on portal_proposal_grants;
create policy pgr_self on portal_proposal_grants
  for select to authenticated using (client_user_id = auth.uid() and revoked_at is null);

drop policy if exists pp_read on portal_proposals;
create policy pp_read on portal_proposals
  for select to authenticated using (portal_has_grant(id));

drop policy if exists pr_read on portal_revisions;
create policy pr_read on portal_revisions
  for select to authenticated using (portal_has_grant(proposal_id));

drop policy if exists prp_read on portal_revision_photos;
create policy prp_read on portal_revision_photos
  for select to authenticated using (
    portal_has_grant((select proposal_id from portal_revisions r where r.id = revision_id)));

drop policy if exists pfd_read on portal_field_defs;
create policy pfd_read on portal_field_defs
  for select to authenticated using (
    portal_has_grant((select proposal_id from portal_revisions r where r.id = revision_id)));

-- portal_comments / portal_change_requests / portal_approvals / portal_audit_events:
--   NO policy for `authenticated` in Phase 1  -> RLS denies all client access.
--   (Client write paths are added in Phases 2-4.)
-- portal_clients: NO policy -> the portal never needs it.

-- ---------------------------------------------------------------------------
-- 5 · private Storage bucket + read policy
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('proposal-photos', 'proposal-photos', false, 26214400,
        array['image/jpeg','image/png','image/webp'])
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists portal_photo_read on storage.objects;
create policy portal_photo_read on storage.objects
  for select to authenticated using (
    bucket_id = 'proposal-photos'
    and exists (
      select 1
      from portal_revision_photos rp
      join portal_revisions r on r.id = rp.revision_id
      where rp.storage_path = storage.objects.name
        and portal_has_grant(r.proposal_id)
    )
  );
-- no insert/update/delete policy for authenticated -> uploads only via service_role

-- ---------------------------------------------------------------------------
-- 6 · refresh PostgREST so new functions / grants are visible immediately
-- ---------------------------------------------------------------------------
notify pgrst, 'reload schema';

-- ============================================================================
-- done. Verify:
--   select proname from pg_proc where proname like 'portal_%';
--   select tablename, policyname from pg_policies where tablename like 'portal_%';
--   select has_table_privilege('service_role','portal_proposals','select');   -- t
--   select has_table_privilege('authenticated','portal_revisions','select');  -- t
-- ============================================================================
