-- Dummy seed data. Emails/phones are intentionally realistic-looking so the
-- PII masking middleware has something to catch in demo runs.

INSERT INTO customer (full_name, email, phone, city, country, signup_date) VALUES
    ('Asha Rao',        'asha.rao@example.com',      '+91-98765-43210', 'Bengaluru', 'India',  '2024-01-12'),
    ('Marcus Chen',     'marcus.chen@example.com',   '+1-415-555-0142',  'San Jose',  'USA',    '2024-02-03'),
    ('Priya Nair',      'priya.nair@example.com',    '+91-90000-11122', 'Chennai',   'India',  '2024-03-21'),
    ('Diego Fernandez', 'diego.fernandez@example.com','+34-611-222-333', 'Madrid',    'Spain',  '2024-04-15'),
    ('Emily Carter',    'emily.carter@example.com',  '+44-7700-900123', 'London',    'UK',     '2024-05-02'),
    ('Kenji Sato',      'kenji.sato@example.com',    '+81-90-1234-5678','Osaka',     'Japan',  '2024-06-18');

INSERT INTO product (product_name, category, unit_price) VALUES
    ('Wireless Mouse',        'Electronics', 19.99),
    ('Mechanical Keyboard',   'Electronics', 79.99),
    ('USB-C Hub',             'Electronics', 34.50),
    ('Standing Desk Mat',     'Office',      45.00),
    ('Noise-Cancelling Headphones', 'Electronics', 149.99),
    ('Ergonomic Chair',       'Office',      219.00);

INSERT INTO orders (customer_id, order_date, status) VALUES
    (1, '2025-01-05', 'delivered'),
    (2, '2025-01-08', 'shipped'),
    (1, '2025-02-14', 'pending'),
    (3, '2025-02-20', 'delivered'),
    (4, '2025-03-01', 'cancelled'),
    (5, '2025-03-10', 'delivered'),
    (6, '2025-03-15', 'shipped');

INSERT INTO order_item (order_id, product_id, quantity, unit_price) VALUES
    (1, 1, 2, 19.99),
    (1, 3, 1, 34.50),
    (2, 2, 1, 79.99),
    (3, 5, 1, 149.99),
    (4, 4, 3, 45.00),
    (5, 6, 1, 219.00),
    (6, 2, 2, 79.99),
    (7, 1, 1, 19.99),
    (7, 5, 1, 149.99);
