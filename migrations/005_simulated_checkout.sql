-- Apply once after a verified backup and inspection of apex_orders constraints.
BEGIN;

ALTER TABLE apex_orders ADD COLUMN simulated_method VARCHAR(16);
ALTER TABLE apex_orders ADD CONSTRAINT apex_orders_simulated_method_check
    CHECK (simulated_method IS NULL OR simulated_method IN ('tarjeta', 'transferencia', 'retiro'));

ALTER TABLE apex_orders DROP CONSTRAINT apex_orders_status_check;
ALTER TABLE apex_orders ADD CONSTRAINT apex_orders_status_check
    CHECK (status IN ('AWAITING_DEMO_PAYMENT', 'SIMULATED_COMPLETE', 'CANCELLED'));

COMMIT;
