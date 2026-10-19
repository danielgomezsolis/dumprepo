---
name: anonymization-metrics
description: Use to evaluate anonymized, pseudonymized or redacted text in any placeholder style: the LLM judges every item and whether it was anonymized correctly, reports precision, recall and consistency, and writes one CSV line per document.
---

# Anonymization metrics

This skill evaluates anonymized, pseudonymized or redacted text. The judge is the LLM running this skill. It reads the text itself, judges each item, and computes the measures from its own verdicts. No scripts, local NLP models or external services are used.

It works with any replacement style:
- indexed tags (`<PERSON_1>`, `[NAME-2]`, `{{person 3}}`, `<GIVEN_NAME-001>`, `«PERSON 1»`);
- tags without an index (`<PERSON>`, `[REDACTED]`);
- redaction marks (`█████`, `XXXX`, `***`);
- deleted text;
- surrogates, meaning fake values that replace the real ones.

The output alone is enough. If the original is also given, the judge uses it as a reference. That makes the verdicts firmer and lets the judge see deletions and surrogates, which it can't see from the output alone.

| You have | What the judge does | Result |
| --- | --- | --- |
| Output only | Reads the output and infers what each placeholder hid from its context | Estimated measures |
| Output and original | Reads both and checks each item against the original | Verified measures, still judged by the LLM |

## 0. Ground rules

- **The judge reads.** Don't run code, NER or NLP models, or detection services. The LLM's own reading is the judgment, and its arithmetic is shown so that anyone can check it.
- **Keep documents in this session.** Never put their content into web search or an external service.
- **Never try to find out who a person really is.**
- **Report without originals.** The report names entities by ID, category and document, never by their text. If text must be shown, mask it (`J*** P***`).
- **Never present the numbers as proof of legal anonymity.**

## 1. Inputs

| Input | Needed | Notes |
| --- | --- | --- |
| Anonymized outputs | Yes | Any style. For .docx or .pdf, extract the text first, using the same tool for any original |
| Original documents | Optional | Used only as the judge's reference |
| Anonymization policy | Recommended | What the tool was meant to hide and keep. Without one, use the default policy in §4 |
| Counter scope | Yes, for indexed tags | `document` if each file restarts its numbering; `batch` if an entity keeps its identity across files. Ask if it's unknown |
| Languages | For batches | Report each language separately |
| Re-identification key | Never | Don't ask for it. If the user supplies it anyway, keep it out of the report |

## 2. Reading the replacement style

Look at the outputs first. Note:
- what a placeholder looks like in this tool;
- whether it carries a label (`PERSON`, `DATE`) and an index (`_1`, `-001`);
- whether the index restarts per category (`<PERSON_1>` and `<DATE_1>` are different entities) and per document.

| Style | Identity used for consistency |
| --- | --- |
| Indexed tags | The label and index together, read within each category, so `<PERSON_1>` and `<LOCATION_1>` never collide. In a tag such as `<3P-001>`, the index is the trailing number, not the digit in the label |
| Tags without an index, redaction marks, deletion | None: consistency is n/a, because the output keeps no context |
| Surrogates | The surrogate text. A shorter surrogate inside one longer surrogate of the same category ("Quirós" in "Mateo Quirós") is the same identity |

## 3. The judgment table

The judge builds one table per document, with one row per item in reading order:

| Column | Content |
| --- | --- |
| `#` | Row number |
| `span` | The placeholder, or the visible text for a leak or keep row (masked in the report) |
| `category` | The category judged from the context (§3.1) |
| `entity` | The real-world thing behind it, e.g. `PERSON-a`, `DATE-b` (§3.2) |
| `identity` | The tool's identity for it (§2), or `—` |
| `verdict` | `ok`, `wrong-label`, `over`, `partial`, `leak` or `keep` (§4) |
| `confidence` | `high`, `medium` or `low` |

