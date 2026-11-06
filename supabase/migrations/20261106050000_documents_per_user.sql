-- =====================================================================
-- 교안을 계정별로 나누기
--   한 계정에서 올리거나 가져온 교안(documents·document_chunks)은 그 계정에서만 보인다.
--   · 소유자 = documents.created_by (처음부터 있던 칸)
--   · 의미 검색 함수에 소유자 조건(filter_owner) 추가 — 비우면 아무것도 돌려주지 않는다
--   · 브라우저용 키로 읽을 때도 본인 교안만 (RLS)
--   · 같은 LMS 자료는 계정마다 한 번만 가져오기 (created_by + source_url)
--   다시 실행해도 안전하다.
-- =====================================================================

create index if not exists idx_documents_created_by on public.documents (created_by, created_at desc);

create unique index if not exists uq_documents_owner_source
  on public.documents (created_by, source_url)
  where source_url is not null;

-- ---------------------------------------------------------------------
-- 의미 검색: 소유자 조건 추가 (예전 4개 인자 함수는 지운다)
-- ---------------------------------------------------------------------
drop function if exists public.match_document_chunks(extensions.vector, int, float, uuid);

create or replace function public.match_document_chunks(
  query_embedding     extensions.vector(1536),
  match_count         int default 5,
  match_threshold     float default 0.0,
  filter_document_id  uuid default null,
  filter_owner        uuid default null      -- 이 사용자의 교안에서만 찾는다 (비우면 결과 없음)
)
returns table (
  chunk_id        uuid,
  document_id     uuid,
  document_title  text,
  chunk_index     int,
  page_number     int,
  content         text,
  metadata        jsonb,
  similarity      float
)
language sql
stable
set search_path = public, extensions
as $$
  select
    c.id,
    c.document_id,
    d.title,
    c.chunk_index,
    c.page_number,
    c.content,
    c.metadata,
    1 - (c.embedding <=> query_embedding) as similarity
  from public.document_chunks c
  join public.documents d on d.id = c.document_id
  where c.embedding is not null
    and filter_owner is not null
    and d.created_by = filter_owner
    and (filter_document_id is null or c.document_id = filter_document_id)
    and 1 - (c.embedding <=> query_embedding) >= match_threshold
  order by c.embedding <=> query_embedding
  limit greatest(1, least(match_count, 50));
$$;

revoke execute on function public.match_document_chunks(extensions.vector, int, float, uuid, uuid) from anon, public;
grant  execute on function public.match_document_chunks(extensions.vector, int, float, uuid, uuid) to authenticated, service_role;

-- ---------------------------------------------------------------------
-- RLS: 로그인 사용자도 본인 교안만 읽기 (서버는 secret 키 + 코드에서 소유자 검사)
-- ---------------------------------------------------------------------
drop policy if exists "documents_read_authenticated" on public.documents;
drop policy if exists "documents_read_own" on public.documents;
create policy "documents_read_own" on public.documents
  for select to authenticated using (created_by = (select auth.uid()));

drop policy if exists "chunks_read_authenticated" on public.document_chunks;
drop policy if exists "chunks_read_own" on public.document_chunks;
create policy "chunks_read_own" on public.document_chunks
  for select to authenticated using (
    exists (
      select 1 from public.documents d
       where d.id = document_chunks.document_id
         and d.created_by = (select auth.uid())
    )
  );
