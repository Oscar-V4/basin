"""Deterministic atom staging (the fast, model-free path that runs in the hook).

Heuristic only — noisy by design. The detached LLM worker (extract_llm.py, later)
refines: better statements, conflict edges, confidence. Patterns cover EN + KO since
the user mixes languages. authority_tier comes from the speaker.
"""
from __future__ import annotations

import re

from .core import Store, norm_ws
from . import ops

# (atom_type, compiled pattern) — order matters: first match wins per sentence.
_PATTERNS = [
    ("rejected_path", re.compile(r"\b(instead of|rather than|avoid|discard|drop|deprecate)\b|대신|버리|폐기|하지\s?말|말자", re.I)),
    ("decision",      re.compile(r"\b(let'?s (go with|use|do)|we'?ll (use|go)|decided to|i'?ll use|we choose|chosen)\b|가자|하자|결정|쓰자|채택", re.I)),
    ("constraint",    re.compile(r"\b(must not|must|never|always|only|required|need to|has to)\b|항상|절대|반드시|필수", re.I)),
    ("principle",     re.compile(r"\b(principle|rule of thumb|invariant|always prefer)\b|원칙|불변", re.I)),
    ("open_question", re.compile(r"\b(unsure|not sure|tbd|open question|to be decided|undecided)\b|열린\s?질문|미정|모르겠", re.I)),
    ("preference",    re.compile(r"\b(prefer|favou?r|would like|nice to have)\b|선호|좋겠", re.I)),
    ("risk",          re.compile(r"\b(risk|danger|might break|fragile|caution)\b|리스크|위험|취약", re.I)),
]

_SPEAKER_TIER = {
    "user": "user_explicit",
    "assistant": "assistant_proposed",
    "tool_result": "tool_observed",
    "system": "artifact_declared",
}

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|[\n\r]+|·|•")
_STOP = {"the", "a", "an", "to", "of", "for", "and", "or", "is", "are", "be", "we", "i",
         "let", "lets", "with", "this", "that", "it", "use", "go", "do", "ll"}

# Only conversational prose carries genuine decisions. Pasted attachments, prompt
# echoes (last-prompt), and UI metadata (mode/queue-operation) are not speech — they
# flooded the dogfood pack with file-dump "constraints". Keep them in the raw log
# (recall) but never stage atoms from them.
_PROSE_EVENTS = {"user", "assistant", "system"}

# A leading list / quote / tag marker is NOT evidence of a file dump — the user writes real
# decisions as markdown bullets, numbered items, and quoted lines. Strip ONE such marker
# before judging (else we drop the user's own high-authority decisions — review finding r3).
_LEAD_MARKER = re.compile(r"^\s*(?:[-*+•]|\d+[.)\]:])\s+")     # bullet or "1." / "2)" list marker
_LEAD_TAG = re.compile(r"^\s*\[[a-z_]+\]\s+")                  # "[decision] ", "[constraint] "
_QUOTES = "\"'`“”‘’«»"

# Structural lines that genuinely are file / markup, not prose (checked AFTER marker-strip).
_STRUCT = re.compile(
    r"^\s*[`#>|]"        # md heading / quote / fenced code / table row
    r"|^\s*[\[{]"        # json / array fragment
    r"|^\s*\d+\s+\S"     # cat -n line-number dump: "105 you only need…", "10 - Registers…"
)
# A path/filename CITATION inside prose is fine ("CLAUDE.md never names a client domain.") —
# discount the path token, then judge what remains; only reject when the line is mostly path.
# Extension branch is non-backtracking (no '.' in the run + lookahead anchor) to avoid ReDoS.
_PATH_TOKEN = re.compile(
    r"https?://\S+"
    r"|(?<![\w.])~?/[\w./-]+"
    r"|\b[\w/-]+\.(?:md|py|ts|tsx|json|sql|sh|ya?ml)(?![\w.])"
)
_HANGUL = re.compile(r"[가-힣]")


def _strip_markers(sent: str) -> str:
    s = _LEAD_TAG.sub("", sent.strip(), count=1)
    s = _LEAD_MARKER.sub("", s, count=1)
    return s.strip().strip(_QUOTES).strip()


def _is_prose(sent: str) -> bool:
    """True if the sentence reads as natural language rather than a file/code fragment.

    A bulleted, numbered, tagged, or quoted statement still counts (markers stripped first),
    and a sentence that merely cites a filename/path still counts (the path is discounted, not
    fatal). Only a line that is structurally a dump, or mostly path/symbols, is rejected.
    """
    s = _strip_markers(sent)
    if not s or _STRUCT.search(s):
        return False
    core = _PATH_TOKEN.sub(" ", s)                 # discount path/filename citations
    letters = sum(c.isalpha() for c in core)
    if letters < max(1, len(core)) * 0.45:         # what's left is mostly path/symbols -> not prose
        return False
    words = re.findall(r"[A-Za-z]{2,}|[가-힣]{2,}", core)
    min_words = 2 if _HANGUL.search(core) else 4   # Korean decisions are terser than English
    return len(words) >= min_words


def _subject_key(statement: str) -> str:
    words = re.findall(r"[\w가-힣]+", statement.lower())
    sig = [w for w in words if w not in _STOP and len(w) > 1][:4]
    return "-".join(sig) if sig else "general"


def extract_events(store: Store, project_id: str, branch_id: str, checkpoint_id: str | None,
                   events: list[dict]) -> dict:
    """Stage candidate atoms from a list of raw_event dicts. Returns a summary."""
    staged = []
    for ev in events:
        et = ev.get("event_type", "")
        if et not in _PROSE_EVENTS:          # skip attachment/last-prompt/mode/queue-operation
            continue
        tier = _SPEAKER_TIER.get(et, "model_inferred")
        text = ev.get("content_text", "")
        for sent in _SENT_SPLIT.split(text):
            sent = norm_ws(sent)
            if len(sent) < 8 or len(sent) > 400:
                continue
            if not _is_prose(sent):           # drop file-dump / code / markup fragments
                continue
            for atom_type, pat in _PATTERNS:
                if pat.search(sent):
                    conf = 0.85 if tier == "user_explicit" else 0.55
                    res = ops.stage_atom(
                        store, project_id, branch_id, checkpoint_id,
                        atom_type=atom_type, statement=sent, subject_key=_subject_key(sent),
                        authority_tier=tier, source_raw_event_id=ev.get("id"),
                        source_quote=sent, confidence_score=conf, created_by="extractor",
                    )
                    if res:
                        staged.append({"atom_id": res[0], "type": atom_type, "statement": sent})
                    break  # one atom per sentence
    return {"staged": len(staged), "atoms": staged}
