-- Per department: every pound in the raw files is kept or dropped,
-- and the payments table holds exactly the kept pounds.
SELECT
    l.dept_code,
    count(*)                                              AS raw_lines,
    count(*) FILTER (WHERE l.keep)                        AS kept_lines,
    count(*) FILTER (WHERE NOT l.keep)                    AS dropped_lines,
    any_value(p.payments)                                 AS payments,
    coalesce(sum(l.amount_pence), 0)                      AS raw_pence,
    coalesce(sum(l.amount_pence) FILTER (WHERE l.keep), 0)     AS kept_pence,
    coalesce(sum(l.amount_pence) FILTER (WHERE NOT l.keep), 0) AS dropped_pence,
    any_value(p.payments_pence)                           AS payments_pence
FROM payment_lines l
LEFT JOIN (
    SELECT dept_code, count(*) AS payments, sum(amount_pence) AS payments_pence
    FROM payments GROUP BY dept_code
) p USING (dept_code)
GROUP BY l.dept_code
ORDER BY l.dept_code;
