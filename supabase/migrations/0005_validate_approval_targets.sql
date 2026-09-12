-- ============================================================================
-- Vista Client Proposal Portal — 0005 · validate signed approvals at the DB layer
-- ----------------------------------------------------------------------------
-- portal_approvals.approval_token is free text. RLS (0003 pa_insert) only checks
-- the grant + signed_by; it does NOT check that the token is a real, published
-- approvable target for the exact revision being signed. That is fine for the
-- client UI (which only renders controls for snapshot.approvalTargets) but it is
-- NOT enough for signed audit evidence: a caller hitting PostgREST directly — or
-- a leaked service_role key, which bypasses RLS entirely — could insert a row
-- like {approval_token:'approval.fake', snapshot_hash:'...'} and it would be
-- accepted, hash-chained and shown as a genuine client signature.
--
-- This migration adds a BEFORE INSERT trigger on portal_approvals that rejects
-- the row unless every one of these holds:
--   * approval_token is present in the frozen approvalTargets of revision_id
--     (portal_revisions.snapshot_jsonb -> 'approvalTargets')
--   * approval_token is well-formed: approval.proposal  |  approval.item.<NN>  |
--     approval.item.<NN>.ci#<N>
--   * item_ref equals the item number embedded in the token (null for approval.proposal)
--   * scope = 'proposal' for approval.proposal, 'item' for approval.item.* (matches
--     the token grammar + the 0001 CHECK)
--   * revision_id exists and belongs to proposal_id
--   * snapshot_hash equals that revision's content_hash
--   * signed_by = auth.uid()
--   * the signer still holds an active 'approve' grant on the proposal
--
-- It does NOT change: the table, RLS, the Client Action approval mechanism, the
-- Sign & Submit flow, Vista Accept/Reject (which never touches approvals), the
-- audit hash chain, or revision immutability. Approvals stay append-only signed
-- evidence — this only gates what may become one.
--
-- Idempotent. Run AFTER 0001-0004. Existing portal_approvals rows are untouched
-- (BEFORE INSERT only); all current rows already satisfy these rules.
-- ============================================================================

create or replace function portal_approval_validate() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  rev            portal_revisions%rowtype;
  targets        jsonb;
  tok            text := new.approval_token;
  item_from_tok  text;
begin
  -- ── signer identity ─────────────────────────────────────────────────────
  if auth.uid() is null or new.signed_by is distinct from auth.uid() then
    raise exception 'portal_approvals: signed_by must be the authenticated user'
      using errcode = 'insufficient_privilege';
  end if;

  -- ── active approve grant on this proposal ───────────────────────────────
  if not portal_grant_flag(new.proposal_id, 'approve') then
    raise exception 'portal_approvals: no active approve grant on proposal %', new.proposal_id
      using errcode = 'insufficient_privilege';
  end if;

  -- ── the revision must exist and belong to proposal_id ───────────────────
  select * into rev from portal_revisions where id = new.revision_id;
  if not found then
    raise exception 'portal_approvals: revision % does not exist', new.revision_id;
  end if;
  if rev.proposal_id is distinct from new.proposal_id then
    raise exception 'portal_approvals: revision % does not belong to proposal %',
      new.revision_id, new.proposal_id;
  end if;

  -- ── snapshot_hash must match the frozen revision content hash ───────────
  if new.snapshot_hash is distinct from rev.content_hash then
    raise exception 'portal_approvals: snapshot_hash does not match revision % content_hash',
      rev.revision_label;
  end if;

  -- ── token shape ────────────────────────────────────────────────────────
  if tok is null
     or tok !~ '^approval\.(proposal|item\.[0-9]{1,3}(\.ci#[0-9]{1,3})?)$' then
    raise exception 'portal_approvals: malformed approval_token %', coalesce(tok, '(null)');
  end if;

  -- ── token must be a published approvable target of THIS revision ────────
  targets := coalesce(rev.snapshot_jsonb -> 'approvalTargets', '[]'::jsonb);
  if jsonb_typeof(targets) <> 'array' or not (targets ? tok) then
    raise exception 'portal_approvals: % is not an approvable target of revision %',
      tok, rev.revision_label;
  end if;

  -- ── item_ref + scope must match what the token grammar implies ──────────
  if tok = 'approval.proposal' then
    if new.item_ref is not null then
      raise exception 'portal_approvals: item_ref must be null for approval.proposal';
    end if;
    if new.scope is distinct from 'proposal' then
      raise exception 'portal_approvals: scope must be ''proposal'' for approval.proposal';
    end if;
  else
    item_from_tok := substring(tok from '^approval\.item\.([0-9]{1,3})');
    if new.item_ref is distinct from item_from_tok then
      raise exception 'portal_approvals: item_ref % does not match token % (expected item %)',
        coalesce(new.item_ref, '(null)'), tok, item_from_tok;
    end if;
    if new.scope is distinct from 'item' then
      raise exception 'portal_approvals: scope must be ''item'' for token %', tok;
    end if;
  end if;

  return new;
end $$;

revoke all on function portal_approval_validate() from public, anon;

drop trigger if exists portal_approval_validate_t on portal_approvals;
create trigger portal_approval_validate_t
  before insert on portal_approvals
  for each row execute function portal_approval_validate();

notify pgrst, 'reload schema';

-- ============================================================================
-- verify (run as service_role in the SQL editor):
--   -- a real published target of the current revision  -> should be REJECTED here
--   --   only because auth.uid() is null for service_role; from an authenticated
--   --   client with the approve grant it inserts fine.
--   -- a bogus token against a real revision:
--   select approval_token from portal_approvals limit 0;   -- table unchanged
--   select proname from pg_proc where proname = 'portal_approval_validate';           -- 1 row
--   select tgname from pg_trigger where tgname = 'portal_approval_validate_t';        -- 1 row
-- ============================================================================
