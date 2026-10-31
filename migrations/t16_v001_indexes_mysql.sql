-- Offline MySQL 8 / InnoDB artifact; live EXPLAIN/isolation verification pending.
-- Requires separately approved T08 and T14 expansion; no automatic execution.
CREATE INDEX ix_t16_sale_branch_id_date ON priced_sales (branch_id, sale_id, business_date, total_minor);
CREATE INDEX ix_t16_observation_run_branch_id_date ON sales_observations (run_id, branch_id, observation_id, business_date);
