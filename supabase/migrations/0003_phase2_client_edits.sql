-- ============================================================================
-- Vista Client Proposal Portal — 0003 · Phase 2: client edits + signed change sets
-- ----------------------------------------------------------------------------
-- Run AFTER 0001 and 0002. Idempotent — safe to re-run.
--
-- Adds:
--   * portal_change_sets            one signed client review session
--   * portal_change_requests.change_set_id / kind   (columns)
--   * portal_comments.change_set_id                 (column)
--   * portal_approvals.change_set_id                (column)
--   * BEFORE INSERT validation trigger on portal_change_requests
--       -> enforces the field-token whitelist against portal_field_defs
--       -> copies old_value + field_label from the frozen field def
--       -> validates new_value against the stored constraints
--   * lock triggers so a submitted change set / its rows are immutable to the client
--   * audit triggers -> portal_log_event for every client action
--   * RLS INSERT/SELECT policies for authenticated clients (own rows, granted proposal, right flag)
--
-- The client NEVER writes to portal_field_defs, portal_revisions, the snapshot,
-- or anything internal. Only a proposed change lands, always pending Vista review.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1 · portal_change_sets
-- ---------------------------------------------------------------------------
do $$ begin
  create type portal_change_set_status as enum
    ('draft','submitted','accepted','partially_accepted','rejected');
exception when duplicate_object then null; end $$;

create sequence if not exists portal_cs_seq;

create table if not exists portal_change_sets (
  id             uuid primary key default gen_random_uuid(),
  cs_number      text unique,                       -- 'CS-0004'
  proposal_id    uuid not null references portal_proposals(id) on delete cascade,
  revision_id    uuid not null references portal_revisions(id),
  client_user_id uuid not null references portal_client_users(id),
  status         portal_change_set_status not null default 'draft',
  -- signature (captured on submit)
  signer_name    text,
  signer_title   text,
  signer_company text,
  signature_type text check (signature_type in ('typed','drawn')),
  signature_data text,                              -- typed name, or data:image/png;base64,... for drawn
  confirmed      boolean not null default false,    -- the "I confirm ..." checkbox
  signed_at      timestamptz,
  ip             inet,
  user_agent     text,
  -- lifecycle
  created_at     timestamptz not null default now(),
  submitted_at   timestamptz,
  decided_at     timestamptz,
  decided_by     text,
  decided_by_name text
);
create index if not exists portal_cs_proposal on portal_change_sets(proposal_id, status);
create index if not exists portal_cs_user on portal_change_sets(client_user_id);

-- ---------------------------------------------------------------------------
-- 2 · columns on existing client-data tables
-- ---------------------------------------------------------------------------
alter table portal_change_requests add column if not exists change_set_id uuid references portal_change_sets(id) on delete cascade;
alter table portal_change_requests add column if not exists kind text not null default 'field';   -- 'field' | 'input_response' | 'client_action'
create index if not exists portal_cr_change_set on portal_change_requests(change_set_id);

alter table portal_comments  add column if not exists change_set_id uuid references portal_change_sets(id) on delete set null;
alter table portal_approvals add column if not exists change_set_id uuid references portal_change_sets(id) on delete set null;

-- ---------------------------------------------------------------------------
-- 3 · cs_number generator
-- ---------------------------------------------------------------------------
create or replace function portal_cs_number() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.cs_number is null then
    new.cs_number := 'CS-' || lpad(nextval('portal_cs_seq')::text, 4, '0');
  end if;
  return new;
end $$;
drop trigger if exists portal_cs_number_t on portal_change_sets;
create trigger portal_cs_number_t before insert on portal_change_sets
  for each row execute function portal_cs_number();

-- ---------------------------------------------------------------------------
-- 4 · WHITELIST validation — the security core
--     A proposed change is only accepted if portal_field_defs has a matching
--     row for (revision_id, field_token) with permission = 'propose'.
--     old_value + field_label + constraints come from that frozen row, never
--     from the browser. new_value is range/length/type checked here.
-- ---------------------------------------------------------------------------
create or replace function portal_cr_validate() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  fd            portal_field_defs%rowtype;
  cs            portal_change_sets%rowtype;
  c             jsonb;
  nv_text       text;
  nv_num        numeric;
