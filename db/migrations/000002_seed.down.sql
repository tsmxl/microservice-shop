BEGIN;

DELETE FROM order_items
WHERE product_id IN (
    SELECT id FROM products
    WHERE title IN ('Плёнка 1987', 'Северное сияние', 'Тёплый шум', 'Город спит', 'Демо-запись №5', 'Кварцевые часы', 'Перемотка', 'Сторона Б')
);

DELETE FROM orders o
WHERE NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.order_id = o.id);

DELETE FROM products
WHERE title IN ('Плёнка 1987', 'Северное сияние', 'Тёплый шум', 'Город спит', 'Демо-запись №5', 'Кварцевые часы', 'Перемотка', 'Сторона Б');

COMMIT;
