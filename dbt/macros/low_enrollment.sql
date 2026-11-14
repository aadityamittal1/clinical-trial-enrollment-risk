{#-
  The low-enrollment label rule, in one place so it can be iterated on.

  A TERMINATED study is `terminated_low_enrollment` when lower(why_stopped) matches
  low_enrollment_pattern(). The pattern is a plain search (not anchored) in the RE2 /
  POSIX ERE common subset (see regex.sql). Pieces:
  - stems: accru(al|e), recrui(t|ting|tment) incl. the misspellings recuit / recrut,
    enrol(l)(ment|ing)
  - slow / low / poor / insufficient / lack of (new) inclusion(s), inclusion rate,
    inclusion of patients slow
  - insufficient / inadequate / lack of / low / limited / not enough / too few / few /
    small number of / poor / no / unable to find|identify|obtain|include|randomise ...
    followed by (eligible|suitable|...|study) participants|patients|subjects|volunteers|
    cases|pts|candidates
  - "<qualifier> number of patients", "number of patients too small",
    "target number of patients not reached"
  - low / poor / insufficient participation
  - insufficient sample size, sample size not reached, unable to complete the sample size
  - difficulty obtaining (informed) consent
  Known false-positive shapes seen in the sample: a stem used in a non-shortfall sense
  ("unable to recruit a replacement", "stopping enrollment after the interim analysis",
  "after meeting enrollment targets"). Precision is to be measured by the 200-example
  hand check; until then it is unverified.
-#}

{% macro low_enrollment_pattern() -%}
    {%- set subj = '(participants?|patients?|subjects?|volunteers?|cases|pts|candidates)' -%}
    {%- set parts = [
        'accru|recrui|recuit|recrut|enrol',
        '(slow|low|poor|insufficient|inadequate|lack of|difficult[a-z]*|no)( new)? inclusions?',
        'inclusion rate',
        'inclusion of ' ~ subj ~ ' (was |is )?(slow|low|poor|insufficient|difficult)',
        '(insufficient|inadequate|lack of|low|limited|not enough|too few|few|small number of|poor|no'
            ~ '|unable to (find|identify|obtain|include|randomi[sz]e|get|locate))'
            ~ ' (eligible |suitable |available |qualified |willing |interested |study )?' ~ subj,
        '(insufficient|inadequate|low|small|limited|not enough) number of (eligible )?' ~ subj,
        'number of (eligible )?' ~ subj ~ ' (was |were |is |are )?(too )?(small|low|insufficient|limited)',
        '(target|planned|required|expected) number of ' ~ subj
            ~ ' (was |were )?not (reached|achieved|met|obtained)',
        '(low|poor|insufficient|lack of) participation',
        '(insufficient|inadequate) sample size',
        'sample size (was |is )?(insufficient|too small|not reached|not achieved|not met)',
        '(complete|reach|achieve|attain) the (target |planned |required )?sample size',
        '(difficult[a-z]*|unable|inability) (of |in |to )?(obtain|obtaining|get|getting) (informed )?consent'
    ] -%}
    {{ return(parts | join('|')) }}
{%- endmacro %}

{# is_low_enrollment(expr): TRUE / FALSE, NULL when expr is NULL. #}
{% macro is_low_enrollment(expr) -%}
    {{ regex_search('lower(' ~ expr ~ ')', low_enrollment_pattern()) }}
{%- endmacro %}
