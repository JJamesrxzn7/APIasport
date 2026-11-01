# Apex Sports API

FastAPI service used by the storefront and the administration panel. API routes are versioned under `/api/v1`; interactive OpenAPI documentation is available at `/docs`.

## Local setup

Requirements: Python 3.11+ and PostgreSQL. From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r app\requirements.txt
# Optional test tooling:
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require"
$env:CORS_ORIGINS = '["http://localhost:5500","http://127.0.0.1:5500","http://localhost:5501","http://127.0.0.1:5501","http://localhost:5502","http://127.0.0.1:5502"]'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Set environment variables in the process/service manager. The API does **not** implicitly load a local `.env`; `APEX_ENV_FILE` can be explicitly set by an operator if file-based settings are required. Never commit credentials. Configure `APEX_API_BASE` in either interface before its scripts load if the API is not at `http://127.0.0.1:8000/api/v1`.

Both GitHub Pages project URLs (`/E-commerce/` and `/Administracion-ecommerce/`) have the same browser origin: `https://jjamesrxzn7.github.io`. If keeping these URLs, set `CORS_ORIGINS` to that exact origin (without a path); CORS cannot distinguish the two projects. This works for the school demo, but both interfaces share the browser same-origin security boundary: JavaScript served by either project can access same-origin browser storage. Keep tokens short-lived, avoid untrusted scripts, and understand that CORS is not authentication. For stronger isolation, publish the interfaces under separate origins (for example separate GitHub Pages owners/organizations) and allow each exact origin. Never use `"*"`.

## Deployment for GitHub Pages

The clients default to `http://127.0.0.1:8000/api/v1`; GitHub Pages cannot use that URL. After deploying the API, configure `window.APEX_API_BASE` to the HTTPS API URL **before** `api-client.js` loads in both `E-commerce/` and `Administracion-ecommerce/`, for example with a small `api-config.js` loaded first:

```js
window.APEX_API_BASE = "https://YOUR-API-SERVICE.onrender.com/api/v1";
```

Commit and publish that non-secret URL with each static site. Never put `DATABASE_URL`, passwords, or other server secrets in frontend files.

For a Render Web Service:

- Build command: `pip install -r app/requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment variables: `DATABASE_URL` (Supabase PostgreSQL connection string with SSL) and `CORS_ORIGINS=["https://jjamesrxzn7.github.io"]`
- Do not upload `.env`, `.venv/`, or database credentials to GitHub. Create a GitHub repository for the API (or configure Render's root directory to the API subfolder in a monorepo).

Before deploying, apply the supplied base schema and `migrations/001_api_sessions_catalog_orders.sql` manually to the intended Supabase project, then provision roles, cities, products, and product/category mappings as documented above. Verify `GET /health/live`, `GET /health/ready`, `/docs`, and storefront/admin login against the deployed URL. Do not run migration SQL against production until you have a backup and have reviewed the target schema.

### Render Blueprint (free demo)

This project includes `render.yaml` to define a free Render web service. To deploy it:

1. Create a GitHub repository for **this API project only** and push the contents of `API-ApexSports/` to its root. Check that `.env`, `.venv/`, and local data are not included.
2. In Render, choose **New → Blueprint**, connect that repository, and review the service named `apex-sports-api`.
3. Before creating the service, prepare the Supabase schema and manual API migration using the database setup instructions above. Keep a backup and never paste database credentials into GitHub.
4. In Render, set the requested `DATABASE_URL` secret to the Supabase PostgreSQL connection string with SSL. The blueprint sets the exact GitHub Pages CORS origin.
5. Deploy. Wait for the health check to pass, then copy the assigned `https://...onrender.com` service URL.
6. In both frontend projects, set `window.APEX_API_BASE` to `https://...onrender.com/api/v1` in a config script loaded before `api-client.js`, publish both Pages updates, and test registration/login/catalog/cart/checkout.

Do not use the example database URL from `.env.example`; enter the real secret only in Render's environment settings. The Pages config contains only the public API URL, never `DATABASE_URL` or credentials.

### Availability and free plans

Free hosting is suitable for a school demo, not continuous 24/7 availability. Render's free web services spin down after 15 minutes without inbound traffic and can take about a minute to start again. Free Render Postgres expires 30 days after creation. Supabase Free projects pause after one week of inactivity. Therefore, for a service expected to stay warm and a database that does not pause due to inactivity, choose paid always-on API compute and a non-pausing database plan, or accept cold starts/pauses with free tiers. A paid plan improves availability but is not itself a 24/7 uptime guarantee.

`GET /health/live` does not use the database. `GET /health/ready` checks connectivity.

## Database setup (manual, not performed by the application)

