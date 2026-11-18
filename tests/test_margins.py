"""scripts/margins.py: the near-tie reader check_extraction --margins uses (#120). Fake tokens
only - tests never call the model."""
import math
from types import SimpleNamespace

from scripts.margins import near_ties


def _token(text, p, *rivals):
    """One answer token chosen at likelihood p, with (text, likelihood) runners-up."""
    tops = [SimpleNamespace(token=text, logprob=math.log(p))]
    tops += [SimpleNamespace(token=t, logprob=math.log(q)) for t, q in rivals]
    return SimpleNamespace(token=text, logprob=math.log(p), top_logprobs=tops)


def test_a_near_tie_is_listed_with_its_context_and_rivals():
    tokens = [_token('{"kind":"', 1.0), _token("everyone", 0.49, ("named", 0.49), ("none", 0.02))]
    [line] = near_ties(tokens)
    assert "'{\"kind\":\"'" in line
    assert "chose 'everyone' 49%" in line and "'named' 49%" in line and "'none' 2%" in line


def test_a_sure_choice_is_not_listed():
    assert near_ties([_token("5", 0.97, ("null", 0.03))]) == []


def test_spacing_and_tiny_rivals_are_not_near_ties():
    """'","' against ' ","' or a run of spaces changes nothing in the JSON."""
    tokens = [_token('","', 0.53, (' ","', 0.40), ("  \n", 0.06)), _token("6", 0.90, ("7", 0.005))]
    assert near_ties(tokens) == []
