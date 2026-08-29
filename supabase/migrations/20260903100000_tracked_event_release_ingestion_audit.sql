-- Durable attribution for explicit operator-triggered tracked-event ingestion.
-- This is intentionally separate from provider ingestion runs: those rows describe
-- provider work, while this audit also records no-op already-analyzed requests.
create table public.tracked_event_release_ingestion_audit (
  id bigint generated always as identity primary key,
  tracked_event_id uuid not null,
  release_event_id text not null,
  actor text not null check (actor = btrim(actor) and length(actor) between 1 and 200),
  status text not null check (length(status) between 1 and 100),
  created_at timestamptz not null default now()
);

comment on table public.tracked_event_release_ingestion_audit is
  'Append-only attribution for authenticated explicit tracked-event release ingestion attempts.';

alter table public.tracked_event_release_ingestion_audit enable row level security;
revoke all on table public.tracked_event_release_ingestion_audit
  from public, anon, authenticated, service_role;
grant select on table public.tracked_event_release_ingestion_audit to service_role;

create or replace function public.record_tracked_event_release_ingestion_attempt(
  input_tracked_event_id uuid,
  input_release_event_id text,
  input_actor text,
  input_status text
) returns bigint
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  audit_id bigint;
begin
  if input_actor is null or input_actor <> btrim(input_actor)
     or length(input_actor) < 1 or length(input_actor) > 200 then
    raise exception 'invalid ingestion audit actor';
  end if;
  if input_release_event_id is null or btrim(input_release_event_id) = '' then
    raise exception 'invalid ingestion audit release identity';
  end if;
  if input_status is null or btrim(input_status) = '' or length(input_status) > 100 then
    raise exception 'invalid ingestion audit status';
  end if;

  insert into public.tracked_event_release_ingestion_audit (
    tracked_event_id, release_event_id, actor, status
  ) values (
    input_tracked_event_id, input_release_event_id, input_actor, input_status
  ) returning id into audit_id;
  return audit_id;
end;
$$;

revoke all on function public.record_tracked_event_release_ingestion_attempt(uuid, text, text, text)
  from public, anon, authenticated;
grant execute on function public.record_tracked_event_release_ingestion_attempt(uuid, text, text, text)
  to service_role;