1. Use a disposable/local PostgreSQL database first. This implementation was not connected to a remote Supabase database.
2. The supplied `Scripts/Script-Creacion-Tablas.sql` and `Scripts/Script-Constrains-Tablas.sql` define the existing data contract. Apply those base tables and constraints only after reviewing them for the target PostgreSQL version and required seed/reference data. The wider supplied SQL bundle contains dialect-specific routines/seeds; do not assume those scripts are directly executable in PostgreSQL.
3. Ensure these existing tables and keys are present before continuing: `USUARIOS`, `ROLES`, `USUARIOSxROLES`, `CIUDADES`, `CLIENTES`, `PRODUCTOS`, `CATEGORIAS`, `MARCAS`, `CARRITO`, and `DETALLE_CARRITO`.
4. Manually apply the versioned checkout/API migration **once**, after the base schema:

   ```powershell
   psql "$env:DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/001_api_sessions_catalog_orders.sql
   ```

   It creates revocable server-side sessions, the public product-slug map, stock-minimum policy, order snapshots, idempotency, and a stock-reservation ledger. It is not run at application startup.
5. Provision the active customer role (`cliente`, `customer`, or `client` in `ROLES`) and seed `CIUDADES`, `PRODUCTOS`, `CATEGORIAS`, and `MARCAS`. Admin accounts are provisioned out-of-band; public signup cannot choose a role. Generate Argon2id hashes for new administrator credentials with `python -m app.hash_password` and provision the account/role assignment through a trusted operator workflow. Existing hashes with another format must be migrated/reset before these login endpoints can verify them.
6. Map each real legacy product key (`PRODUCTOS.id_Producto`, at most 10 characters) to its public slug through the administrator API:

   `PUT /api/v1/admin/catalog/mappings`

   Example JSON: `{"product_id":"DBKEY00001","slug":"futbol-balon-pro-match","category_slug":"futbol","image_url":"images/products/futbol-balon-pro-match.svg"}`. Use the actual product key from the database, and map each storefront category ID (for example `mma`, `ropa`) explicitly. Slugs are not stored in or truncated into the legacy `CHAR(10)` IDs. This project intentionally does not seed products or guess this mapping.
7. Serve `E-commerce/` and `Administracion-ecommerce/` from origins listed in `CORS_ORIGINS`. The public customer registration also requires a matching city ID.

## Implemented API

- `POST /api/v1/auth/register`, `POST /auth/login`, `GET /auth/me`, `DELETE /auth/session`
- `GET /products`, `GET /products/{slug}`, `GET /products/categories`, `GET /products/cities`
- Authenticated `GET /cart`, `PUT /cart/items`, `DELETE /cart/items/{slug}`
- `POST /checkout` (requires `Idempotency-Key`), `GET /orders`, `GET /orders/{id}`, and cancellation for the current user's pending order
- Admin-only database-backed summary and paginated reads for products, inventory, purchases, suppliers, receipts, sales/invoices, deliveries, customers, users, roles, audit records, and checkout orders
- Admin product/catalog mapping edits, stock minimum settings, and explicit release of expired demo-order reservations

Authentication uses Argon2id hashes and opaque bearer tokens whose SHA-256 digests are stored server-side with expiry and revocation. Login and registration have a per-process IP rate limit. Keep the API behind HTTPS in any non-local environment. Bearer tokens are held in browser `sessionStorage`; do not serve untrusted scripts from either interface origin.

## Checkout and known business limitations

Checkout re-reads prices and inventory from PostgreSQL, locks products, reserves stock atomically, records immutable line/address snapshots, and clears the server cart in one transaction. Repeating the same user's idempotency key returns the original order. Reservations expire after 30 minutes and stock is released with a ledger entry when a user next checks their orders/checkout or an administrator calls `POST /api/v1/admin/orders/release-expired`. Schedule that endpoint operationally if abandoned reservations must be released without user/admin traffic.

There is no payment provider. `payment_method` is fixed to `demo`, payment status is always `NOT_PROCESSED`, and the API stores no card number/CVV. Orders remain pending and must not be presented as paid or ready to ship. Delivery pricing and tax are not specified by the supplied schema/business rules, so the demo API currently records a zero delivery fee and no tax; the UI labels this unconfigured. Configure validated pricing/tax rules before production checkout.

Admin interface modules use real, read-only data where the existing schema supports it. Product/catalog and minimum-stock updates have endpoints, but not every legacy module has a safe write workflow. Role-permission editing, purchase/receipt/adjustment workflows, invoice generation, delivery fulfillment, password recovery, and a payment-provider integration are not implemented. Existing supplied routines may need PostgreSQL dialect/behavior review before being used with this API.

## Local validation

```powershell
python -m pytest -q
```

No live database, remote Supabase instance, migration execution, payment processor, or deployment is included in local tests.
