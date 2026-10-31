-- Manual migration for MySQL >= 8.0.16, using InnoDB. NOT run by the app.
-- First back up and stop all writers. Run these SELECTs and resolve every row
-- before executing the DDL section. Never silently discard duplicate stock.
SELECT product_id, COUNT(*) FROM inventory GROUP BY product_id HAVING COUNT(*) > 1;
SELECT * FROM inventory WHERE quantity IS NULL OR quantity < 0 OR product_id IS NULL;
SELECT * FROM inventory WHERE product_id NOT IN (SELECT product_id FROM products);
SELECT * FROM sales WHERE quantity <= 0;
SELECT name, category, supplier, expiry_date, COUNT(*) FROM products
WHERE category IS NOT NULL AND supplier IS NOT NULL AND expiry_date IS NOT NULL
GROUP BY name, category, supplier, expiry_date HAVING COUNT(*) > 1;
-- Inspect SHOW TABLE STATUS and SHOW CREATE TABLE for all affected tables.
-- DDL below must only be executed after preflight resolution and review.
-- MySQL DDL implicitly commits. This script is NOT atomic or rerunnable as-is.
ALTER TABLE products ADD COLUMN version INT NOT NULL DEFAULT 1,
  ADD CONSTRAINT uq_product_batch UNIQUE (name, category, supplier, expiry_date);
ALTER TABLE inventory MODIFY product_id INT NOT NULL,
  MODIFY quantity INT NOT NULL DEFAULT 0,
  ADD CONSTRAINT uq_inventory_product UNIQUE (product_id),
  ADD CONSTRAINT ck_inventory_nonnegative CHECK (quantity >= 0);
ALTER TABLE sales ADD CONSTRAINT ck_sale_positive CHECK (quantity > 0);
CREATE TABLE inventory_audit (
  audit_id INT AUTO_INCREMENT PRIMARY KEY,
  actor_id INT NOT NULL,
  product_id INT NOT NULL,
  action VARCHAR(30) NOT NULL,
  old_value JSON NULL,
  new_value JSON NULL,
  timestamp DATETIME NOT NULL,
  INDEX ix_inventory_audit_product_id (product_id)
) ENGINE=InnoDB;
