-- Version 002. Apply manually after the base schema and migration 001.
-- Existing FAVORITOS tables are preserved; the unique index guarantees
-- idempotent inserts for the (user, product) pair.
BEGIN;

CREATE TABLE IF NOT EXISTS favoritos (
    id_usuario CHAR(16) NOT NULL REFERENCES usuarios(id_usuario) ON DELETE CASCADE,
    id_producto CHAR(10) NOT NULL REFERENCES productos(id_producto) ON DELETE CASCADE,
    fav_fecha TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS favoritos_usuario_producto_uidx
    ON favoritos (id_usuario, id_producto);

COMMIT;
