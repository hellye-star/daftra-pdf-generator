-- ============================================================================
-- Vista Client Proposal Portal — 0001 · foundation
-- ----------------------------------------------------------------------------
-- Schema, enums, indexes, immutability + integrity triggers.
-- No RLS, no Storage, no RPC — those are in 0002_phase1_rls_storage.sql.
-- Idempotent: safe to run more than once.
-- Run in: Supabase Dashboard -> SQL Editor -> paste -> Run.
-- ============================================================================

create extension if not exists "pgcrypto";   -- gen_random_uuid(), digest()
create extension if not exists "citext";

-- ---------------------------------------------------------------------------
-- 1 · enums
-- ---------------------------------------------------------------------------
do $$ begin create type portal_user_role         as enum ('viewer','approver');                                        exception when duplicate_object then null; end $$;
do $$ begin create type portal_user_status       as enum ('invited','active','disabled');                              exception when duplicate_object then null; end $$;
do $$ begin create type portal_proposal_status   as enum ('active','closed');                                          exception when duplicate_object then null; end $$;
do $$ begin create type portal_permission        as enum ('view','comment','propose','approve');                       exception when duplicate_object then null; end $$;
do $$ begin create type portal_change_state      as enum ('pending','accepted','rejected','withdrawn','superseded');    exception when duplicate_object then null; end $$;
do $$ begin create type portal_approval_decision as enum ('approved','approved_with_comments','declined');             exception when duplicate_object then null; end $$;
do $$ begin create type portal_actor_type        as enum ('client','vista','system');                                  exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------------------
-- 2 · identity & access
-- ---------------------------------------------------------------------------
create table if not exists portal_clients (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  created_at  timestamptz not null default now(),
  created_by  text not null                       -- vista identity email
);

create table if not exists portal_client_users (
  id           uuid primary key,                  -- == auth.users.id
  client_id    uuid not null references portal_clients(id) on delete restrict,
  email        citext not null unique,
  full_name    text not null,
  role         portal_user_role   not null default 'viewer',
  status       portal_user_status not null default 'invited',
  invited_at   timestamptz not null default now(),
  invited_by   text not null,
  last_seen_at timestamptz
);
create index if not exists portal_client_users_client on portal_client_users(client_id);

create table if not exists portal_proposals (
  id                  uuid primary key default gen_random_uuid(),
  internal_project_id text not null unique,        -- Vista project.id (bridge key)
  client_id           uuid not null references portal_clients(id) on delete restrict,
  title               text not null,
  current_revision_id uuid,                        -- FK added after portal_revisions (below)
  status              portal_proposal_status not null default 'active',
  created_at          timestamptz not null default now(),
  created_by          text not null
);

create table if not exists portal_proposal_grants (
  proposal_id    uuid not null references portal_proposals(id) on delete cascade,
  client_user_id uuid not null references portal_client_users(id) on delete cascade,
  can_comment    boolean not null default true,
  can_propose    boolean not null default false,
  can_approve    boolean not null default false,
  granted_by     text not null,
  granted_at     timestamptz not null default now(),
  revoked_at     timestamptz,
  primary key (proposal_id, client_user_id)
);
create index if not exists portal_grants_user on portal_proposal_grants(client_user_id) where revoked_at is null;

-- ---------------------------------------------------------------------------
-- 3 · immutable published content
-- ---------------------------------------------------------------------------
create table if not exists portal_revisions (
  id                     uuid primary key default gen_random_uuid(),
  proposal_id            uuid not null references portal_proposals(id) on delete cascade,
  revision_label         text not null,           -- 'R1','R2','R2-b'
  snapshot_schema        int  not null default 1,
  snapshot_jsonb         jsonb not null,          -- client-safe projection
  content_hash           text not null,           -- sha256 of canonical(snapshot_jsonb)
  supersedes_revision_id uuid references portal_revisions(id),
  published_at           timestamptz not null default now(),
  published_by           text not null,           -- vista identity email
  published_by_name      text not null,
  unique (proposal_id, revision_label)
);
create index if not exists portal_revisions_proposal on portal_revisions(proposal_id, published_at desc);

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'portal_proposals_current_rev_fk') then
    alter table portal_proposals
      add constraint portal_proposals_current_rev_fk
      foreign key (current_revision_id) references portal_revisions(id);
  end if;
end $$;

create table if not exists portal_revision_photos (
  id           uuid primary key default gen_random_uuid(),
  revision_id  uuid not null references portal_revisions(id) on delete cascade,
  item_ref     text not null,                     -- item.number
  slot         text not null check (slot in ('existing','simulation','sample')),
  ordinal      int  not null default 0,
  storage_path text not null,                     -- key within the 'proposal-photos' bucket
  mime         text,
  w            int,
  h            int,
  sha256       text not null,
  unique (revision_id, storage_path)
);
create index if not exists portal_rev_photos_rev on portal_revision_photos(revision_id);

-- the client-editable-surface registry; generated at publish, frozen
create table if not exists portal_field_defs (
  revision_id   uuid not null references portal_revisions(id) on delete cascade,
  field_token   text not null,                    -- enumerated token (see Phase 0 spec §9)
  permission    portal_permission not null,
  data_type     text not null,                    -- 'integer'|'string'|'email'|'phone'
  label         text not null,
  item_ref      text,
  current_value jsonb,                            -- value in this revision (old_value source)
  constraints   jsonb not null default '{}'::jsonb,
  primary key (revision_id, field_token)
);
create index if not exists portal_field_defs_rev on portal_field_defs(revision_id);

