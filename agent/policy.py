"""When the agent is allowed to say "this is fraud".

A scheme is declared only if the model is confident enough AND the caller has
actually said at least one hard red flag. "Actually said" is checked here:
the model's quotes must be found in the caller's words, so a hallucinated
flag cannot raise an alarm on an ordinary call.
"""

from dataclasses import dataclass

from agent.prompts import Analysis
from rag.chunks import tokenize

QUOTE_OVERLAP = 0.7  # share of quote words that must appear in the caller's speech


def quote_is_said(quote: str, caller_text: str) -> bool:
    """True if the quote is in the text, allowing small paraphrases and word-form changes."""
    quote_tokens = tokenize(quote)
    if not quote_tokens:
        return False
    said = set(tokenize(caller_text))
    return sum(t in said for t in quote_tokens) / len(quote_tokens) >= QUOTE_OVERLAP


@dataclass(frozen=True)
class Decision:
    scheme_id: str | None
    stage_n: int | None
    flags: list[str]  # hard red flags confirmed in the caller's words

    @property
    def declared(self) -> bool:
        return self.scheme_id is not None


def decide(
    analysis: Analysis,
    caller_text: str,
    candidates: set[str],
    threshold: float,
    current: str | None = None,
    current_stage: int | None = None,
) -> Decision:
    flags = [q for q in analysis.hard_red_flags if quote_is_said(q, caller_text)]
    kept = Decision(current, current_stage, flags)

    if current is not None and analysis.scheme_id == current:
        # Once declared, the scheme sticks: following its stages needs no new flags.
        return Decision(current, analysis.stage_n or current_stage, flags)

    qualifies = (
        analysis.scheme_id is not None
        and analysis.scheme_id in candidates
        and analysis.confidence >= threshold
        and bool(flags)
    )
    if not qualifies:
        return kept  # an unsure answer neither declares nor withdraws a scheme
    return Decision(analysis.scheme_id, analysis.stage_n, flags)
