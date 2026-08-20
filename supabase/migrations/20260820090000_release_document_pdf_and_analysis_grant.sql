-- Version the two production hotfixes required by the Hays results runtime.
-- Preserve every existing source_type rule while admitting PDF company results.
do $$
declare
  item record;
begin
  for item in
    select c.conname, pg_get_expr(c.conbin, c.conrelid) as expression
    from pg_constraint c
    where c.conrelid = 'public.event_source_documents'::regclass
      and c.contype = 'c'
      and pg_get_expr(c.conbin, c.conrelid) ilike '%source_type%'
  loop
    execute format(
      'alter table public.event_source_documents drop constraint %I', item.conname
    );
    execute format(
      'alter table public.event_source_documents add constraint %I check ((%s) or source_type = %L)',
      item.conname,
      item.expression,
      'company_results_pdf'
    );
  end loop;
end $$;

grant update on table public.event_ai_analyses to service_role;
