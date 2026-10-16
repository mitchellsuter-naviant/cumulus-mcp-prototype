"""Retrieval indexes over assets: BM25 lexical, cosine semantic, and the rank
fusion that combines them.

BM25 is held as an inverted index (term -> {doc_id: term_frequency}), so scoring
a query touches only the documents that actually contain a query term rather
than the whole corpus. Documents are added and removed individually, so a single
write no longer costs a full reindex.

The semantic side is a brute-force cosine scan. That is the right call at this
size — an approximate-nearest-neighbour index only starts paying for itself in
the hundreds of thousands, and it would cost recall and a rebuild step here.
"""

import math
import re
import threading

import numpy as np

from .models import Asset

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Dropped from queries, never from the index.
#
# These carry no topic signal but do match almost everything, which is
# actively harmful under rank fusion: a document matching only "what/to/when"
# still earns a lexical rank, and RRF rewards appearing in both rankings. That
# lets four documents with noise-level BM25 scores outrank the one document
# semantic search identified as the answer. Removing them at query time costs
# nothing — IDF already discounts them for ranking — and stops them
# manufacturing lexical evidence that isn't there.
#
# Bare quantifiers — "much", "many", "most", "lot" — are here for the same
# reason and one more: they are what makes a short question look uncovered.
# "How much PTO do I have" is two words after the classic stoplist, one of
# which no policy is about, so the PTO policy covered half the question and got
# reported as maybe-not-the-answer. A word that cannot be the topic must not be
# counted as part of it.
#
# The index keeps every token, so this changes what is asked, not what is
# stored, and needs no reindex to adjust.
_STOPWORDS = frozenset(
    """
    a about all am an and any are as at be been being but by can cannot could
    did do does doing for from get gets getting had has have having he her him
    his how i if in into is it its lot lots me most much many my no nor not
    of off on once only or
    other our out over own same she should so some such than that the their
    them then there these they this those through to too under until up us was
    we were what when where which while who whom why will with would you your
    """.split()
)


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


#: A run of digits followed by letters, or the reverse — "401k", "10k", "w2".
#: The index splits on non-alphanumerics only, so "401(k)" is stored as "401"
#: and "k" while a person typing "401k" produces one token that matches
#: neither. Splitting the mixed token at query time makes the two spellings the
#: same question. Query-side like the stopword list, and for the same reason:
#: it changes what is asked, not what is stored, so it needs no reindex.
_MIXED_RE = re.compile(r"(\d+)([a-z]+)|([a-z]+)(\d+)")


def _expand_mixed(term: str) -> list[str]:
    """A mixed alphanumeric token, plus the parts of it worth searching for.

    A numeric part needs three digits to come along. "401k" splits usefully
    because "401" is the topic; "10k" does not, because "10" appears in every
    document that mentions ten of anything and would report a PTO policy as
    half an answer to a question about invoices over ten thousand dollars.
    """
    match = _MIXED_RE.fullmatch(term)
    if match is None:
        return [term]
    parts = [
        part
        for part in match.groups()
        if part and len(part) >= (3 if part.isdigit() else 2)
    ]
    return [term, *parts]


def query_term_groups(query: str) -> list[list[str]]:
    """A query as the *things it asks about*, each with its spellings.

    One group per word the person typed; a mixed token brings its parts along
    as alternatives inside the same group. The grouping is what coverage needs
    and ranking does not: "401k" and "401" are two ways to say one thing, so a
    document holding either has covered that part of the question once, and
    flattening them first would charge a document for missing a spelling of a
    word it does have.
    """
    groups = [_expand_mixed(token) for token in tokenize(query)]
    meaningful = [g for g in groups if g[0] not in _STOPWORDS]
    return meaningful or groups


def query_terms(query: str) -> list[str]:
    """Tokenize a query, dropping stopwords when anything else remains.

    The fallback matters: a query that is *only* stopwords ("how do I") should
    still search for them rather than silently match nothing.
    """
    return [term for group in query_term_groups(query) for term in group]


#: Coverage matches on a prefix this long when an exact token match fails, so
#: "reimbursed" covers a document that says "reimbursement" and "flight" covers
#: one that says "flights". Five characters is long enough that "kombucha" and
#: "compliance" still share nothing, and this never touches the index or the
#: BM25 score — a looser rule for *did this document address the word* than for
#: *how well*, which is the right asymmetry: coverage is a recall test.
_STEM_CHARS = 5


