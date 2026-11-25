-- Runs after payment_lines and quality_issues are loaded.
-- 1. flag identical lines inside one file
-- 2. drop lines that an earlier file already published
-- 3. roll lines up into payments
-- 4. flag payments under the £25,000 publication threshold

-- 1. Identical lines in the same file (kept: an invoice can carry two equal lines)
INSERT INTO quality_issues
SELECT 'line', line_id, dept_code, 'exact_duplicate_line', 'copies in file: ' || copies, amount_pence
FROM (
    SELECT line_id, dept_code, amount_pence,
           count(*) OVER (PARTITION BY source_file, entity, expense_type, expense_area, supplier_raw,
                                       transaction_id, description, amount_pence, payment_date) AS copies
    FROM payment_lines
    WHERE keep
)
WHERE copies > 1;

-- 2. The same transaction republished in a later month's file
CREATE TEMP TABLE repeated AS
SELECT line_id, dept_code, amount_pence, first_file
FROM (
    SELECT line_id, dept_code, amount_pence, source_file,
           first_value(source_file) OVER (
               PARTITION BY dept_code, transaction_id, supplier_key, amount_pence, payment_date
               ORDER BY file_month, source_row) AS first_file
    FROM payment_lines
    WHERE keep AND transaction_id IS NOT NULL
)
WHERE source_file <> first_file;

INSERT INTO quality_issues
SELECT 'line', line_id, dept_code, 'repeated_across_files', 'first published in ' || first_file, amount_pence
FROM repeated;

UPDATE payment_lines SET keep = false WHERE line_id IN (SELECT line_id FROM repeated);

-- 3. One row per payment. DWP publishes invoice lines under one payment number,
--    so a payment is every kept line sharing a transaction number and supplier.
--    Lines with no transaction number are payments on their own.
CREATE TABLE payments AS
SELECT
    dept_code || ':' || coalesce(transaction_id, line_id) || ':' || coalesce(supplier_key, '?') AS payment_id,
    dept_code,
    coalesce(transaction_id, line_id)                       AS payment_ref,
    min(payment_date)                                       AS payment_date,
    date_trunc('month', coalesce(min(payment_date), min(file_month)))::DATE AS period_month,
    min(file_month)                                         AS file_month,
    supplier_key,
    arg_max(supplier_name, amount_pence)                    AS supplier_name,
    bool_or(is_redacted)                                    AS is_redacted,
    arg_max(entity, amount_pence)                           AS entity,
    arg_max(expense_type, amount_pence)                     AS expense_type,
    arg_max(expense_area, amount_pence)                     AS expense_area,
    arg_max(description, amount_pence)                      AS description,
    min(postcode)                                           AS postcode,
    count(*)                                                AS n_lines,
    sum(amount_pence)                                       AS amount_pence,
    sum(amount_pence) / 100.0                               AS amount_gbp
FROM payment_lines
WHERE keep
GROUP BY dept_code, coalesce(transaction_id, line_id), supplier_key;

-- 4. Payments that are still under £25,000 once their lines are added up
INSERT INTO quality_issues
SELECT 'payment', payment_id, dept_code, 'below_publication_threshold',
       'payment total £' || printf('%,.2f', amount_pence / 100.0), amount_pence
FROM payments
WHERE abs(amount_pence) < 2500000;