begin
  -- the change set must exist, belong to the caller, be a draft, same proposal+revision
  select * into cs from portal_change_sets where id = new.change_set_id;
  if not found then
    raise exception 'change_set % not found', new.change_set_id;
  end if;
  if cs.client_user_id <> auth.uid() then
    raise exception 'change set belongs to another user';
  end if;
  if cs.status <> 'draft' then
    raise exception 'change set % is already %', cs.cs_number, cs.status;
  end if;
  new.proposal_id := cs.proposal_id;
  new.revision_id := cs.revision_id;
  new.submitted_by := auth.uid();

  -- the whitelist gate
  select * into fd from portal_field_defs
    where revision_id = cs.revision_id and field_token = new.field_token;
  if not found then
    raise exception 'field_token % is not part of revision %', new.field_token, cs.revision_id;
  end if;
  if fd.permission <> 'propose' then
    raise exception 'field_token % is % only, not proposable', new.field_token, fd.permission;
  end if;

  -- authoritative old_value + label from the frozen def (ignore anything the client sent)
  new.old_value := fd.current_value;
  new.field_label := fd.label;
  new.item_ref := fd.item_ref;
  new.constraints_snapshot := fd.constraints;
  new.state := 'pending';

  -- validate new_value against constraints
  c := fd.constraints;
  if fd.data_type = 'integer' then
    begin nv_num := (new.new_value #>> '{}')::numeric; exception when others then
      raise exception '% must be a number', new.field_token; end;
    if nv_num <> trunc(nv_num) then raise exception '% must be a whole number', new.field_token; end if;
    if c ? 'min' and nv_num < (c->>'min')::numeric then raise exception '% below minimum %', new.field_token, c->>'min'; end if;
    if c ? 'max' and nv_num > (c->>'max')::numeric then raise exception '% above maximum %', new.field_token, c->>'max'; end if;
    new.new_value := to_jsonb(nv_num::bigint);
  else
    nv_text := coalesce(new.new_value #>> '{}', '');
    if c ? 'maxLength' and length(nv_text) > (c->>'maxLength')::int then
      raise exception '% exceeds % characters', new.field_token, c->>'maxLength'; end if;
    new.new_value := to_jsonb(nv_text);
  end if;

  -- a proposed value identical to the current one is pointless
  if new.new_value is not distinct from new.old_value then
    raise exception 'proposed value for % is identical to the current value', new.field_token;
  end if;

  return new;
end $$;
drop trigger if exists portal_cr_validate_t on portal_change_requests;
create trigger portal_cr_validate_t before insert on portal_change_requests
  for each row execute function portal_cr_validate();

-- ---------------------------------------------------------------------------
-- 5 · lock triggers  (client cannot alter a submitted set or its rows;
--     Vista decisions move state forward only)
-- ---------------------------------------------------------------------------
create or replace function portal_cs_guard() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    if old.status <> 'draft' then raise exception 'a submitted change set cannot be deleted'; end if;
    return old;
  end if;
  -- draft -> submitted : allowed once, stamps signature
  if old.status = 'draft' and new.status = 'submitted' then
    if not new.confirmed then raise exception 'confirmation checkbox is required'; end if;
    if coalesce(new.signer_name,'') = '' then raise exception 'signer name is required'; end if;
    new.submitted_at := coalesce(new.submitted_at, now());
    new.signed_at := coalesce(new.signed_at, now());
    return new;
  end if;
  -- no other change may touch a non-draft set's signature / identity
  if old.status <> 'draft' then
    if (new.signer_name, new.signer_title, new.signer_company, new.signature_data,
        new.signature_type, new.signed_at, new.submitted_at, new.client_user_id,
        new.revision_id, new.proposal_id)
       is distinct from
       (old.signer_name, old.signer_title, old.signer_company, old.signature_data,
        old.signature_type, old.signed_at, old.submitted_at, old.client_user_id,
        old.revision_id, old.proposal_id) then
      raise exception 'signed change set is immutable';
    end if;
    -- only forward transitions on status
    if new.status <> old.status
       and not (old.status = 'submitted'
                and new.status in ('accepted','partially_accepted','rejected')) then
      raise exception 'invalid change-set transition % -> %', old.status, new.status;
    end if;
  end if;
  return new;
end $$;
drop trigger if exists portal_cs_guard_t on portal_change_sets;
create trigger portal_cs_guard_t before update or delete on portal_change_sets
  for each row execute function portal_cs_guard();

-- reuse/replace the 0001 change-request guard so change_set_id is also frozen
create or replace function portal_cr_guard() returns trigger
language plpgsql as $$
begin
  if (old.field_token, old.revision_id, old.old_value, old.new_value, old.submitted_by, old.change_set_id)
     is distinct from
     (new.field_token, new.revision_id, new.old_value, new.new_value, new.submitted_by, new.change_set_id) then
     raise exception 'portal_change_requests: core fields are immutable';
  end if;
  if old.state <> 'pending' and new.state <> old.state then
     raise exception 'portal_change_requests: % is terminal', old.state;
  end if;
  return new;
end $$;
-- (trigger portal_cr_guard_t already created in 0001)
drop trigger if exists portal_cr_guard_t on portal_change_requests;
create trigger portal_cr_guard_t before update on portal_change_requests
  for each row execute function portal_cr_guard();

-- ---------------------------------------------------------------------------
-- 6 · audit — every client action is logged (append-only, hash-chained)
-- ---------------------------------------------------------------------------
create or replace function portal_audit_client_action() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  nm text; em text; ev text; pid uuid; rid uuid; note text;
begin
  select full_name, email into nm, em from portal_client_users where id = auth.uid();
  if tg_table_name = 'portal_change_sets' then
    if tg_op = 'INSERT' then return new; end if;                       -- draft creation: no event
    if new.status = 'submitted' and old.status = 'draft' then
      ev := 'change_set.submitted'; pid := new.proposal_id; rid := new.revision_id;
      note := new.cs_number || ' signed by ' || coalesce(new.signer_name,'');
      perform portal_log_event(pid, rid, 'client', auth.uid()::text, coalesce(nm,em,'client'),
        ev, 'change_set', new.id::text, null, null, to_jsonb(new.status), note,
        new.ip, new.user_agent, null);
    end if;
    return new;
  elsif tg_table_name = 'portal_change_requests' then
    ev := 'change_request.submitted'; pid := new.proposal_id; rid := new.revision_id;
    perform portal_log_event(pid, rid, 'client', auth.uid()::text, coalesce(nm,em,'client'),
      ev, 'change_request', new.id::text, new.field_token, new.old_value, new.new_value,
      new.field_label, null, null, new.change_set_id::text);
    return new;
  elsif tg_table_name = 'portal_comments' then
    ev := 'comment.added'; pid := new.proposal_id; rid := new.revision_id;
    perform portal_log_event(pid, rid, new.author_type, new.author_id, new.author_name,
      ev, 'comment', new.id::text, new.target_token, null, to_jsonb(new.body), null,
      null, null, new.change_set_id::text);
    return new;
  elsif tg_table_name = 'portal_approvals' then
    ev := (case when new.decision = 'declined' then 'approval.declined' else 'approval.signed' end);
    pid := new.proposal_id; rid := new.revision_id;
    perform portal_log_event(pid, rid, 'client', new.signed_by::text, new.signed_name,
      ev, 'approval', new.id::text, new.approval_token, null, to_jsonb(new.decision),
      new.statement_shown, new.ip, new.user_agent, new.change_set_id::text);
    return new;
  end if;
  return new;
end $$;

drop trigger if exists portal_cs_audit_t on portal_change_sets;
create trigger portal_cs_audit_t after update on portal_change_sets
  for each row execute function portal_audit_client_action();
drop trigger if exists portal_cr_audit_t on portal_change_requests;
create trigger portal_cr_audit_t after insert on portal_change_requests
  for each row execute function portal_audit_client_action();
drop trigger if exists portal_comments_audit_t on portal_comments;
create trigger portal_comments_audit_t after insert on portal_comments
  for each row execute function portal_audit_client_action();
drop trigger if exists portal_approvals_audit_t on portal_approvals;
create trigger portal_approvals_audit_t after insert on portal_approvals
  for each row execute function portal_audit_client_action();

-- ---------------------------------------------------------------------------
-- 7 · grants
-- ---------------------------------------------------------------------------
grant select, insert, update on portal_change_sets to authenticated;
grant select, insert on portal_change_requests to authenticated;
grant select, insert on portal_comments to authenticated;
grant select, insert on portal_approvals to authenticated;

grant select, insert, update, delete on portal_change_sets to service_role;
grant usage, select on sequence portal_cs_seq to service_role;

-- ---------------------------------------------------------------------------
-- 8 · RLS enable + policies
-- ---------------------------------------------------------------------------
alter table portal_change_sets enable row level security;
revoke all on portal_change_sets from anon;

-- change sets: see / create / submit your own on a granted proposal
drop policy if exists pcs_read on portal_change_sets;
create policy pcs_read on portal_change_sets for select to authenticated
  using (client_user_id = auth.uid() and portal_has_grant(proposal_id));
drop policy if exists pcs_insert on portal_change_sets;
create policy pcs_insert on portal_change_sets for insert to authenticated
  with check (client_user_id = auth.uid() and portal_has_grant(proposal_id) and status = 'draft');
drop policy if exists pcs_update on portal_change_sets;
create policy pcs_update on portal_change_sets for update to authenticated
  using (client_user_id = auth.uid() and portal_has_grant(proposal_id))
  with check (client_user_id = auth.uid());

-- change requests: read your own; insert requires a draft set you own + can_propose
drop policy if exists pcr_read on portal_change_requests;
create policy pcr_read on portal_change_requests for select to authenticated
  using (submitted_by = auth.uid() and portal_has_grant(proposal_id));
drop policy if exists pcr_insert on portal_change_requests;
create policy pcr_insert on portal_change_requests for insert to authenticated
  with check (
    portal_has_grant(proposal_id)
    and portal_grant_flag(proposal_id,'propose')
    and exists (select 1 from portal_change_sets cs
                where cs.id = change_set_id and cs.client_user_id = auth.uid() and cs.status = 'draft'));

-- comments: read all non-hidden on a granted proposal; insert your own client comment
drop policy if exists pc_read on portal_comments;
create policy pc_read on portal_comments for select to authenticated
  using (portal_has_grant(proposal_id) and hidden_at is null);
drop policy if exists pc_insert on portal_comments;
create policy pc_insert on portal_comments for insert to authenticated
  with check (
    portal_has_grant(proposal_id)
    and portal_grant_flag(proposal_id,'comment')
    and author_type = 'client'
    and author_id = auth.uid()::text);

-- approvals: read your own; insert your own with can_approve
drop policy if exists pa_read on portal_approvals;
create policy pa_read on portal_approvals for select to authenticated
  using (signed_by = auth.uid() and portal_has_grant(proposal_id));
drop policy if exists pa_insert on portal_approvals;
create policy pa_insert on portal_approvals for insert to authenticated
  with check (
    portal_has_grant(proposal_id)
    and portal_grant_flag(proposal_id,'approve')
    and signed_by = auth.uid());

notify pgrst, 'reload schema';

-- ============================================================================
-- verify:
--   select proname from pg_proc where proname like 'portal_%';
--   select tablename, policyname, cmd from pg_policies where tablename like 'portal_%' order by 1,3;
--   select has_table_privilege('authenticated','portal_change_sets','insert');   -- t
-- ============================================================================
