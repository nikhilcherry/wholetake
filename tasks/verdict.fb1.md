Review round 1: 47/50 pass. Three failures; my rulings below. Emit changed files in full.

1. CODE BUG (label.py validate_why): the "neutral vocabulary" check rejects every natural explanation — your own
   test's 'Omits the word "not" from "had not changed".' is dropped because "omits"... any verb outside your
   list fails. The rule is: text inside quote marks must be grounded in the inputs. Unquoted words are the
   model's own description and are allowed (length cap and the "misleading" ban still apply). Remove the
   neutral-vocab check. Keep a test that an invented QUOTE is dropped and add one that a plain-English
   explanation with a grounded quote survives.
2. TEST BUG (service test): the fake detector script embeds the detection dict into Python source, so JSON
   `null` becomes an undefined name -> NameError -> job "error". Write the detection JSON to a file in tmp_path
   and have the fake script copy it to --out (or embed it via json.loads of a repr'd string).
3. Headline quote window: test expects "… had not changed over …", code produces "… had not changed over the …".
   Decide one rule, document it in compose.py (I suggest: the removed words plus 3 record words either side,
   ellipses on both ends), and make the test assert that rule rather than a hand-typed string.
