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

# A leading list / quote / blockquote / tag marker is NOT evidence of a file dump — the user
# writes real decisions as markdown bullets, numbered items, blockquotes, and quoted lines.
# Strip them (bounded) before judging (else we drop the user's own decisions — review r3/r4).
_LEAD_MARKER = re.compile(r"^\s*(?:[-*+•]|>+|\d+[.)\]:])\s+")   # bullet / blockquote / "1." / "2)"
_LEAD_TAG = re.compile(r"^\s*\[[a-z_]+\]\s+")                  # "[decision] ", "[constraint] "
_QUOTES = "\"'`“”‘’«»"

# Structural lines that genuinely are file / markup, not prose (checked AFTER marker-strip).
# cat -n output is "<number>\t<content>" or wide-aligned; require a TAB or 2+ spaces so genuine
# prose that merely starts with a number ("3 lanes are enough.") is not misread as a dump (r4).
_STRUCT = re.compile(
    r"^\s*[`#|]"            # md heading / fenced code / table row
    r"|^\s*[\[{]"           # json / array fragment
    r"|^\s*\d+(?:\t| {2,})\S"   # cat -n line-number dump (tab/wide-aligned)
)
# A path/filename CITATION inside prose is fine ("CLAUDE.md never names a client domain.") —
# discount the path token, then judge what remains; only reject when the line is mostly path.
# Branches are non-backtracking (segment runs hold no '.', extension anchored by lookahead) — r4.
_PATH_TOKEN = re.compile(
    r"https?://\S+"
    r"|(?<![\w.])~?(?:[\w-]+/)+[\w./-]*"                          # rooted/relative path with a slash
    r"|(?<![\w.])(?:[\w-]+/)*[\w-]+\.(?:md|py|ts|tsx|json|sql|sh|ya?ml)(?![\w.])"   # filename.ext
    r"|\b[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*){2,}\b"             # dotted ident a.b.c (config/JSON path)
)
_HANGUL = re.compile(r"[가-힣]")

# Basin's OWN output and artifacts must never be re-ingested as atoms (cold-continuity finding):
# `basin status` rows, pack/CANON YAML keys, engine source, atom/rev/checkpoint id refs, and the
# generic continuity questions (which otherwise loop back in as bogus open_question atoms).
_SELF_OUTPUT = re.compile(
    r"^\s*[●○]"
    r"|^\s*(?:statement|authority|confidence|atom|subject_key|atom_id|revision_no|atom_type|branch_id|lifecycle_status):\s"
    r"|\bre\.compile\(|\bby_type\(|\bdef \w+\(|^\s*(?:from|import)\s+\w"
    r"|\b(?:at|rev|ck|cp|e|s|sl|dnl)_[0-9a-f]{8,}\b"
)
_CONTINUITY_LEAK = re.compile(
    r"what is a commit in this system"
    r"|why is a summary alone insufficient"
    r"|name one rejected path you must not"
    r"|state one open question that is still"
    r"|name one binding constraint that limits", re.I)


def _strip_markers(sent: str) -> str:
    s = _LEAD_TAG.sub("", sent.strip(), count=1)
    for _ in range(3):                       # bounded: e.g. blockquote + bullet ("> - item")
        ns = _LEAD_MARKER.sub("", s, count=1)
        if ns == s:
            break
        s = ns
    return s.strip().strip(_QUOTES).strip()


def _is_prose(sent: str) -> bool:
    """True if the sentence reads as natural language rather than a file/code fragment.

    A bulleted, numbered, tagged, or quoted statement still counts (markers stripped first),
    and a sentence that merely cites a filename/path still counts (the path is discounted, not
    fatal). Only a line that is structurally a dump, or mostly path/symbols, is rejected.
    """
    if _SELF_OUTPUT.search(sent) or _CONTINUITY_LEAK.search(sent):
        return False                               # Basin's own output/artifacts are not atoms
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
        for raw in _SENT_SPLIT.split(text):
            sent = norm_ws(raw)
            if len(sent) < 8 or len(sent) > 400:
                continue
            if not _is_prose(raw):            # judge the RAW form: norm_ws collapses the cat -n TAB
                continue                       # that distinguishes a dump from bare-number prose
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