-- ---------------------------------------------------------------------------
-- 4 · client-generated data
-- ---------------------------------------------------------------------------
create table if not exists portal_comments (
  id           uuid primary key default gen_random_uuid(),
  proposal_id  uuid not null references portal_proposals(id) on delete cascade,
  revision_id  uuid not null references portal_revisions(id),
  target_token text not null,                     -- client-facing comment target
  item_ref     text,
  body         text not null check (length(body) between 1 and 8000),
  author_type  portal_actor_type not null,
  author_id    text not null,                     -- client_user.id::text | vista email
  author_name  text not null,
  parent_id    uuid references portal_comments(id),
  created_at   timestamptz not null default now(),
  resolved_at  timestamptz,
  resolved_by  text,
  hidden_at    timestamptz                        -- moderation only; never hard-deleted
);
create index if not exists portal_comments_proposal on portal_comments(proposal_id, created_at);

create table if not exists portal_change_requests (
  id                       uuid primary key default gen_random_uuid(),
  proposal_id              uuid not null references portal_proposals(id) on delete cascade,
  revision_id              uuid not null references portal_revisions(id),
  field_token              text not null,         -- MUST match a portal_field_defs row, permission='propose'
  item_ref                 text,
  field_label              text not null,
  old_value                jsonb,                 -- copied from portal_field_defs.current_value at submit
  new_value                jsonb not null,
  constraints_snapshot     jsonb not null,
  client_note              text check (client_note is null or length(client_note) <= 2000),
  submitted_by             uuid not null references portal_client_users(id),
  submitted_by_name        text not null,
  submitted_at             timestamptz not null default now(),
  state                    portal_change_state not null default 'pending',
  decided_by               text,
  decided_by_name          text,
  decided_at               timestamptz,
  decision_note            text,
  applied_to_internal_at   timestamptz,
  superseded_by_revision_id uuid references portal_revisions(id)
);
create index if not exists portal_cr_proposal_pending on portal_change_requests(proposal_id) where state = 'pending';
create index if not exists portal_cr_submitter on portal_change_requests(submitted_by, submitted_at desc);

create table if not exists portal_approvals (
  id              uuid primary key default gen_random_uuid(),
  proposal_id     uuid not null references portal_proposals(id) on delete cascade,
  revision_id     uuid not null references portal_revisions(id),
  approval_token  text not null,                  -- 'approval.item.<ref>' | 'approval.proposal'
  scope           text not null check (scope in ('item','proposal')),
  item_ref        text,
  decision        portal_approval_decision not null,
  signed_name     text not null,
  signed_by       uuid not null references portal_client_users(id),
  signed_by_email citext not null,
  signed_at       timestamptz not null default now(),
  ip              inet,
  user_agent      text,
  statement_shown text not null,                  -- exact wording the client saw
  statement_lang  text not null default 'en',
  snapshot_hash   text not null                   -- == portal_revisions.content_hash at signing
);
create index if not exists portal_approvals_rev on portal_approvals(revision_id, approval_token, signed_at desc);

-- ---------------------------------------------------------------------------
-- 5 · append-only audit
-- ---------------------------------------------------------------------------
create table if not exists portal_audit_events (
  id           bigint generated always as identity primary key,
  proposal_id  uuid references portal_proposals(id) on delete set null,
  revision_id  uuid references portal_revisions(id),
  actor_type   portal_actor_type not null,
  actor_id     text not null,                     -- client_user.id::text | vista email | 'system'
  actor_name   text not null,
  event_type   text not null,
  object_type  text,
  object_id    text,
  field_token  text,
  old_value    jsonb,
  new_value    jsonb,
  note         text,
  ip           inet,
  user_agent   text,
  request_id   text,
  prev_hash    text,                              -- per-proposal hash chain
  row_hash     text,
  created_at   timestamptz not null default now()
);
create index if not exists portal_audit_proposal   on portal_audit_events(proposal_id, created_at);
create index if not exists portal_audit_event_type on portal_audit_events(event_type, created_at);

-- ---------------------------------------------------------------------------
-- 6 · immutability & integrity triggers
--     (service_role bypasses RLS but NOT triggers -> these bind everyone)
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

-- change requests: core columns frozen post-insert; terminal states cannot re-open
create or replace function portal_cr_guard() returns trigger
language plpgsql as $$
begin
  if (old.field_token, old.revision_id, old.old_value, old.new_value, old.submitted_by)
     is distinct from
     (new.field_token, new.revision_id, new.old_value, new.new_value, new.submitted_by) then
     raise exception 'portal_change_requests: core fields are immutable';
  end if;
  if old.state <> 'pending' and new.state <> old.state then
     raise exception 'portal_change_requests: % is terminal', old.state;
  end if;
  return new;
end $$;
drop trigger if exists portal_cr_guard_t on portal_change_requests;
create trigger portal_cr_guard_t before update on portal_change_requests
  for each row execute function portal_cr_guard();

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

-- ============================================================================
-- done. Next: run 0002_phase1_rls_storage.sql for RLS, the private Storage
-- bucket, and portal_log_event().
-- Verify tables:  select table_name from information_schema.tables
--                 where table_schema='public' and table_name like 'portal_%' order by 1;
-- ============================================================================
