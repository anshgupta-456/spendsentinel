-- Suppliers paid by more than one department: candidates for a single
-- cross-government contract and the first place to check for inconsistent pricing.
CREATE OR REPLACE TABLE audit_cross_department_suppliers AS
SELECT
    supplier_key,
    arg_max(supplier_name, spend_gbp)        AS supplier_name,
    count(DISTINCT dept_code)                AS departments,
    string_agg(dept_code, ', ' ORDER BY dept_code) AS department_list,
    sum(payments)                            AS payments,
    sum(spend_gbp)                           AS spend_gbp
FROM supplier_spend
GROUP BY supplier_key
HAVING count(DISTINCT dept_code) >= 2
ORDER BY spend_gbp DESC;
