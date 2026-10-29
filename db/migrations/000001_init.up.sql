BEGIN;

CREATE TABLE products (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    artist text NOT NULL CHECK (length(artist) BETWEEN 1 AND 200),
    format text NOT NULL CHECK (format IN ('vinyl', 'cassette')),
    description text NOT NULL DEFAULT '',
    price numeric(10, 2) NOT NULL CHECK (price >= 0),
    stock integer NOT NULL CHECK (stock >= 0),
    drop_at timestamptz NOT NULL DEFAULT now(),
    per_customer_limit integer NOT NULL DEFAULT 2 CHECK (per_customer_limit > 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer text NOT NULL CHECK (length(customer) BETWEEN 1 AND 100),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'rejected')),
    reason text,
    total numeric(10, 2) NOT NULL CHECK (total >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    processed_by text
);

CREATE INDEX orders_customer_idx ON orders (customer);

CREATE TABLE order_items (
    order_id bigint NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES products (id) ON DELETE RESTRICT,
    qty integer NOT NULL CHECK (qty > 0),
    price numeric(10, 2) NOT NULL CHECK (price >= 0),
    PRIMARY KEY (order_id, product_id)
);

CREATE INDEX order_items_product_idx ON order_items (product_id);

CREATE TABLE event_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    consumer text NOT NULL,
    consumed_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX event_log_event_id_key ON event_log ((payload ->> 'id'));

COMMIT;
