-- Possible duplicate payments: the same department paid the same supplier the
-- same amount, to the penny, within 7 days, under two different payment references.
--
-- Two cases look like duplicates but usually are not, so they get their own group:
--   bulk          4 or more identical payments within 7 days of each other. This is
--                 a bulk order at a unit price (for example a fleet of identical cars),
--                 not a bill paid twice.
--   no_reference  one of the two payments has no transaction number, so we cannot
--                 tell whether it is a separate payment.
-- The rest are graded:
--   high          same day, same expense type and same description
--   medium        within 7 days, same expense type
--   low           within 7 days, different expense type
CREATE OR REPLACE TABLE audit_possible_duplicates AS
WITH p AS (
    SELECT
        p.*,
        p.payment_ref <> coalesce(l.line_id, '') AS has_reference,
        count(*) OVER (
            PARTITION BY p.dept_code, p.supplier_key, p.amount_pence
            ORDER BY p.payment_date
            RANGE BETWEEN INTERVAL 7 DAY PRECEDING AND INTERVAL 7 DAY FOLLOWING
        ) AS identical_nearby
    FROM payments p
    LEFT JOIN payment_lines l ON l.line_id = p.payment_ref
    WHERE p.supplier_key IS NOT NULL
      AND p.payment_date IS NOT NULL
      AND p.amount_pence > 0
)
SELECT
    a.dept_code,
    a.supplier_name,
    a.supplier_key,
    a.amount_gbp,
    a.payment_ref                                    AS first_ref,
    a.payment_date                                   AS first_date,
    b.payment_ref                                    AS second_ref,
    b.payment_date                                   AS second_date,
    b.payment_id                                     AS second_payment_id,
    date_diff('day', a.payment_date, b.payment_date) AS days_apart,
    greatest(a.identical_nearby, b.identical_nearby) AS identical_nearby,
    a.expense_type                                   AS first_expense_type,
    b.expense_type                                   AS second_expense_type,
    a.description                                    AS first_description,
    b.description                                    AS second_description,
    CASE
        WHEN greatest(a.identical_nearby, b.identical_nearby) >= 4 THEN 'bulk'
        WHEN NOT (a.has_reference AND b.has_reference) THEN 'no_reference'
        WHEN a.payment_date = b.payment_date
             AND a.expense_type IS NOT DISTINCT FROM b.expense_type
             AND a.description IS NOT DISTINCT FROM b.description THEN 'high'
        WHEN a.expense_type IS NOT DISTINCT FROM b.expense_type THEN 'medium'
        ELSE 'low'
    END                                              AS confidence
FROM p a
JOIN p b
  ON  a.dept_code = b.dept_code
  AND a.supplier_key = b.supplier_key
  AND a.amount_pence = b.amount_pence
  AND a.payment_id <> b.payment_id
  AND b.payment_date BETWEEN a.payment_date AND a.payment_date + INTERVAL 7 DAY
  AND (a.payment_date < b.payment_date OR a.payment_id < b.payment_id);
