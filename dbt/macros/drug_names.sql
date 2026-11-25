{#-
  Drug-name normalisation rules: the SQL twin of ingestion/drug_names.py
  (canonical_drug_name). The word lists below must stay identical to NULL_WORDS,
  STRIP_WORDS, DOSE_UNITS and DOSE_PER_UNITS there; tests/dbt/test_dbt_build.py checks the
  lists and compares stg_drug_names.canonical_name with the Python function on every raw
  name in the sample.

  Python uses lookarounds, which neither RE2 nor POSIX ERE support. They are emulated:
  - (?<![\w.]) before the number  -> group 1 (^|[^a-z0-9_.µ§]) is consumed and written back.
  - (?![a-zµ]) / (?![a-z²]) after the unit -> group 9 ([^a-zµ²]|$) is consumed and written
    back. Because a match consumes the character after it, a dose that directly follows
    another dose ("10 mg 20 mg") can be skipped on the first pass, so the replacement runs
    twice (two passes are enough: skipped doses are never adjacent). A removed dose is
    replaced by the marker '§' instead of a space, and '§' is not a valid character before
    a number, so the second pass cannot match a number that Python's look-behind rejected
    ("5mg10mg" keeps "10mg"). The marker becomes a space afterwards. Assumes '§' never
    occurs in an intervention name.
  - STRIP_WORDS are removed token by token: every whitespace run becomes two spaces, so each
    token has its own leading and trailing space, and one pass removes all of them.
-#}

{% macro drug_null_words() -%}
    {{ return(['placebo']) }}
{%- endmacro %}

{% macro drug_strip_words() -%}
    {{ return([
        'oral', 'orally', 'tablet', 'tablets', 'tab', 'tabs', 'capsule', 'capsules',
        'injection', 'injections', 'injectable', 'iv', 'intravenous', 'intravenously',
        'subcutaneous', 'subcutaneously', 'sc', 'intramuscular', 'im', 'topical', 'solution',
        'suspension', 'infusion', 'cream', 'gel', 'ointment', 'patch', 'spray', 'inhaled',
        'inhalation', 'drops'
    ]) }}
{%- endmacro %}

{% macro drug_dose_units() -%}
    {{ return(['mg', 'mcg', 'ug', 'µg', 'g', 'kg', 'ng', 'ml', 'l', 'iu', 'unit', 'units', 'u',
               'meq', 'mmol']) }}
{%- endmacro %}

{% macro drug_dose_per_units() -%}
    {{ return(['kg', 'm2', 'm²', 'ml', 'l', 'day', 'd', 'h', 'hr', 'dose']) }}
{%- endmacro %}


{# Rule 2: the name contains a NULL word as a whole [a-z]+ run. #}
{% macro drug_null_word_pattern() -%}
    {{ return('(^|[^a-z])(' ~ drug_null_words() | join('|') ~ ')([^a-z]|$)') }}
{%- endmacro %}

{# Rule 3: doses. 9 capture groups: \1 = char before the number, \9 = char after the unit. #}
{% macro drug_dose_pattern() -%}
    {%- set num = '[0-9]+([.,][0-9]+)?' -%}
    {%- set sp = '[[:space:]]*' -%}
    {{ return(
        '(^|[^a-z0-9_.µ§])' ~ num ~ '(' ~ sp ~ '-' ~ sp ~ num ~ ')?' ~ sp
        ~ '((' ~ drug_dose_units() | join('|') ~ ')'
        ~ '(' ~ sp ~ '/' ~ sp ~ '(' ~ drug_dose_per_units() | join('|') ~ '))?'
        ~ '([^a-zµ²]|$)|%)'
    ) }}
{%- endmacro %}

{# Rule 5: a padded token that is a strip word once -/+&. are trimmed from its ends. #}
{% macro drug_strip_word_pattern() -%}
    {{ return(' [-/+&.]*(' ~ drug_strip_words() | join('|') ~ ')[-/+&.]* ') }}
{%- endmacro %}


{#-
  canonical_drug_name(expr): SQL expression giving the canonical name of the raw
  intervention name `expr`, or NULL (placebo, or nothing left after stripping).
-#}
{% macro canonical_drug_name(expr) -%}
    {%- set lowered = 'lower(' ~ expr ~ ')' -%}
    {%- set no_dose_1 = regex_replace(lowered, drug_dose_pattern(), '\\1§\\9') -%}
    {%- set no_dose_2 = regex_replace(no_dose_1, drug_dose_pattern(), '\\1§\\9') -%}
    {%- set no_dose = "replace(" ~ no_dose_2 ~ ", '§', ' ')" -%}
    {%- set no_punct = regex_replace(no_dose, '[(),;:]', ' ') -%}
    {%- set padded = "' ' || " ~ regex_replace(no_punct, '[[:space:]]+', '  ') ~ " || ' '" -%}
    {%- set no_strip = regex_replace('(' ~ padded ~ ')', drug_strip_word_pattern(), ' ') -%}
    {%- set collapsed = regex_replace(no_strip, '[[:space:]]+', ' ') -%}
    {%- set trimmed = regex_replace(collapsed, '^[-/+&. ]+|[-/+&. ]+$', '') -%}
    case
        when {{ expr }} is null then null
        when {{ regex_search(lowered, drug_null_word_pattern()) }} then null
        else nullif({{ trimmed }}, '')
    end
{%- endmacro %}
