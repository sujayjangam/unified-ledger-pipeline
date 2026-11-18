"""Near-ties in a model answer, read from its logprobs (#120).

At temperature 0 the model always takes its most likely choice, so a pass looks the same at
51% as at 99%. A choice that won narrowly can flip on the next call, when OpenAI's servers
break the tie differently, and a check that only says pass or fail can't show that. Reading
the likelihood of each choice shows which messages sit on a tie, and where.

Kept apart from check_extraction.py so tests can import it without loading .env.
"""

import math

# Below this, the model's top choice is reported: it is close enough to flip.
THRESHOLD = 0.95
# How much of the answer before a near-tie is shown, so the field it is in can be read.
CONTEXT = 60


def near_ties(tokens, threshold: float = THRESHOLD) -> list[str]:
    """One line per point where the top choice had under `threshold` likelihood.

    `tokens` is response.choices[0].logprobs.content: each has .token, .logprob and
    .top_logprobs. A point is skipped when every runner-up is the same text with different
    spacing, only spacing, or under 1%: '","' against ' ","' changes nothing in the JSON.
    """
    lines, text = [], ""
    for token in tokens:
        chosen = math.exp(token.logprob)
        rivals = [alt for alt in token.top_logprobs
                  if alt.token.strip() and alt.token.strip() != token.token.strip()
                  and math.exp(alt.logprob) >= 0.01]
        if chosen < threshold and rivals:
            options = ", ".join(f"{alt.token!r} {math.exp(alt.logprob):.0%}" for alt in rivals[:3])
            lines.append(f"after ...{text[-CONTEXT:]!r}: chose {token.token!r} {chosen:.0%}, or {options}")
        text += token.token
    return lines