def _stem(term: str) -> str:
    return term[:_STEM_CHARS]


def _searchable_text(asset: Asset) -> str:
    """Title/path/tags repeated so they outrank body matches."""
    boosted = " ".join(
        [asset.title] * 3 + [asset.path.replace("/", " ")] * 2 + asset.tags * 2
    )
    return boosted + " " + asset.content


class SearchIndex:
    K1 = 1.5
    B = 0.75

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # term -> {doc_id: term frequency in that doc}
        self._postings: dict[str, dict[str, int]] = {}
        self._doc_len: dict[str, int] = {}
        self._total_len: int = 0

    # ---- Mutation ---------------------------------------------------------

    def rebuild(self, assets: list[Asset]) -> None:
        with self._lock:
            self._postings = {}
            self._doc_len = {}
            self._total_len = 0
            for asset in assets:
                self._add_locked(asset)

    def upsert(self, asset: Asset) -> None:
        """Add or replace a single document."""
        with self._lock:
            self._remove_locked(asset.id)
            self._add_locked(asset)

    def remove(self, doc_id: str) -> None:
        with self._lock:
            self._remove_locked(doc_id)

    def _add_locked(self, asset: Asset) -> None:
        tokens = tokenize(_searchable_text(asset))
        freqs: dict[str, int] = {}
        for token in tokens:
            freqs[token] = freqs.get(token, 0) + 1
        for term, tf in freqs.items():
            self._postings.setdefault(term, {})[asset.id] = tf
        self._doc_len[asset.id] = len(tokens)
        self._total_len += len(tokens)

    def _remove_locked(self, doc_id: str) -> None:
        length = self._doc_len.pop(doc_id, None)
        if length is None:
            return
        self._total_len -= length
        # Drop this doc from every postings list it appears in.
        for term in list(self._postings):
            docs = self._postings[term]
            if docs.pop(doc_id, None) is not None and not docs:
                del self._postings[term]

    # ---- Query ------------------------------------------------------------

    def search(self, query: str, limit: int | None = None) -> list[tuple[str, float]]:
        """Ranked (doc_id, score) pairs. `limit=None` returns every match.

        Callers that post-filter results (e.g. dropping unpublished documents)
        must pass `limit=None` and truncate afterwards — truncating here would
        discard matches that the filter would have kept.
        """
        terms = query_terms(query)
        with self._lock:
            n = len(self._doc_len)
            if not n or not terms:
                return []
            avgdl = (self._total_len / n) or 1.0
            scores: dict[str, float] = {}
            for term in terms:
                docs = self._postings.get(term)
                if not docs:
                    # A word no document contains *exactly* falls back to its
                    # stem, so "reimbursed" reaches the guideline that says
                    # "reimbursement" and "flight" the one that says "flights".
                    # Deliberately narrow: the fallback fires only where the
                    # term was scoring nothing for anybody, so it can add
                    # matches and never reshuffle ones that already matched.
                    docs, _ = self._stem_match_locked(term, "")
                if not docs:
                    continue
                df = len(docs)
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                for doc_id, tf in docs.items():
                    dl = self._doc_len[doc_id]
                    denom = tf + self.K1 * (1 - self.B + self.B * dl / avgdl)
                    scores[doc_id] = (
                        scores.get(doc_id, 0.0) + idf * (tf * (self.K1 + 1)) / denom
                    )
            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            return ranked if limit is None else ranked[:limit]


    def coverage(self, query: str, doc_id: str) -> float:
        """The share of a question's *information* one document contains."""
        return self.coverages(query, [doc_id]).get(doc_id, 0.0)

    def coverages(self, query: str, doc_ids: list[str]) -> dict[str, float]:
        """Coverage for a whole candidate set, resolving each word once.

        Not the share of a question's *words*. "How much PTO do I have" is one
        topic word after stopwording, so a memo that merely says "much" covers
        half of it by count — and a floor built on that number would let the
        memo through while it answered nothing. Weighting each word by its IDF
        says what a reader means: the document has "pto", or it does not.

        A word no document contains is counted at the corpus's highest observed
        IDF and never as covered, which is what makes a query of invented words
        score near zero everywhere instead of picking a winner. Capped at an
        observed value rather than the unbounded df=0 figure, so one unfamiliar
        proper noun cannot sink an otherwise well-covered question.

        Batched because the stem fallback scans the term dictionary: resolving
        it once per word rather than once per (word, document) is the
        difference between a scan and fifty of them.
        """
        groups = query_term_groups(query)
        with self._lock:
            n = len(self._doc_len)
            if not n or not groups:
                return {doc_id: 0.0 for doc_id in doc_ids}
            # df=1 is the rarest a word can be and still be in the corpus, so
            # it is the ceiling an absent word is charged at.
            unknown_idf = math.log(1 + (n - 1 + 0.5) / 1.5)

            # Per group: the weight it contributes, and the documents that
            # cover it under any of its spellings.
            resolved: list[tuple[float, set[str]]] = []
            for group in groups:
                best_idf = 0.0
                holders: set[str] = set()
                for term in group:
                    exact = self._postings.get(term)
                    if exact:
                        df = len(exact)
                        best_idf = max(
                            best_idf, math.log(1 + (n - df + 0.5) / (df + 0.5))
                        )
                        holders.update(exact)
                    # The stem always contributes here, even when the exact
                    # word exists somewhere. Coverage asks whether *this*
                    # document addressed the word, and the travel guideline
                    # saying "reimbursement" has addressed "reimbursed"
                    # whether or not some other document happens to spell it
                    # the way the question did. Ranking is where the exact
                    # match earns its extra weight; this is a recall test, and
                    # being generous is the correct bias.
                    stemmed, _ = self._stem_match_locked(term, "")
                    if stemmed:
                        holders.update(stemmed)
                        if not exact:
                            df = len(stemmed)
                            best_idf = max(
                                best_idf,
                                math.log(1 + (n - df + 0.5) / (df + 0.5)),
                            )
                resolved.append((best_idf or unknown_idf, holders))

            total = sum(weight for weight, _ in resolved)
            if not total:
                return {doc_id: 0.0 for doc_id in doc_ids}
            out: dict[str, float] = {}
            for doc_id in doc_ids:
                if doc_id not in self._doc_len:
                    out[doc_id] = 0.0
                    continue
                covered = sum(
                    weight for weight, holders in resolved if doc_id in holders
                )
                out[doc_id] = round(covered / total, 4)
            return out

    def _stem_match_locked(self, term: str, doc_id: str):
        """Postings for any indexed word sharing this one's stem, and whether
        `doc_id` is among them.

        A scan of the term dictionary, which is the right cost at this corpus
        size and the wrong one at a hundred thousand documents — a stem->terms
        map built alongside the postings is the change to make if it ever
        matters. Only reached for a word no document contains exactly, and now
        once per word rather than once per candidate.
        """
        if len(term) < _STEM_CHARS:
            return None, False
        stem = _stem(term)
        merged: dict[str, int] = {}
        for other, docs in self._postings.items():
            if other.startswith(stem):
                merged.update(docs)
        if not merged:
            return None, False
        return merged, doc_id in merged


