-- Stores a deterministic checksum of the normalized instrument fields.
-- The application uses it to skip unchanged instruments during a CSV sync.
alter table public.master_instruments
    add column if not exists source_hash text;
