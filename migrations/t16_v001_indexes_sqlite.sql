-- Additive, manually reviewed T16 indexes. Never run at application startup.
CREATE INDEX ix_t16_sale_branch_id_date ON priced_sales (branch_id, sale_id, business_date, total_minor);
CREATE INDEX ix_t16_observation_run_branch_id_date ON sales_observations (run_id, branch_id, observation_id, business_date);
