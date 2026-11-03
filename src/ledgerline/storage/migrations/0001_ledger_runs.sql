-- Apply with a dedicated migration role, never from a Vercel request handler.
CREATE TABLE IF NOT EXISTS ledger_runs (
    seq BIGINT PRIMARY KEY CHECK (seq >= 0),
    run_id TEXT NOT NULL UNIQUE CHECK (length(trim(run_id)) > 0),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    payload_canonical TEXT NOT NULL,
    prev_hash CHAR(64) NOT NULL,
    record_hash CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ledger_runs_created_at_idx ON ledger_runs (created_at DESC);

CREATE TABLE IF NOT EXISTS ledger_chain_head (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    last_seq BIGINT NOT NULL CHECK (last_seq >= 0),
    last_hash CHAR(64) NOT NULL
);

CREATE OR REPLACE FUNCTION ledgerline_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ledger_runs is append-only';
END;
$$;
DROP TRIGGER IF EXISTS ledger_runs_no_update ON ledger_runs;
CREATE TRIGGER ledger_runs_no_update BEFORE UPDATE ON ledger_runs
FOR EACH ROW EXECUTE FUNCTION ledgerline_reject_mutation();
DROP TRIGGER IF EXISTS ledger_runs_no_delete ON ledger_runs;
CREATE TRIGGER ledger_runs_no_delete BEFORE DELETE ON ledger_runs
FOR EACH ROW EXECUTE FUNCTION ledgerline_reject_mutation();

-- Grant SELECT/INSERT on ledger_runs and SELECT/INSERT/UPDATE on ledger_chain_head
-- to a narrowly scoped runtime role. Do not grant UPDATE/DELETE/TRUNCATE on ledger_runs.
-- Keep migration ownership and credentials separate from the runtime role.
