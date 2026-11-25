-- How concentrated is each department's spend?
--   top10_share       share of spend going to the 10 largest suppliers
--   suppliers_for_80  how many suppliers make up 80% of spend
--   hhi               Herfindahl-Hirschman index (0 to 10,000; above 2,500 is highly concentrated)
CREATE OR REPLACE TABLE supplier_spend AS
SELECT
    dept_code,
    supplier_key,
    arg_max(supplier_name, amount_pence)  AS supplier_name,
    count(*)                              AS payments,
    sum(amount_gbp)                       AS spend_gbp
FROM payments
WHERE supplier_key IS NOT NULL
GROUP BY dept_code, supplier_key
HAVING sum(amount_gbp) > 0;

CREATE OR REPLACE TABLE supplier_ranked AS
SELECT
    *,
    row_number() OVER (PARTITION BY dept_code ORDER BY spend_gbp DESC)          AS rank,
    spend_gbp / sum(spend_gbp) OVER (PARTITION BY dept_code)                    AS share,
    sum(spend_gbp) OVER (PARTITION BY dept_code ORDER BY spend_gbp DESC
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        / sum(spend_gbp) OVER (PARTITION BY dept_code)                          AS cumulative_share
FROM supplier_spend;

CREATE OR REPLACE TABLE audit_supplier_concentration AS
SELECT
    dept_code,
    count(*)                                                AS suppliers,
    sum(spend_gbp)                                          AS spend_gbp,
    sum(share) FILTER (WHERE rank <= 10)                    AS top10_share,
    min(rank) FILTER (WHERE cumulative_share >= 0.8)        AS suppliers_for_80,
    round(sum(power(share * 100, 2)))                       AS hhi,
    arg_min(supplier_name, rank)                            AS largest_supplier,
    max(share)                                              AS largest_supplier_share
FROM supplier_ranked
GROUP BY dept_code
ORDER BY dept_code;