**Steps:**
1. **Read the whole output before judging any row.** With batch scope, read every file in the batch.
2. **Add one row for every placeholder.** Drop none. Count the placeholders, and check that the count matches the rows.
3. **Judge every row:** category, entity, verdict and confidence.
4. **Search the visible text for personal data that should have been hidden.** Go sentence by sentence. Add a `leak` row for each item, giving the entity it exposes (the same ID as that person's placeholders, if any). Add `keep` rows for borderline items you decided may stay.
5. **With the original:** compare it with the output, passage by passage.
   - Add `leak` rows for anything the comparison shows was missed, including real names kept among surrogates.
   - Correct verdicts the original contradicts.
   - Personal data that was deleted outright counts as masked: add it as an `ok` row with `identity` `—`.
6. **Write residual-risk notes** for each document. Rate each risk Low, Medium or High, with its clues masked:
   - could someone single out a person from a rare combination of the details left?
   - could the document be linked to another record?
   - could a sensitive attribute be inferred?

### 3.1 Category

Treat the tag's label as a hypothesis and check it against the context. Without a label, infer the category from the context alone. Use the tool's or the policy's category names.

| Context around the item | Category |
| --- | --- |
| Title or honorific (Sr., Mrs., Dr., -san), a verb of saying or doing, a kinship word, a signature line | PERSON |
| "born", "nació", "on", "el día", a weekday, "dated" | DATE |
| "aged", "años de edad" | AGE |
| "lives at", "vecino de", "calle", "avenue", a house number | ADDRESS or LOCATION |
| "ID", "cédula", "passport", "file no.", "account" | Identifier |
| "@", "tel.", "phone" | EMAIL, PHONE |
| "works at", "employed by", "S.A.", "Inc.", "Hospital", "Universidad" | ORG |
| "diagnosed", "padece", "treated for" | HEALTH |
| "member of", "afiliado a", a party, union or church | POLITICAL, UNION, RELIGION |
| "convicted", "arrested", "denunciado" | CRIMINAL |

### 3.2 Entity

Give every row except `over` and `keep` rows an ID for the real-world thing behind it: a person, a date, a place or a number. Decide by meaning, not by the tool's number. Meaning comes from roles, titles, pronouns, kinship, gender agreement and actions, and from the original when you have it.

- **Agree with the tool** when the context agrees: same entity, same ID.
- **Split** when one tool identity stands for two people whose roles, gender or kinship can't both be true. That is a merge by the tool.
- **Join** when the context shows that two tool identities are one person. Examples: a signer after "Firma:" who is the declarant; "the buyer" introduced as `[PERSON_1]` and later written `[PERSON_3]`. That is a split by the tool.
- **Name parts** (given name, surnames, patronymics, a nickname) belong to that person's ID.
- **Same date, same entity**, in any format. A different date is another entity.
- **Items without an index** (`[ADDRESS]`) still get an ID, so that two different addresses count as two entities.

## 4. Verdicts and policy

| Verdict | Use when | Counts as |
| --- | --- | --- |
| `ok` | The placeholder hides personal data, and its label fits or it has none | Masked |
| `wrong-label` | It hides personal data, but its label is wrong (a date tagged PERSON) | Masked; lowers label agreement |
| `over` | It hides something that isn't personal data under the policy: a public authority, a city where the policy keeps cities, a common word | Over-masking |
| `partial` | The personal data is only partly hidden: part of it stays beside the tag or inside it (`[PERSON_1] Pérez`, `<DATE=3 March 1987>`) | Exposed |
| `leak` | Visible personal data that the policy says must be hidden | Exposed |
| `keep` | A visible item you checked that may stay | Not counted |

A generalization the policy allows is `ok`, for example a date reduced to its year (`<DATE-001=1987>`).

**Default policy**, used when the user gives none:
- **Must be hidden:**
  - names;
  - identity, account, record and contact numbers;
  - exact addresses;
  - sensitive attributes: health, sex life, beliefs, political or union membership, ethnic origin, criminal matters, immigration status, genetic and biometric data;
  - everything above for third parties too.
- **Hidden or generalized:** dates tied to a person, ages, places below city level, specific occupations, organizations that point to a person, rare events.
- **May stay:** city or region and above; the year, when the policy generalizes dates to the year; public authorities; legal citations; general history.

**Confidence:**
- **high:** the context settles it;
- **medium:** it rests on a reasonable reading, such as a signer being the declarant;
- **low:** it's a guess.

## 5. Measures

Count from the judgment table and show the counts beside every figure. Use micro-averaging: pool the rows of all documents in a group, then divide.

| Measure | Question it answers | Computed as | Source |
| --- | --- | --- | --- |
| **Precision** | Of everything masked, how much was personal data? | (ok + wrong-label + partial) ÷ (ok + wrong-label + partial + over) | Standard IR measure, as in i2b2 and TAB |
| **Mention recall** | How many personal mentions were fully hidden? | (ok + wrong-label) ÷ (ok + wrong-label + partial + leak) | i2b2 2014 |
| **Entity recall** | How many entities were fully hidden? This is the privacy figure, because one visible repeat mention exposes the person | Entities with no `partial` or `leak` row ÷ entities | Pilán et al. 2022 (TAB), the all-mentions rule |
| **Consistency** | Does each entity keep one identity, and does no other entity share it? | B³ (below), over masked personal rows that have an identity | Bagga & Baldwin 1998; PSILENCE 2024 |
| **F1** | — | 2·P·R ÷ (P + R), with mention recall | — |
| **Label agreement** | Are tag labels right? | ok ÷ (ok + wrong-label), labelled tags only | — |

### Computing B³ by hand

Take the masked personal rows (`ok`, `wrong-label`) that have a tool identity. Within each document under document scope, or across the batch under batch scope:

1. Group the rows two ways: by the judge's **entity**, and by the tool's **identity**.
2. For each row, let *n* be the number of rows sharing both its entity and its identity, *I* the size of its identity group, and *E* the size of its entity group. Then:
   - *p* = *n* ÷ *I*;
   - *r* = *n* ÷ *E*.
3. Average *p* over all rows to get **B³ p**, and average *r* to get **B³ r**.
4. Consistency = 2·p·r ÷ (p + r).

Rows in the same cell (same entity and identity) share *n*, *I* and *E*, so tabulate cells rather than single rows:

| Entity | Identity | Rows in cell (*n*) | Entity total (*E*) | Identity total (*I*) | *p* = *n*/*I* | *r* = *n*/*E* |
| --- | --- | --- | --- | --- | --- | --- |

Then weight each cell's *p* and *r* by its row count *n*.

Write this table to the B³ CSV (§8.6), one line per cell, so that anyone can follow the calculation.

**Worked example.** The person A appears as `[PERSON_1]` twice and as `[PERSON_3]` once (a split). The person B appears as `[PERSON_2]` once, and the person C also as `[PERSON_2]` once (a merge).

| Entity | Identity | *n* | *E* | *I* | *p* | *r* |
| --- | --- | --- | --- | --- | --- | --- |
| A | PERSON_1 | 2 | 3 | 2 | 1 | 2/3 |
| A | PERSON_3 | 1 | 3 | 1 | 1 | 1/3 |
| B | PERSON_2 | 1 | 1 | 2 | 1/2 | 1 |
| C | PERSON_2 | 1 | 1 | 2 | 1/2 | 1 |

- B³ p = (2·1 + 1·1 + 1·½ + 1·½) ÷ 5 = 4 ÷ 5 = 0.800.
- B³ r = (2·⅔ + 1·⅓ + 1·1 + 1·1) ÷ 5 = (5/3 + 2) ÷ 5 = 0.733.
- Consistency = 2 × 0.800 × 0.733 ÷ (0.800 + 0.733) = 0.765.

Check every computation once more before reporting. Round to three decimals.

### Reading the numbers

- **Entity recall below 1:** some entity still has a visible mention. It is listed under "Exposed entities".
- **B³ r below 1 means splits:** an entity came back with a different identity.
- **B³ p below 1 means merges:** two entities share one identity.
- **Identity coverage:** the share of masked personal rows that have an identity. At 0% (tags without an index, redaction marks, deletion), consistency is n/a.
- **Never fold consistency into F1, and never average P, R and C into one score.**
- **Small samples:** with fewer than about 10 documents or 30 personal rows per group, say that differences between groups may be noise.

## 6. Procedure

1. Collect the outputs, any originals, the policy and the counter scope.
2. Read the replacement style (§2).
3. For each document, build the judgment table and the risk notes (§3, §4).
4. Count the verdicts per category and compute the measures (§5), per language and overall.
5. Re-read every `leak`, `partial`, `over` and low-confidence row, and recheck the arithmetic.
6. Report (§7).
7. Write one CSV line per document (§8), and the B³ cell lines for each document (§8.6). Ask about the file before writing the first line.

## 7. Report

By default, write a short report in chat. Build a document or spreadsheet only if the user asks. The report has six parts:

1. **Header:**
   - documents and languages;
   - the replacement style and counter scope;
   - the policy used;
   - "judged by [model], output only" or "judged by [model], with the original as reference".
2. **Results per language and overall:** documents, personal rows, entities, precision, mention recall, entity recall, consistency (p, r, identity coverage), F1 and label agreement, each with its counts. Mark output-only figures "estimated".
3. **Verdicts by category:** counts of `ok`, `wrong-label`, `partial`, `leak`, `over` and `keep`.
4. **Exposed entities and over-masking:** document, entity ID and category, never the text.
5. **Residual-risk notes** per document, and the number of low-confidence rows.
6. **Limits** (§9).

After the report, show the CSV lines (§8).

Include the judgment tables, with spans masked, only if the user asks.

Never call a result "anonymous".

## 8. CSV output

Every evaluation ends with one CSV line per document, holding what the judge gathered. Each line keeps the counts as well as the scores, so later analysis can pool documents by adding counts rather than averaging scores. Never write a summary or "overall" line: it would be counted twice.

### 8.1 New or existing file

Before writing the first line, ask the user:

> Do you need a new CSV file, or should the lines be added to an existing file with the same structure?

- **New file:** write the header row, then the lines. Name it `anonymization_eval_YYYY-MM-DD.csv` unless the user gives a name, and deliver it to the user.
- **Existing file:** ask for it if it isn't attached. Then read its header row and compare it with the header in §8.2, name by name and in order.
  - **Same header:** add the new lines at the end. Never change, reorder or delete existing rows. Use the file's delimiter (`,` or `;`) and decimal mark. Deliver the updated file, or add to it in place where you can write to it.
  - **Different header:** don't write. Show which columns are missing, extra or in a different order, and ask whether to:
    - write the lines in that file's column order, leaving its extra columns empty and leaving out values it has no column for;
    - map differently named columns to the ones in §8.2, as the user says;
    - start a new file instead.

    Never add columns to an existing file.
  - **Possible duplicate:** if a row already has the same `doc_id`, `tool`, `judge_model` and `eval_date`, ask before adding another.
- **Nobody to ask** (an unattended run): make a new file, and say so in the report.

Whatever the answer, also show the header and the lines in chat in a code block, so they can be copied.

### 8.2 Columns

The header is fixed. Use exactly these 35 columns, in this order, and no others:

```
eval_date,doc_id,language,tool,judge_model,mode,policy,scope,style,placeholders,entities,n_ok,n_wrong_label,n_over,n_partial,n_leak,n_keep,n_low_conf,precision,mention_recall,entity_recall,b3_p,b3_r,consistency,identity_coverage,f1,label_agreement,risk_singling_out,risk_linkability,risk_inference,exposed_categories,over_categories,keywords,notes,error_offsets
```

| Group | Column | Content |
| --- | --- | --- |
| Identification | `eval_date` | Date of the evaluation, `YYYY-MM-DD` |
| | `doc_id` | The document's file name or ID |
| | `language` | Language code, e.g. `es`, `en` |
| | `tool` | The anonymization tool and version evaluated, as the user names it; empty if unknown |
| | `judge_model` | The model that judged |
| | `mode` | `output_only` or `with_original` |
| | `policy` | The policy's name, or `default` |
| | `scope` | `document` or `batch` |
| | `style` | `indexed`, `unindexed`, `redaction`, `deletion`, `surrogates` or `mixed` |
| Counts | `placeholders` | Placeholders in the output; must equal the placeholder rows of the judgment table |
| | `entities` | Entities judged (every distinct entity ID) |
| | `n_ok`, `n_wrong_label`, `n_over`, `n_partial`, `n_leak`, `n_keep` | Rows with each verdict |
| | `n_low_conf` | Rows with low confidence |
| Measures | `precision`, `mention_recall`, `entity_recall` | Three decimals |
| | `b3_p`, `b3_r`, `consistency`, `identity_coverage` | Three decimals; empty when consistency is n/a |
| | `f1`, `label_agreement` | Three decimals; `label_agreement` empty when tags carry no labels |
| Risk | `risk_singling_out`, `risk_linkability`, `risk_inference` | `low`, `medium` or `high` |
| Findings | `exposed_categories` | Categories with at least one exposed entity, separated by `;`, e.g. `PERSON;DATE`; empty if none |
| | `over_categories` | Categories with `over` rows, separated by `;`; empty if none |
| Notes | `keywords` | Terms from the vocabulary in §8.3 only, separated by `;`; empty if no finding |
| | `notes` | The judge's comment on the document, in one or two sentences |
| Location | `error_offsets` | Where in the text each problem row sits, as a pointer and never as text (§8.5); empty if there is none |

**Rules for the values:**
- UTF-8. One header row in a new file. No line breaks inside a field.
- Put a field in double quotes when it contains the delimiter or a quote, and double any quote inside it (`""`).
- A measure that can't be computed (division by zero, or n/a) is an empty cell, never `0`.
- **Output only, surrogates:** leave `mention_recall`, `entity_recall` and `f1` empty, because name recall can't be evaluated, and use the keyword `surrogate-unclear`.
- **`notes`** follow the report's ground rules: no original text and no clue that points to a person. Mask any name as `P***`. Say what went wrong and where, by entity ID and category (e.g. "PERSON-b's surname visible in a later mention"). Anything no keyword covers goes here. Point to a place with `error_offsets` (§8.5), not with words from the text.

### 8.3 Keyword vocabulary

Use only these terms. If no term fits a finding, describe it in `notes` and add no new term.

| Keyword | Use when |
| --- | --- |
| `leak-surname` | Part of a name (a surname, a given name) is visible in a mention |
| `leak-repeat-mention` | An entity masked once is visible in a later mention |
| `partial-in-tag` | Personal data is kept inside or beside a tag (`<DATE=3 March 1987>`, `[PERSON_1] Pérez`) |
| `split` | One entity carries two or more identities |
| `merge` | Two or more entities share one identity |
| `signer-declarant` | The judge joined a signer with the declarant, or with another party, from the context |
| `wrong-label` | At least one tag's label doesn't match its category |
| `overmask-city` | A city or region was masked although the policy keeps it |
| `overmask-authority` | A public authority, office or institution was masked |
| `overmask-common-word` | A common word or title was masked |
| `generalization-ok` | A value was generalized as the policy allows |
| `year-kept` | A date was reduced to its year as the policy allows |
| `low-context` | Too little context to judge some rows with confidence |
| `surrogate-unclear` | A name can't be told apart from a surrogate |
| `text-rewritten` | The tool rewrote the text beyond the placeholders |
| `numbering-reset` | Indexes restart where the counter scope says they shouldn't, or the other way round |

### 8.4 Example

A test record judged from the output alone:

```
eval_date,doc_id,language,tool,judge_model,mode,policy,scope,style,placeholders,entities,n_ok,n_wrong_label,n_over,n_partial,n_leak,n_keep,n_low_conf,precision,mention_recall,entity_recall,b3_p,b3_r,consistency,identity_coverage,f1,label_agreement,risk_singling_out,risk_linkability,risk_inference,exposed_categories,over_categories,keywords,notes,error_offsets
2026-10-17,test-001,es,tool-x 1.0,claude-opus-5-5,output_only,default,document,indexed,32,23,31,0,1,0,1,1,0,0.969,0.969,0.957,0.944,0.917,0.930,0.770,0.969,1.000,medium,low,medium,PERSON,LOCATION,leak-repeat-mention;split;merge;overmask-city;signer-declarant,"PERSON-b's surname visible in a later mention; one person split across two tags and two people merged under one; a city masked although the policy keeps cities.",leak:812-824;over:1530-1541
```

**Check before delivering:**
- every line has 35 fields;
- `placeholders` = `n_ok` + `n_wrong_label` + `n_over` + `n_partial`, not counting `ok` rows added for text deleted outright;
- each measure matches the counts in its line;
- every keyword is in §8.3.

### 8.5 Error offsets

`error_offsets` lets a reader find each problem in a long text without the CSV repeating the data. It holds one entry per row whose verdict is `leak`, `partial`, `wrong-label` or `over`, in the order they occur in the text, separated by `;`.

- **Entry:** `verdict:start-end`, e.g. `leak:812-824`.
- **Offsets:** counted in characters from the first character of the text, which is offset 0. `start` is the first character of the item and `end` is the position just after its last character. A line break counts as one character, and so does a space. Do not count the `<record>` tags, if the text comes inside them.
- **Which span:** for `partial`, `wrong-label` and `over`, the whole placeholder, including anything kept beside it (`[PERSON_1] Pérez`). For `leak`, the visible personal data.
- **Order:** by `start`, smallest first, whatever the verdict. Never group by verdict.
- **Never the text.** An entry holds a verdict and two numbers, nothing else: no quotes, no names, no surrounding words.
- **Count with care.** Work through the text in blocks and add up the characters of each; don't guess. If you can't place an item with confidence, give your best estimate and add the keyword `low-context`.
- **Checks:** the number of entries equals `n_leak` + `n_partial` + `n_wrong_label` + `n_over`, and every `end` is greater than its `start` and not beyond the length of the text.

### 8.6 B³ table

A second CSV shows the table from "Computing B³ by hand", so the consistency figures can be followed step by step. It holds one line per cell, meaning one line per pair of judge entity and tool identity, for every document that has a consistency figure. Name it `anonymization_b3_YYYY-MM-DD.csv` unless the user gives a name. The same rules as §8.1 apply to a new or existing file, and the header is fixed:

```
eval_date,doc_id,tool,judge_model,entity,identity,n,entity_total,identity_total,p,r
```

| Column | Content |
| --- | --- |
| `eval_date`, `doc_id`, `tool`, `judge_model` | The same values as in the main line of that document. Under batch scope `doc_id` is `batch` |
| `entity` | The judge's entity ID (`PERSON-a`), never the text |
| `identity` | The tool's identity for it (`PERSON_1`) |
| `n` | Rows in the cell |
| `entity_total` | *E*: rows of this entity in the group |
| `identity_total` | *I*: rows with this identity in the group |
| `p`, `r` | *n* ÷ *I* and *n* ÷ *E*, three decimals |

**Checks before delivering:**
- the sum of `n` over a document's cells equals its masked personal rows that have an identity;
- the `n`-weighted mean of `p` equals `b3_p` in the main line, and that of `r` equals `b3_r`;
- a document whose consistency is n/a has no lines here.

In an unattended run, reply with this table as a second ` ```csv ` block, header first, after the main line's block.

## 9. Limits

State these in every report:
- **Output only: recall is optimistic.** It can't count personal data that both the tool and the judge missed, or text deleted without a trace.
- **Output only: surrogates.** Real and fake names look alike. Without the original or the tool's list of surrogates, a name leak can't be told from a surrogate, so report recall for names as not evaluable.
- **Over-masking shows only when the context reveals it.**
- **Consistency rests on the judge's reading** of who is who.
- **One judge.** If possible, have a person re-judge a sample (20% of rows, at least 30) and report the share of rows with the same verdict.

## 10. Pitfalls

- Judging rows before reading the whole output.
- Dropping placeholders, so the table has fewer rows than the text has placeholders.
- Copying the tool's numbers into the entity column without checking the meaning.
- Trusting a tag's label without checking the context.
- Reading a digit in the label (`<3P-001>`) as the index, which merges people.
- The wrong counter scope:
  - document scope hides splits across documents;
  - batch scope on output numbered per document invents splits.
- Running code or an NLP model to find personal data. The judge reads.
- Presenting estimates as exact.
- Comparing tools of different styles on consistency alone.
- Pooling languages, so that a weak language hides behind a strong one.
- Reporting mention recall without entity recall.
- Quoting original text in the report or in the CSV `notes`.
- Writing to an existing CSV without asking first, or without checking its header.
- Adding columns or keywords beyond §8.2 and §8.3.
- Putting the leaked text, rather than its offsets, in `error_offsets`.
- Writing an overall line in the CSV, which counts documents twice.
- Giving a `b3_p` or `b3_r` that the B³ table (§8.6) does not reproduce.

## 11. Sources

- Pilán et al. (2022), "The Text Anonymization Benchmark (TAB)", *Computational Linguistics* 48(4): entity-level recall with the all-mentions rule, precision, micro-averaging.
- Bagga & Baldwin (1998), "Algorithms for Scoring Coreference Chains", LREC Workshop on Linguistic Coreference: B-cubed.
- "PSILENCE: A Pseudonymization Tool for International Law" (CALD-pseudo workshop, 2024): B-cubed used to score pseudonym grouping.
- Stubbs, Kotfila & Uzuner (2015), *Journal of Biomedical Informatics* 58 (Suppl.), S11–S19: the 2014 i2b2/UTHealth de-identification evaluation, with micro-averaged precision and recall by category.
- Article 29 Working Party, Opinion 05/2014 on Anonymisation Techniques: singling out, linkability and inference.
