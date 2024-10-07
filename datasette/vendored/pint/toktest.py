from __future__ import annotations

import tokenize

from datasette.vendored.pint.pint_eval import _plain_tokenizer, uncertainty_tokenizer

tokenizer = _plain_tokenizer

input_lines = [
    "( 8.0 + / - 4.0 ) e6 m",
    "( 8.0 ± 4.0 ) e6 m",
    "( 8.0 + / - 4.0 ) e-6 m",
    "( nan + / - 0 ) e6 m",
    "( nan ± 4.0 ) m",
    "8.0 + / - 4.0 m",
    "8.0 ± 4.0 m",
    "8.0(4)m",
    "8.0(.4)m",
    "8.0(-4)m",  # error!
    "pint == wonderfulness ^ N + - + / - * ± m J s",
]

for line in input_lines:
    result = []
    g = list(uncertainty_tokenizer(line))  # tokenize the string
    for toknum, tokval, _, _, _ in g:
        result.append((toknum, tokval))

    print("====")
    print(f"input line: {line}")
    print(result)
    print(tokenize.untokenize(result))