class VectorIndex:
    """Cosine similarity over unit-length document vectors.

    Vectors arrive already L2-normalized (see `embeddings.l2_normalize`), so
    cosine similarity is a plain dot product and the whole query is one
    matrix-vector multiply.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._vectors: dict[str, np.ndarray] = {}
        # Stacked form of `_vectors`, rebuilt on demand. Writes invalidate it
        # rather than restacking, so a burst of upserts costs one rebuild.
        self._matrix: np.ndarray | None = None
        self._ids: list[str] = []

    def __len__(self) -> int:
        with self._lock:
            return len(self._vectors)

    def doc_ids(self) -> set[str]:
        with self._lock:
            return set(self._vectors)

    # ---- Mutation ---------------------------------------------------------

    def upsert(self, doc_id: str, vector: list[float]) -> None:
        with self._lock:
            self._vectors[doc_id] = np.asarray(vector, dtype=np.float32)
            self._matrix = None

    def upsert_many(self, items: list[tuple[str, list[float]]]) -> None:
        with self._lock:
            for doc_id, vector in items:
                self._vectors[doc_id] = np.asarray(vector, dtype=np.float32)
            self._matrix = None

    def remove(self, doc_id: str) -> None:
        with self._lock:
            if self._vectors.pop(doc_id, None) is not None:
                self._matrix = None

    def retain(self, doc_ids: set[str]) -> int:
        """Drop vectors for assets that no longer exist. Returns the count."""
        with self._lock:
            stale = [d for d in self._vectors if d not in doc_ids]
            for doc_id in stale:
                del self._vectors[doc_id]
            if stale:
                self._matrix = None
            return len(stale)

    # ---- Query ------------------------------------------------------------

    def search(
        self,
        vector: list[float],
        limit: int | None = None,
        allowed: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Ranked (doc_id, cosine) pairs, best first.

        `allowed` restricts scoring to a set of eligible ids. Unlike BM25,
        which only ever returns documents containing a query term, cosine
        assigns every document a score — so filtering has to happen before the
        cut, or a `limit` of 10 could be filled entirely with drafts and hide
        the published documents underneath them.
        """
        with self._lock:
            if not self._vectors:
                return []
            if self._matrix is None:
                self._ids = list(self._vectors)
                self._matrix = np.stack([self._vectors[d] for d in self._ids])
            matrix, ids = self._matrix, self._ids
            query = np.asarray(vector, dtype=np.float32)
            if query.shape[0] != matrix.shape[1]:
                # A dimension change means the encoder changed under us; the
                # backfill will replace these vectors, so skip semantic scoring
                # rather than raising into the caller's search.
                return []
            scores = matrix @ query
            eligible = len(ids)
            if allowed is not None:
                mask = np.fromiter(
                    (d in allowed for d in ids), dtype=bool, count=len(ids)
                )
                eligible = int(mask.sum())
                if not eligible:
                    return []
                scores = np.where(mask, scores, -np.inf)
            take = eligible if limit is None else min(limit, eligible)
            # argpartition beats a full sort once the corpus is large and we
            # only want the head of the ranking.
            if take < len(ids):
                top = np.argpartition(-scores, take - 1)[:take]
                top = top[np.argsort(-scores[top])]
            else:
                top = np.argsort(-scores)
            return [(ids[i], float(scores[i])) for i in top]


