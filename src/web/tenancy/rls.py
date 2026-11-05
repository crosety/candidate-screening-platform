from django.db import migrations


def enable_rls(table: str) -> migrations.RunSQL:
    return migrations.RunSQL(
        sql=f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {table}
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
""",
        reverse_sql=f"""
DROP POLICY IF EXISTS tenant_isolation ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
""",
    )


def make_append_only(table: str) -> migrations.RunSQL:
    fn = f"{table}_append_only"
    return migrations.RunSQL(
        sql=f"""
CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'append-only table';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER {fn}_trg BEFORE UPDATE OR DELETE ON {table}
FOR EACH ROW EXECUTE FUNCTION {fn}();
""",
        reverse_sql=f"DROP TRIGGER IF EXISTS {fn}_trg ON {table}; DROP FUNCTION IF EXISTS {fn}();",
    )
