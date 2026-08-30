"""The governed read for an extraction prompt.

Doctrine puts prompts in the map, not in the code: "a customer editing a prompt
or threshold in the shell is editing governed knowledge in the map, not
application code — which is what makes it safe." A prompt held as a Python
string literal is the opposite of that — invisible to the corpus, uneditable
without a deploy, and absent from the read-set of the ModelCall it steers. So
each extraction prompt is a `kind: prompt` asset, and the flow reads it at run
time exactly the way it already reads its thresholds: at the revision it got,
with `knowledge:{id}@rev{n}` stamped onto the step, so a decision stays
explainable against the wording that actually produced it.

An asset carries more than the instruction — a prompt a customer is expected to
edit has to say what it is for and what happens to the answer, or handing it
over is not really handing it over. The first fenced block is the part the model
sees; a page with no fence is all instruction, which is the reading that never
silently substitutes something else for what was written.

The built-in default is a fallback for a corpus that has not seeded yet, not for
a bad edit: a fresh deployment must still extract. An edit, however unwise, is
the customer's and runs as written — with the revision in the journal saying so.

One function rather than one per flow: the two extraction flows want the same
read, and all that differs is which asset and which default — both of which are
the flow's own and stay in the flow's own module.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel

log = logging.getLogger("cumulus.prompts")

# The first fenced block, whatever it is labelled.
_FENCE = re.compile(r"^```[a-z]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


class GovernedPrompt(BaseModel):
    """The instruction a ModelCall ran on, and where it came from.

    Shaped like `PolicyValues`: the parsed content plus the ref it was read at,
    so the step that consumes it can put the ref in its read-set without a
    second lookup, and `found_asset` can say in the journal which of the two
    things a run actually ran on.
    """

    text: str = ""
    source_ref: str = ""
    found_asset: bool = True


def read_prompt(knowledge, asset_id: str, default: str) -> GovernedPrompt:
    """Read one prompt asset. Degrades to `default` when it is not there.

    The unpinned `knowledge:{id}` on the missing branch is deliberate and the
    same thing `read_policy` does: the run still names the prompt it wanted, and
    the absent `@rev` is how the journal says it never got one.
    """
    asset = knowledge.repo.get(asset_id)
    if asset is None:
        log.warning(
            "Prompt asset missing; using the built-in default",
            extra={"assetId": asset_id},
        )
        return GovernedPrompt(
            text=default, source_ref=f"knowledge:{asset_id}", found_asset=False
        )
    match = _FENCE.search(asset.content)
    return GovernedPrompt(
        text=(match.group(1) if match else asset.content).strip(),
        source_ref=f"knowledge:{asset_id}@rev{getattr(asset, 'revision', 1)}",
    )