def reciprocal_rank_fusion(
    rankings: list[tuple[list[str], float]], k: int = 60
) -> dict[str, float]:
    """Combine ranked id lists into one score per id.

    RRF is used instead of adding the raw scores because BM25 and cosine are
    not on a comparable scale: BM25 is unbounded and moves with corpus
    statistics, cosine sits in [-1, 1]. Normalizing them per query would make
    the weighting depend on the spread of whatever happened to be retrieved,
    which is unstable when one list is short. Ranks sidestep that entirely.

    `k` damps the top of each list; at the conventional 60, rank 1 contributes
    only ~1.6x what rank 10 does, so one retriever cannot dominate on its own.
    A document missing from a list simply contributes nothing for it, which is
    what makes partially-embedded corpora degrade smoothly.
    """
    scores: dict[str, float] = {}
    for ids, weight in rankings:
        if weight <= 0:
            continue
        for rank, doc_id in enumerate(ids, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)
    return scores


def make_snippet(content: str, query: str, width: int = 240) -> str:
    """Return a window of content around the first query-term match."""
    # Stopwords excluded for the same reason as in search: centring the excerpt
    # on the first "the" in the document tells the reader nothing.
    terms = query_terms(query)
    lower = content.lower()
    pos = -1
    for term in terms:
        pos = lower.find(term)
        if pos >= 0:
            break
    if pos < 0:
        pos = 0
    start = max(0, pos - width // 4)
    end = min(len(content), start + width)
    snippet = content[start:end].strip().replace("\n", " ")
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet = snippet + "…"
    return snippet


# ---- Signal vs noise in a ranking -------------------------------------------
#
# A dense retriever assigns every document a score, so "documents this query
# retrieved" is the entire corpus. That is fine for ranking and fatal for
# anything that counts: recording an impression per returned hit would give
# every document an impression on every search, and the metric would measure
# nothing but total traffic.
#
# Absolute cosine cannot separate them either. Measured against this corpus
# with gemini-embedding-2, unrelated documents score 0.48-0.58 for any query,
# and nonsense ("zzzz quarterly kombucha allowance") topped out at 0.545 —
# higher than genuine second-place hits on real queries. What separates signal
# from noise is how far the best result leads the pack:
#
#   vpn                                    top 0.756  background 0.574  +0.182
#   connecting to the office from home     top 0.704  background 0.595  +0.109
#   what happens when a service goes down  top 0.638  background 0.548  +0.089
#   how do I get money back for a flight   top 0.648  background 0.567  +0.081
#   zzzz quarterly kombucha allowance      top 0.545  background 0.508  +0.037
#
# This lived in the Flutter client first. It belongs here: the console, the MCP
# tools and the usage counters were otherwise each free to draw the line
# somewhere different, and a rule this fiddly should exist once.

# Below this lead nothing in the ranking stands out, so the whole semantic tail
# is nearest-neighbour noise.
MIN_SEMANTIC_LEAD = 0.06
# A hit must reach this far up the lead to count as signal.
SEMANTIC_RETAIN_FRACTION = 0.5


def semantic_profile(similarities: list[float]) -> tuple[float, float] | None:
    """(background, lead) for a ranking, or None if there is no pack to lead."""
    scored = sorted(s for s in similarities if s is not None)
    if len(scored) < 3:
        return None
    middle = len(scored) // 2
    background = (
        scored[middle]
        if len(scored) % 2
        else (scored[middle - 1] + scored[middle]) / 2
    )
    return background, scored[-1] - background


def is_weak_hit(
    retrieval: str, similarity: float | None, profile: tuple[float, float] | None
) -> bool:
    """Whether a hit is nearest-neighbour noise rather than a real match.

    A hit BM25 also found is never weak: a word the caller actually typed
    appears in it, which is corroboration no cosine should override.
    """
    if retrieval != "semantic" or similarity is None or profile is None:
        return False
    background, lead = profile
    if lead < MIN_SEMANTIC_LEAD:
        return True
    return similarity < background + lead * SEMANTIC_RETAIN_FRACTION


def semantic_strength(
    similarity: float | None, profile: tuple[float, float] | None
) -> float:
    """How far up the ranking's lead this hit sits, in [0, 1].

    The semantic half of grounding, and the reason it has to exist: `coverage`
    is a lexical measure, so on a hybrid deployment a document the embedder
    found and the words missed scores zero — "how do I get money back for a
    flight" against a guideline that says *reimbursement* and *airfare*. A
    grounding floor built on coverage alone would drop exactly the hits
    embeddings were added to catch, which would make hybrid retrieval worse
    than lexical rather than better.

    Measured against the pack rather than as an absolute cosine, for the reason
    written at length above `MIN_SEMANTIC_LEAD`: unrelated documents score
    0.48-0.58 against anything, and what separates signal from noise is how far
    the best result leads. A ranking with no lead at all has no strength to
    report and returns zero everywhere, which is what nonsense should score.
    """
    if similarity is None or profile is None:
        return 0.0
    background, lead = profile
    if lead < MIN_SEMANTIC_LEAD:
        return 0.0
    return round(max(0.0, min(1.0, (similarity - background) / lead)), 4)


def mark_weak_hits(hits: list, profile: tuple[float, float] | None = None) -> list:
    """Stamp `weak` and `semantic_strength` on each hit.

    `profile` is the pack the hits are judged against, and passing the *whole
    candidate set's* profile is what makes the numbers mean anything. Derived
    from the page instead — which is what this did — a `limit=3` request
    profiles three similarities that are by construction the three closest
    together in the corpus, the lead collapses under `MIN_SEMANTIC_LEAD`, and
    every hit is reported as having no lead at all. Observed on the deployed
    corpus: a squarely-answering guideline at cosine 0.65, three rows from a
    background of 0.55, scored a semantic strength of zero.

    Falls back to profiling the page when no profile is supplied, so a caller
    holding hits and nothing else still gets a judgement rather than an error.
    """
    if profile is None:
        profile = semantic_profile([h.similarity for h in hits])
    for hit in hits:
        hit.weak = is_weak_hit(hit.retrieval, hit.similarity, profile)
        hit.semantic_strength = semantic_strength(hit.similarity, profile)
    return hits
