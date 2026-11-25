-- Spend per department per month, with the month-on-month change.
CREATE OR REPLACE TABLE monthly_trend AS
SELECT
    dept_code,
    period_month,
    count(*)                         AS payments,
    count(DISTINCT supplier_key)     AS suppliers,
    sum(amount_gbp)                  AS spend_gbp,
    sum(amount_gbp) - lag(sum(amount_gbp)) OVER (PARTITION BY dept_code ORDER BY period_month)
                                     AS change_gbp
FROM payments
GROUP BY dept_code, period_month
ORDER BY dept_code, period_month;
