{#-
  The low-enrollment label rule, in one place so it can be iterated on.

  A TERMINATED study is `terminated_low_enrollment` when, on lower(why_stopped):

      shortfall  OR  (stem AND NOT exclusion)

  - low_enrollment_shortfall_pattern(): a recruitment shortfall stated explicitly
    ("slow accrual", "recruitment difficulties", "enrollment target not reached",
    "insufficient patients", "lack of inclusions", "only two subjects were recruited", ...).
    A shortfall match is positive even when an exclusion shape also occurs, so
    "halt recruitment due to persistently slow enrollment" stays positive.
  - low_enrollment_stem_pattern(): the bare stems accru / recrui(t) / enrol(l), plus the
    misspellings recuit / recrut. On their own they mostly mean a shortfall
    ("Unable to enroll patients", "Lack of recruitment", "Recruitment"), but not always.
  - low_enrollment_exclusion_pattern(): stem uses that are not a shortfall: recruitment
    completed, targets met, enrollment stopped / halted / suspended (after an interim
    analysis, for toxicity, for COVID), "enrolled patients", "the investigator who was
    recruiting", "recruit a replacement", "at the current enrollment". An exclusion only
    cancels a bare-stem match, never a shortfall match.

  All patterns are plain searches in the RE2 / POSIX ERE common subset (see regex.sql).
  Short qualifiers ("low", "no", "few", ...) are preceded by (^|[^a-z]) so that "follow
  enrolled subjects" does not read as "low enrol...". tests/dbt/test_dbt_build.py holds a
  table of why_stopped texts and their expected class. Precision is still unverified until
  the 200-example hand check.
-#}

{% macro _le_subjects() -%}
    {{ return('(participants?|patients?|subjects?|volunteers?|cases|pts|candidates)') }}
{%- endmacro %}

{% macro low_enrollment_stem_pattern() -%}
    {{ return('(accru|recrui|recuit|recrut|enrol)') }}
{%- endmacro %}

{% macro low_enrollment_shortfall_pattern() -%}
    {%- set subj = _le_subjects() -%}
    {%- set stem = low_enrollment_stem_pattern() -%}
    {%- set b = '(^|[^a-z])' -%}
    {%- set parts = [
        b ~ '(slow|slower|low|lower|poor|insufficient|inadequate|lack of|limited|difficult[a-z]*'
            ~ '|difficult[a-z]* (in|with|of|to)|problems? (with|of|in)|challeng[a-z]* (to|of|in|with)'
            ~ '|barriers? to|not enough|unsuccessful|delay[a-z]* (in|of)|delayed|hard to|issues? with'
            ~ '|unsatisfactory)( [a-z]+)? ' ~ stem,
        stem ~ '[a-z]*( [a-z]+){0,2} (was |is |were |are |has been |remained )?'
            ~ '(too |very |extremely |much |far )?'
            ~ '(slow|low|poor|insufficient|inadequate|difficult|problem|challeng|issue|delay|limit'
            ~ '|barrier|below|under|behind)',
        stem ~ '[a-z]* (target|goal)s? (was |were )?not (reached|met|achieved)',
        stem ~ '[a-z]* (was |were )?not (achieved|reached|possible|met|feasible)',
        b ~ '(not|unable to|failed to|fail to|could not|inability to) (meet|reach|achieve|attain)'
            ~ '( [a-z]+){0,2} ' ~ stem,
        'only (one|two|three|four|five|six|seven|eight|nine|ten|a few|[0-9]+) ([a-z]+ )?' ~ subj
            ~ ' (have been |had been |were |was |has been )?'
            ~ '(enrolled|recruited|randomi[sz]ed|included|accrued)',
        b ~ '(slow|low|poor|insufficient|inadequate|lack of|difficult[a-z]*|no)( new)? inclusions?',
        'inclusion rate',
        'inclusion of ' ~ subj ~ ' (was |is )?(slow|low|poor|insufficient|difficult)',
        b ~ '(insufficient|inadequate|lack of|low|limited|not enough|too few|few|small number of|poor|no'
            ~ '|unable to (find|identify|obtain|include|randomi[sz]e|get|locate))'
            ~ ' (eligible |suitable |available |qualified |willing |interested |study )?' ~ subj,
        b ~ '(insufficient|inadequate|low|small|limited|not enough) numbers? of (eligible )?' ~ subj,
        'numbers? of (eligible |enrolled |recruited )?' ~ subj
            ~ ' (was |were |is |are )?(too )?(small|low|insufficient|limited)',
        '(target|planned|required|expected) numbers? of ' ~ subj
            ~ ' (was |were )?not (reached|achieved|met|obtained)',
        '(obtain|obtaining|reach|reaching|achieve|achieving) the'
            ~ ' (planned|target|targeted|required|expected) numbers?',
        'decrease[a-z]* in ([a-z-]+ ){0,3}[a-z-]*eligible ' ~ subj,
        b ~ '(low|poor|insufficient|lack of) participation',
        '(insufficient|inadequate) sample size',
        'sample size (was |is )?(insufficient|too small|not reached|not achieved|not met)',
        '(complete|reach|achieve|attain) the (target |planned |required )?sample size',
        '(difficult[a-z]*|unable|inability) (of |in |to )?(obtain|obtaining|get|getting)'
            ~ ' (informed )?consent'
    ] -%}
    {{ return(parts | join('|')) }}
{%- endmacro %}

{% macro low_enrollment_exclusion_pattern() -%}
    {%- set parts = [
        'enrolled (patients|subjects|participants)',
        '(participants|patients|subjects) (were |have been |had been |was |has been )?(enrolled|recruited)',
        '(accru|enrol|recruit)[a-z]* (was |is |has been |had been )?complete',
        '(meeting|met|reached|achieved|meet|reach|achieve|completing|completed|complete|full)'
            ~ ' (the |its |our )?(target(ed)? |planned |full )?(enrol|accru|recruit)',
        'recruit a replacement',
        '(stop|halt|suspend|paus|clos)[a-z]* (to |of )?(the )?(further |new )?(enrol|recruit|accru)',
        '(enrol|recruit|accru)[a-z]*( [a-z]+){0,4} (was|were|been) ([a-z]+ly )?'
            ~ '(stopped|halted|suspended|paused|terminated|closed|discontinued|interrupted|not reopened)',
        '(through|current|completion of) (the )?(enrol|accru|recruit)',
        'who (was|were|is) (recruiting|enrolling)'
    ] -%}
    {{ return(parts | join('|')) }}
{%- endmacro %}

{# is_low_enrollment(expr): TRUE / FALSE, NULL when expr is NULL. #}
{% macro is_low_enrollment(expr) -%}
    {%- set text = 'lower(' ~ expr ~ ')' -%}
    (
        {{ regex_search(text, low_enrollment_shortfall_pattern()) }}
        or (
            {{ regex_search(text, low_enrollment_stem_pattern()) }}
            and not {{ regex_search(text, low_enrollment_exclusion_pattern()) }}
        )
    )
{%- endmacro %}
